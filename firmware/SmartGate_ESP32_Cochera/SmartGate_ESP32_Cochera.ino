/* SmartGate en ESP32 clasica (WROOM-32): control de la cochera SIN BLE.
 * Ultrasonico exterior: un objeto en la entrada abre la puerta.
 * ToF interior: confirma que el carro entro; con la entrada libre, la puerta cierra.
 * Reed + iman: confirma que la puerta quedo cerrada.
 * Con el carro estacionado dentro, la puerta no se vuelve a abrir sola.
 *
 * Telemetria: JSON por UART2 (TX GPIO17) hacia la ESP32-C3 gateway, que lo publica
 * por MQTT. Esta placa no enciende BLE ni WiFi: su radio provoca brownout.
 *
 * Placa: "ESP32 Dev Module", esp32 by Espressif 3.3.x. Monitor serie 115200, "Nueva linea".
 * Bibliotecas: VL53L0X de Pololu (1.3.1), ArduinoJson (7.4.3).
 */
#include <Arduino.h>
#include <Wire.h>
#include <VL53L0X.h>
#include <ArduinoJson.h>
#include "SmartGate_CicloEntrada.h"  // VentanaEstable: condicion continua durante X ms.
#include "SmartGate_Edge.h"          // MediaMovil: promedio de 5 lecturas validas.

#if !defined(CONFIG_IDF_TARGET_ESP32)
#error "Este sketch es para la ESP32 clasica. Selecciona ESP32 Dev Module."
#endif

// Pines del cableado original de la ESP32 grande.
constexpr uint8_t SERVO_PIN = 18, TRIG_PIN = 23, ECHO_PIN = 25;
// El ToF quedo cableado con SDA/SCL cruzados (verificado con escaneo I2C).
constexpr uint8_t SDA_PIN = 22, SCL_PIN = 21, REED_PIN = 27;
constexpr int REED_CERRADO = LOW;  // Reed a GND + INPUT_PULLUP: iman cerca = LOW.

// Servo: misma escala y extremos que la version anterior. Ajustar si el servo nuevo
// golpea el tope: usar el comando gNN para probar angulos.
constexpr int SERVO_MIN_US = 500, SERVO_MAX_US = 2400;
// Cerrado a la mitad del recorrido: permite abrir hacia cualquier lado (~90 grados).
// ABIERTO_GRADOS se ajusta con gNN para que la hoja quede arriba sin forzar el tope.
constexpr int CERRADO_GRADOS = 90, ABIERTO_GRADOS = 180;
constexpr uint32_t PASO_MS = 20;  // 1 grado cada 20 ms: recorrido completo ~3.3 s.

// Distancias en mm.
constexpr uint16_t ULTRA_LLEGADA_MM = 220;  // Carro en la entrada: abrir.
constexpr uint16_t ULTRA_LIBRE_MM = 300;    // Entrada libre: se permite cerrar.
constexpr uint16_t TOF_CARRO_MM = 120;      // Carro estacionado dentro.

// Tiempos en ms.
constexpr uint32_t LIBRE_MS = 2000;          // Entrada libre continua antes de cerrar.
constexpr uint32_t ABIERTA_MIN_MS = 2000;    // Tiempo minimo abierta.
constexpr uint32_t ESPERA_CARRO_MS = 30000;  // Si el carro no entra, cerrar igual.
constexpr uint32_t REED_ESPERA_MS = 3000;    // Margen para que el reed confirme el cierre.
constexpr uint32_t ULTRA_PERIODO_MS = 70, ECO_LIMITE_US = 50000;
constexpr uint32_t ECO_SIN_OBJETO_US = 25000;  // Pulso mas largo (~4 m): nada en alcance.
constexpr uint16_t TOF_ALCANCE_MM = 2000;      // Mas lejos (8190/8191): interior vacio.

// Telemetria hacia el gateway C3: UART2 solo TX, mismo GND en ambas placas.
constexpr uint8_t TELEMETRIA_TX_PIN = 17;
constexpr uint32_t TELEMETRIA_MS = 500;
constexpr char NODE_ID[] = "cochera-01";

enum class Estado { CERRADA, ABRIENDO, ABIERTA, CERRANDO, ALERTA };
Estado estado = Estado::CERRANDO;
bool automatico = true;
bool carroLlego = false;  // El ToF vio el carro durante esta apertura.
bool rearmado = false;    // Tras cerrar, esperar entrada libre antes de otra apertura.
uint32_t abiertaDesdeMs = 0, abajoDesdeMs = 0, ultimoReporteMs = 0;
String linea;

