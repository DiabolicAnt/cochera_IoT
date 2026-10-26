/* SmartGate: ESP32-C3 como gateway de borde WiFi/MQTT.
 * Recibe por UART el JSON que genera la ESP32 grande (sensores, filtro y puerta),
 * agrega hora NTP y datos de red, y lo publica en smartgate/<node_id>/telemetry.
 * La ESP32 grande no usa radio porque se reinicia por brownout al encenderla.
 *
 * Conexion: GPIO17 (TX2) de la ESP32 grande -> GPIO20 (RX) de la C3, y GND con GND.
 * Placa: "ESP32C3 Dev Module", USB CDC On Boot: Enabled. Biblioteca: ArduinoJson 7.
 * Cliente MQTT: esp-mqtt de ESP-IDF (asincrono, admite QoS 0 y 1 con Last Will).
 */
#include <Arduino.h>
#include <WiFi.h>
#include <ArduinoJson.h>
#include <mqtt_client.h>
#include <esp_sntp.h>
#include <sys/time.h>
#include "SmartGate_Gateway_Config.h"

#if !defined(CONFIG_IDF_TARGET_ESP32C3)
#error "Este sketch es para la ESP32-C3. Selecciona ESP32C3 Dev Module."
#endif

constexpr uint8_t UART_RX_PIN = 20, UART_TX_PIN = 21;
constexpr size_t LINEA_MAX = 1200;
constexpr char NODE_ID[] = "cochera-01";  // Debe coincidir con el de la ESP32 grande.

// Experimentos de la practica 2: compilar con -DGW_QOS_EXPERIMENTO=0|1 y
// -DGW_TX_EXPERIMENTO=WIFI_POWER_2dBm para degradar el enlace sin mover la maqueta.
#ifdef GW_QOS_EXPERIMENTO
constexpr int QOS = GW_QOS_EXPERIMENTO;
#else
constexpr int QOS = GW_MQTT_QOS;
#endif
#ifdef GW_TX_EXPERIMENTO
constexpr wifi_power_t TX_POTENCIA = GW_TX_EXPERIMENTO;
#else
constexpr wifi_power_t TX_POTENCIA = WIFI_POWER_8_5dBm;
#endif

esp_mqtt_client_handle_t mqtt = nullptr;
volatile bool mqttConectado = false;
volatile uint32_t confirmados = 0;  // PUBACK recibidos (solo QoS 1).
char gatewayId[17], topicDatos[64], topicEstado[64], topicControl[64], ultimaVoluntad[128];
volatile uint32_t comandosReenviados = 0;
char linea[LINEA_MAX];
size_t largo = 0;
bool lineaDescartada = false, wifiAnunciada = false;
uint32_t recibidos = 0, publicados = 0, descartados = 0, ultimoPublishUs = 0, ultimaLineaMs = 0;
char salida[1600];

// Practica 3: comando del dashboard {"id":N,"accion":"abrir","valor":null} -> "CMD N abrir" por UART.
void reenviarComando(const char* datos, int largoDatos) {
  JsonDocument cmd;
  if (deserializeJson(cmd, datos, largoDatos) || !cmd["id"].is<uint32_t>() || !cmd["accion"].is<const char*>()) {
    Serial.println("Comando MQTT no valido, descartado."); return;
  }
  char lineaCmd[64];
  if (cmd["valor"].is<int>()) {
    snprintf(lineaCmd, sizeof(lineaCmd), "CMD %lu %s %d", (unsigned long)cmd["id"].as<uint32_t>(),
             cmd["accion"].as<const char*>(), cmd["valor"].as<int>());
  } else {
    snprintf(lineaCmd, sizeof(lineaCmd), "CMD %lu %s", (unsigned long)cmd["id"].as<uint32_t>(), cmd["accion"].as<const char*>());
  }
  Serial1.println(lineaCmd);
  comandosReenviados = comandosReenviados + 1;
  Serial.printf("CMD -> ESP32: %s\n", lineaCmd);
}