// ---------- Servo ----------
bool servoActivo = false;
int gradosActual = CERRADO_GRADOS, gradosObjetivo = CERRADO_GRADOS;
uint32_t ultimoPasoMs = 0;

void servoEscribir(int grados) {
  if (!servoActivo) servoActivo = ledcAttach(SERVO_PIN, 50, 14);
  if (!servoActivo) { Serial.println("ERROR: no se pudo iniciar el PWM del servo"); return; }
  const uint32_t us = SERVO_MIN_US + uint32_t(grados) * (SERVO_MAX_US - SERVO_MIN_US) / 180;
  ledcWrite(SERVO_PIN, us * ((1UL << 14) - 1) / 20000);
}

// Avanza un grado hacia el objetivo. Devuelve true cuando ya llego.
bool moverServo(uint32_t now) {
  if (gradosActual == gradosObjetivo) return true;
  if (now - ultimoPasoMs < PASO_MS) return false;
  ultimoPasoMs = now;
  gradosActual += gradosActual < gradosObjetivo ? 1 : -1;
  servoEscribir(gradosActual);
  return gradosActual == gradosObjetivo;
}

// ---------- Reed ----------
bool reedCerrado = false, reedRaw = false;
uint32_t reedCambioMs = 0;

void leerReed(uint32_t now) {
  const bool raw = digitalRead(REED_PIN) == REED_CERRADO;
  if (raw != reedRaw) { reedRaw = raw; reedCambioMs = now; return; }
  if (raw != reedCerrado && now - reedCambioMs >= 40) {
    reedCerrado = raw;
    Serial.printf("Reed: puerta %s\n", reedCerrado ? "CERRADA" : "ABIERTA");
  }
}

// ---------- Ultrasonico exterior ----------
bool ultraOk = false;
uint16_t ultraMm = 0;
uint8_t ultraCerca = 0;  // Lecturas seguidas en la zona de llegada.
VentanaEstable exteriorLibre;
uint32_t ultimoUltraMs = 0;
MediaMovil mediaUltra;            // Solo telemetria; la logica usa la lectura cruda.
const char* errorUltra = "SIN_DATO";

void leerUltrasonico(uint32_t now) {
  if (now - ultimoUltraMs < ULTRA_PERIODO_MS) return;
  ultimoUltraMs = now;
  digitalWrite(TRIG_PIN, LOW); delayMicroseconds(2);
  digitalWrite(TRIG_PIN, HIGH); delayMicroseconds(10); digitalWrite(TRIG_PIN, LOW);
  uint32_t us = pulseIn(ECHO_PIN, HIGH, ECO_LIMITE_US);
  // Si se agoto la espera con ECHO todavia alto, el pulso es larguisimo: nada en alcance.
  if (us == 0 && digitalRead(ECHO_PIN) == HIGH) us = ECO_LIMITE_US;
  // Sin objeto en alcance el HC-SR04 deja ECHO alto ~38 ms: cuenta como entrada libre.
  // Sin pulso (us = 0) sigue siendo error de cableado.
  const uint32_t mm = us >= ECO_SIN_OBJETO_US ? 4000 : us * 343UL / 2000UL;
  // Una lectura suelta sin eco es comun en el HC-SR04: se ignora hasta 3 seguidas.
  static uint8_t fallosSeguidos = 0;
  if (us == 0 || mm < 20 || mm > 4000) {
    if (++fallosSeguidos < 3) return;
    ultraOk = false; ultraCerca = 0; exteriorLibre.actualizar(now, false);
    mediaUltra.limpiar(); errorUltra = "SIN_ECO"; return;
  }
  fallosSeguidos = 0; ultraOk = true;
  ultraMm = uint16_t(mm);
  if (us >= ECO_SIN_OBJETO_US) { mediaUltra.limpiar(); errorUltra = "SIN_OBJETO"; }
  else { mediaUltra.agregar(ultraMm); errorUltra = nullptr; }
  if (ultraMm <= ULTRA_LLEGADA_MM) { if (ultraCerca < 3) ++ultraCerca; }
  else ultraCerca = 0;
  exteriorLibre.actualizar(now, ultraMm >= ULTRA_LIBRE_MM);
}

// ---------- ToF interior ----------
VL53L0X tof;
bool tofListo = false, tofOk = false;
uint16_t tofMm = 0;
uint8_t tofCerca = 0;  // Lecturas seguidas con el carro cerca.
uint32_t ultimoTofMs = 0, ultimaTofValidaMs = 0, ultimoInitTofMs = 0;
MediaMovil mediaTof;
const char* errorTof = "NO_INICIADO";

void iniciarTof() {
  tof.setTimeout(50);
  tofListo = tof.init();
  if (tofListo) { tof.setMeasurementTimingBudget(33000); tof.startContinuous(50); }
  Serial.println(tofListo ? "ToF VL53L0X: OK" : "ToF VL53L0X: no responde (revisa SDA22/SCL21/3V3/GND)");
}

void tofInvalido(const char* motivo) { tofOk = false; tofCerca = 0; mediaTof.limpiar(); errorTof = motivo; }

void leerTof(uint32_t now) {
  if (!tofListo) {
    tofInvalido("NO_INICIADO");
    if (now - ultimoInitTofMs >= 3000) { ultimoInitTofMs = now; iniciarTof(); }
    return;
  }
  if (now - ultimoTofMs < 25) return;
  ultimoTofMs = now;
  // Leer solo cuando hay medicion lista, para no bloquear el loop.
  const uint8_t listo = tof.readReg(VL53L0X::RESULT_INTERRUPT_STATUS);
  if (tof.last_status != 0) { tofListo = false; tofInvalido("ERROR_I2C"); Serial.println("ToF: error I2C, reintentando"); return; }
  if ((listo & 0x07) == 0) {
    if (now - ultimaTofValidaMs > 500) tofInvalido("SIN_DATO");
    return;
  }
  const uint16_t mm = tof.readRangeContinuousMillimeters();
  if (tof.timeoutOccurred() || tof.last_status != 0) { tofInvalido("TIMEOUT"); return; }
  // 8190/8191 significa "nada dentro del alcance": el interior esta vacio.
  tofOk = true; tofMm = mm; ultimaTofValidaMs = now;
  if (mm > TOF_ALCANCE_MM) { mediaTof.limpiar(); errorTof = "SIN_OBJETO"; }
  else { mediaTof.agregar(mm); errorTof = nullptr; }
  if (mm <= TOF_CARRO_MM) { if (tofCerca < 3) ++tofCerca; }
  else tofCerca = 0;
}

bool carroDentro() { return tofOk && tofCerca >= 3; }

// ---------- Puerta ----------
const char* nombreEstado() {
  switch (estado) {
    case Estado::CERRADA: return "CERRADA";
    case Estado::ABRIENDO: return "ABRIENDO";
    case Estado::ABIERTA: return "ABIERTA";
    case Estado::CERRANDO: return "CERRANDO";
    default: return "ALERTA";
  }
}

void abrir(const char* motivo) {
  if (estado == Estado::ABRIENDO || estado == Estado::ABIERTA) return;
  // Una reapertura durante el cierre conserva si el carro ya habia entrado.
  if (estado != Estado::CERRANDO) carroLlego = false;
  estado = Estado::ABRIENDO; gradosObjetivo = ABIERTO_GRADOS; rearmado = false;
  servoEscribir(gradosActual);
  Serial.printf(">> ABRIENDO: %s\n", motivo);
}

void cerrar(const char* motivo) {
  if (estado == Estado::CERRANDO) return;
  if (estado == Estado::CERRADA && gradosActual == CERRADO_GRADOS) return;
  estado = Estado::CERRANDO; gradosObjetivo = CERRADO_GRADOS; abajoDesdeMs = 0;
  servoEscribir(gradosActual);
  Serial.printf(">> CERRANDO: %s\n", motivo);
}