void eventoMqtt(void*, esp_event_base_t, int32_t id, void* datos) {
  const auto evento = static_cast<esp_mqtt_event_handle_t>(datos);
  if (id == MQTT_EVENT_CONNECTED) {
    mqttConectado = true;
    char online[128];
    snprintf(online, sizeof(online), "{\"node_id\":\"%s\",\"boot_id\":\"%s\",\"online\":true}", NODE_ID, gatewayId);
    esp_mqtt_client_publish(mqtt, topicEstado, online, 0, 1, 1);
    esp_mqtt_client_subscribe(mqtt, topicControl, 1);
    Serial.println("MQTT conectado; suscrito a control.");
  } else if (id == MQTT_EVENT_DATA) {
    // Un comando cabe en un solo fragmento; topic_len > 0 marca el inicio del mensaje.
    if (evento->topic_len == (int)strlen(topicControl) && strncmp(evento->topic, topicControl, evento->topic_len) == 0 &&
        evento->current_data_offset == 0 && evento->data_len == evento->total_data_len) {
      reenviarComando(evento->data, evento->data_len);
    }
  } else if (id == MQTT_EVENT_DISCONNECTED) {
    mqttConectado = false;
    Serial.println("MQTT desconectado; reintentando.");
  } else if (id == MQTT_EVENT_PUBLISHED) {
    confirmados = confirmados + 1;  // Solo esta tarea escribe el contador.
  }
}

void iniciarMqtt() {
  snprintf(topicDatos, sizeof(topicDatos), "smartgate/%s/telemetry", NODE_ID);
  snprintf(topicEstado, sizeof(topicEstado), "smartgate/%s/status", NODE_ID);
  snprintf(topicControl, sizeof(topicControl), "smartgate/%s/control", NODE_ID);
  snprintf(ultimaVoluntad, sizeof(ultimaVoluntad), "{\"node_id\":\"%s\",\"boot_id\":\"%s\",\"online\":false}", NODE_ID, gatewayId);
  static char clientId[48];
  snprintf(clientId, sizeof(clientId), "smartgate-gw-%s", gatewayId);
  esp_mqtt_client_config_t cfg = {};
  cfg.broker.address.uri = GW_MQTT_URI;
  cfg.credentials.client_id = clientId;
  cfg.session.last_will.topic = topicEstado;
  cfg.session.last_will.msg = ultimaVoluntad;
  cfg.session.last_will.qos = 1;
  cfg.session.last_will.retain = 1;
  cfg.session.keepalive = 15;
  cfg.network.reconnect_timeout_ms = 2000;
  mqtt = esp_mqtt_client_init(&cfg);
  esp_mqtt_client_register_event(mqtt, MQTT_EVENT_ANY, eventoMqtt, nullptr);
  esp_mqtt_client_start(mqtt);
}

void publicar() {
  JsonDocument doc;
  if (deserializeJson(doc, linea, largo) || doc["node_id"] != NODE_ID) {
    ++descartados; Serial.println("UART: linea no valida, descartada."); return;
  }
  ++recibidos;
  timeval reloj{}; gettimeofday(&reloj, nullptr);
  const bool sincronizado = reloj.tv_sec >= 1700000000;
  doc["timestamp"] = nullptr; doc["timestamp_ms"] = nullptr;
  if (sincronizado) {
    // t_envio de la practica 3: hora NTP en el momento de publicar (L = t_recepcion - t_envio).
    const uint64_t ahoraMs = uint64_t(reloj.tv_sec) * 1000 + reloj.tv_usec / 1000;
    doc["timestamp"] = ahoraMs; doc["timestamp_ms"] = ahoraMs;
  }
  doc["time_synced"] = sincronizado;
  JsonObject red = doc["network"].to<JsonObject>();
  red["wifi_connected"] = WiFi.status() == WL_CONNECTED;
  red["mqtt_connected"] = mqttConectado;
  red["heap_free"] = ESP.getFreeHeap();
  red["wifi_rssi_dbm"] = WiFi.status() == WL_CONNECTED ? WiFi.RSSI() : -127;
  red["qos"] = QOS;
  red["publish_us"] = ultimoPublishUs;  // Duracion del publish() anterior.
  const size_t n = serializeJson(doc, salida, sizeof(salida));
  if (!mqttConectado || n == 0 || n >= sizeof(salida)) { ++descartados; return; }
  const uint32_t t0 = micros();
  const int id = esp_mqtt_client_publish(mqtt, topicDatos, salida, n, QOS, 0);
  ultimoPublishUs = micros() - t0;
  if (id < 0) { ++descartados; return; }
  ++publicados;
  Serial.printf("PUB seq=%lu qos=%d publish=%lu us\n", (unsigned long)doc["sequence_id"].as<uint32_t>(),
                QOS, (unsigned long)ultimoPublishUs);
}