void actualizarPuerta(uint32_t now) {
  if (carroDentro() && (estado == Estado::ABRIENDO || estado == Estado::ABIERTA)) {
    if (!carroLlego) Serial.println("ToF: carro dentro de la cochera.");
    carroLlego = true;
  }
  // Cerrada o sin confirmar: el servo esta en el extremo cerrado, se puede abrir.
  if (estado == Estado::CERRADA || estado == Estado::ALERTA) {
    if (!rearmado && exteriorLibre.cumplida(now, LIBRE_MS)) {
      rearmado = true; Serial.println("Entrada libre: listo para el siguiente carro.");
    }
    if (automatico && rearmado && ultraCerca >= 3 && tofOk && !carroDentro()) {
      abrir("carro en la entrada"); return;
    }
  }
  switch (estado) {
    case Estado::CERRADA:
      // El servo sigue sosteniendo la hoja para que el iman no se separe del reed.
      if (!reedCerrado) {
        estado = Estado::ALERTA; Serial.println("Aviso: el reed perdio el iman con la puerta cerrada.");
      }
      break;

    case Estado::ABRIENDO:
      if (moverServo(now)) {
        estado = Estado::ABIERTA; abiertaDesdeMs = now;
        Serial.println("ABIERTA.");
        if (reedCerrado) Serial.println("Aviso: el reed sigue en CERRADA; revisa el iman.");
      }
      break;

    case Estado::ABIERTA: {
      const uint32_t abierta = now - abiertaDesdeMs;
      if (automatico && abierta >= ABIERTA_MIN_MS && exteriorLibre.cumplida(now, LIBRE_MS)) {
        if (carroLlego) cerrar("carro dentro y entrada libre");
        else if (abierta >= ESPERA_CARRO_MS) cerrar("el carro no entro");
      }
      break;
    }

    case Estado::CERRANDO:
      if (ultraOk && ultraMm < ULTRA_LIBRE_MM) { abrir("obstaculo en la entrada al cerrar"); break; }
      if (moverServo(now)) {
        if (abajoDesdeMs == 0) abajoDesdeMs = now;
        if (reedCerrado) {
          estado = Estado::CERRADA; rearmado = false;
          Serial.println("CERRADA: el reed confirma el cierre.");
        } else if (now - abajoDesdeMs >= REED_ESPERA_MS) {
          estado = Estado::ALERTA;
          Serial.println("ALERTA: el servo cerro pero el reed no confirma. Revisa iman/puerta.");
        }
      }
      break;

    case Estado::ALERTA:
      // Cierre sin confirmar por el reed; la apertura automatica sigue permitida.
      // Tras una calibracion gNN el servo no esta en cerrado: esperar el comando cerrar.
      if (reedCerrado && gradosActual == CERRADO_GRADOS) {
        estado = Estado::CERRADA; rearmado = false;
        Serial.println("Reed confirma cierre: CERRADA.");
      }
      break;
  }
}

// ---------- Telemetria (UART2 -> gateway C3 -> MQTT) ----------
// El gateway agrega hora NTP y datos de red antes de publicar.
char bootId[17];
uint32_t secuencia = 0, ultimaTelemetriaMs = 0;

void lectura(JsonObject o, bool valida, uint16_t crudo, const MediaMovil& media, const char* error) {
  if (valida && media.cantidad()) {
    o["raw_mm"] = crudo; o["filtered_mm"] = roundf(media.promedio() * 10) / 10;
    o["valid"] = true; o["sample_count"] = media.cantidad(); o["error"] = nullptr;
  } else {
    o["raw_mm"] = nullptr; o["filtered_mm"] = nullptr;
    o["valid"] = false; o["sample_count"] = 0; o["error"] = error ? error : "SIN_DATO";
  }
}

void enviarTelemetria(uint32_t now) {
  if (now - ultimaTelemetriaMs < TELEMETRIA_MS) return;
  ultimaTelemetriaMs = now;
  JsonDocument doc;
  doc["schema_version"] = 1; doc["node_id"] = NODE_ID; doc["boot_id"] = bootId;
  doc["sequence_id"] = secuencia++; doc["uptime_ms"] = now;
  doc["source"] = "hardware"; doc["filter_window"] = 5;
  JsonObject sensores = doc["readings"].to<JsonObject>();
  lectura(sensores["exterior"].to<JsonObject>(), ultraOk, ultraMm, mediaUltra, errorUltra);
  lectura(sensores["interior"].to<JsonObject>(), tofOk, tofMm, mediaTof, errorTof);
  JsonObject puerta = doc["door"].to<JsonObject>();
  puerta["state"] = nombreEstado(); puerta["mode"] = automatico ? "AUTO" : "MANUAL";
  puerta["reed_closed"] = reedCerrado; puerta["servo_command_deg"] = gradosActual;
  JsonObject vehiculo = doc["vehicle"].to<JsonObject>();
  vehiculo["inside"] = carroDentro(); vehiculo["arrival"] = carroLlego;
  vehiculo["rearmed"] = rearmado; vehiculo["exterior_clear"] = exteriorLibre.cumplida(now, LIBRE_MS);
  serializeJson(doc, Serial2);
  Serial2.write('\n');
}

// ---------- Monitor serie ----------
void reporte() {
  Serial.printf("[%s|%s] reed=%s | ultra=", nombreEstado(), automatico ? "AUTO" : "MANUAL",
                reedCerrado ? "CERRADA" : "ABIERTA");
  if (ultraOk) Serial.printf("%u mm", ultraMm); else Serial.print("SIN_ECO");
  Serial.print(" | tof=");
  if (tofOk && tofMm >= 8190) Serial.print("vacio(fuera de alcance)");
  else if (tofOk) Serial.printf("%u mm", tofMm);
  else Serial.print(tofListo ? "SIN_DATO" : "NO_INICIADO");
  Serial.printf(" | carro_dentro=%s | rearmado=%s | servo=%d grados %s\n",
                carroDentro() ? "SI" : "NO", rearmado ? "SI" : "NO", gradosActual,
                servoActivo ? "ON" : "OFF");
}

void comando(String cmd) {
  cmd.trim(); cmd.toLowerCase();
  if (cmd == "estado") reporte();
  else if (cmd == "abrir") abrir("comando manual");
  else if (cmd == "cerrar") cerrar("comando manual");
  else if (cmd == "auto") { automatico = true; Serial.println("Modo AUTO."); }
  else if (cmd == "manual") { automatico = false; Serial.println("Modo MANUAL: solo abrir/cerrar por comando."); }
  else if (cmd.length() > 1 && cmd[0] == 'g') {
    const int g = cmd.substring(1).toInt();
    if (g < 0 || g > 180 || !isDigit(cmd[1])) { Serial.println("Formato: g0 .. g180"); return; }
    automatico = false; estado = Estado::ALERTA;
    gradosActual = gradosObjetivo = g; servoEscribir(g);
    Serial.printf("CALIBRACION: servo a %d grados (modo MANUAL). Escribe cerrar y luego auto para seguir.\n", g);
  } else if (cmd.length()) {
    Serial.println("Comandos: estado, abrir, cerrar, auto, manual, gNN (calibrar angulo)");
  }
}

void leerSerie() {
  while (Serial.available()) {
    const char c = Serial.read();
    if (c == '\n') { comando(linea); linea = ""; }
    else if (c != '\r' && linea.length() < 20) linea += c;
  }
}

void setup() {
  Serial.begin(115200);
#if ARDUINO_USB_CDC_ON_BOOT
  Serial.setTxTimeoutMs(0);  // Sin PC conectada, no frenar el programa al imprimir.
#endif
  // Buffer amplio: un JSON (~550 bytes) no bloquea el loop mientras sale por UART2.
  Serial2.setTxBufferSize(2048);
  Serial2.begin(115200, SERIAL_8N1, -1, TELEMETRIA_TX_PIN);
  snprintf(bootId, sizeof(bootId), "%08lx%08lx", (unsigned long)esp_random(), (unsigned long)esp_random());
  delay(300);
  Serial.println("\n=== SMARTGATE ESP32 GRANDE | sin BLE | telemetria UART2 -> C3 ===");
  pinMode(REED_PIN, INPUT_PULLUP);
  pinMode(TRIG_PIN, OUTPUT); digitalWrite(TRIG_PIN, LOW);
  pinMode(ECHO_PIN, INPUT);
  reedRaw = reedCerrado = digitalRead(REED_PIN) == REED_CERRADO;
  Wire.begin(SDA_PIN, SCL_PIN); Wire.setClock(100000);
  iniciarTof();
  // Arranque: llevar el servo a cerrado y esperar la confirmacion del reed.
  servoEscribir(CERRADO_GRADOS);
  Serial.printf("Abre si ultra <= %u mm; cierra con carro (ToF <= %u mm) y entrada >= %u mm por %lu ms.\n",
                ULTRA_LLEGADA_MM, TOF_CARRO_MM, ULTRA_LIBRE_MM, static_cast<unsigned long>(LIBRE_MS));
  Serial.println("Comandos: estado, abrir, cerrar, auto, manual, gNN");
}

void loop() {
  const uint32_t now = millis();
  leerReed(now);
  leerUltrasonico(now);
  leerTof(now);
  leerSerie();
  actualizarPuerta(now);
  enviarTelemetria(now);
  if (now - ultimoReporteMs >= 1000) { ultimoReporteMs = now; reporte(); }
  delay(1);
}