void leerUart() {
  while (Serial1.available()) {
    const char c = Serial1.read();
    if (c == '\n') {
      if (!lineaDescartada && largo) { ultimaLineaMs = millis(); publicar(); }
      largo = 0; lineaDescartada = false;
    } else if (c != '\r' && !lineaDescartada) {
      if (largo < LINEA_MAX - 1) linea[largo++] = c;
      else { lineaDescartada = true; ++descartados; }
    }
  }
}

void setup() {
  Serial.begin(115200);
#if ARDUINO_USB_CDC_ON_BOOT
  Serial.setTxTimeoutMs(0);
#endif
  Serial1.setRxBufferSize(4096);
  Serial1.begin(115200, SERIAL_8N1, UART_RX_PIN, UART_TX_PIN);
  snprintf(gatewayId, sizeof(gatewayId), "%08lx%08lx", (unsigned long)esp_random(), (unsigned long)esp_random());
  delay(300);
  Serial.printf("\n=== SMARTGATE GATEWAY C3 | QoS %d | TX %.1f dBm | %s ===\n", QOS, static_cast<int>(TX_POTENCIA) / 4.0f, GW_MQTT_URI);
  if (strcmp(GW_WIFI_SSID, "TU_RED_WIFI") == 0) {
    Serial.println("Configura SmartGate_Gateway_Config.local.h con tu WiFi y la IP del broker.");
  }
  WiFi.mode(WIFI_STA); WiFi.setAutoReconnect(true);
  WiFi.onEvent([](WiFiEvent_t, WiFiEventInfo_t info) {
    Serial.printf("WiFi desconectado, motivo %u\n", info.wifi_sta_disconnected.reason);
  }, ARDUINO_EVENT_WIFI_STA_DISCONNECTED);
  WiFi.begin(GW_WIFI_SSID, GW_WIFI_PASSWORD);
  // La antena de la C3 SuperMini no logra asociarse a potencia maxima; 8.5 dBm si conecta.
  WiFi.setTxPower(TX_POTENCIA);
  iniciarMqtt();
}

void loop() {
  // Tiempo de trabajo de cada vuelta del loop (sin el delay final): mide cuanto bloquea publish().
  static uint32_t loopMaxUs = 0;
  const uint32_t inicioUs = micros();
  leerUart();
  const uint32_t now = millis();
  if (WiFi.status() == WL_CONNECTED && !wifiAnunciada) {
    wifiAnunciada = true;
    // Resincronizar cada minuto: el reloj de la C3 se desvia ~100 ms por hora y t_envio debe ser fiable.
    sntp_set_sync_interval(60000);
    configTime(0, 0, "pool.ntp.org", "time.google.com");
    Serial.printf("WiFi conectado: IP %s, RSSI %d dBm\n", WiFi.localIP().toString().c_str(), WiFi.RSSI());
  } else if (WiFi.status() != WL_CONNECTED && wifiAnunciada) {
    wifiAnunciada = false; Serial.println("WiFi perdido; reconectando.");
  }
  static uint32_t ultimoResumen = 0;
  if (now - ultimoResumen >= 5000) {
    ultimoResumen = now;
    Serial.printf("RESUMEN uart=%lu pub=%lu ack=%lu desc=%lu cmd=%lu wifi=%s mqtt=%s rssi=%d loop_max=%lu us ultima_linea_hace=%lu ms\n",
                  (unsigned long)recibidos, (unsigned long)publicados, (unsigned long)confirmados,
                  (unsigned long)descartados, (unsigned long)comandosReenviados, WiFi.status() == WL_CONNECTED ? "SI" : "NO",
                  mqttConectado ? "SI" : "NO", WiFi.RSSI(), (unsigned long)loopMaxUs,
                  (unsigned long)(now - ultimaLineaMs));
    loopMaxUs = 0;
  }
  loopMaxUs = max(loopMaxUs, micros() - inicioUs);
  delay(1);
}
