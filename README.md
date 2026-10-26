# SmartGate IoT · Cochera automática

Maqueta de cochera con puerta de cartón que se abre sola cuando llega un carro y se cierra cuando el carro ya entró.

**CETI Colomos · Ingeniería en Desarrollo de Software · Internet de las Cosas · Grupo 7P**
Lizeth Alexandra Esquivies Torres (23310409) · Caleb Berumen Estrada (23310408)

## Cómo funciona

1. El **ultrasónico HC-SR04** vigila la entrada. Si un objeto queda a 220 mm o menos durante tres lecturas seguidas y no hay carro dentro, la puerta abre.
2. El **ToF VL53L0X** vigila el lugar de estacionamiento. Cuando ve el carro a 120 mm o menos, marca que ya entró.
3. Con el carro dentro y la entrada libre (300 mm o más durante 2 s), la puerta cierra. Si aparece algo en la entrada mientras cierra, vuelve a abrir.
4. El **reed switch** con imán confirma que la puerta quedó cerrada.
5. Con el carro estacionado, la puerta no se vuelve a abrir sola.

## Conexiones (ESP32 WROOM-32)

| Elemento | Pin |
|---|---|
| Servo SG90 (señal) | GPIO18 · alimentación de 5 V aparte, GND común |
| HC-SR04 TRIG / ECHO | GPIO23 / GPIO25 (ECHO con divisor de voltaje) |
| VL53L0X SDA / SCL | GPIO22 / GPIO21 (verificado con escaneo I2C, dirección 0x29) |
| Reed switch | GPIO27 y GND (INPUT_PULLUP: imán cerca = LOW) |

La ESP32 se reinicia (brownout) al encender su radio, por eso este firmware no usa BLE ni WiFi.

## Práctica 2: middleware MQTT + FastAPI + WebSockets + app móvil

```text
ESP32 (sensores, filtro, puerta) --UART GPIO17 -> GPIO20--> ESP32-C3 gateway (WiFi, NTP, MQTT QoS 0/1)
  --> Mosquitto --> FastAPI (validación, SQLite, /stats) --> WebSocket /ws/telemetria --> app Android
```

Como la ESP32 no puede encender su radio, una ESP32-C3 SuperMini publica por ella. Solo hacen falta dos cables: **TX2 (GPIO17) de la ESP32 → RX (GPIO20) de la C3** y **GND con GND**.

1. **Broker:** `mosquitto -c backend/mosquitto.windows.conf -v`
2. **Backend:** `pip install -r backend/requirements.txt` y luego `uvicorn main:app --app-dir backend --host 0.0.0.0 --port 8000`. Pruebas: `python -m unittest discover -s backend/tests`.
3. **Gateway:** copia `SmartGate_Gateway_Config.h` como `SmartGate_Gateway_Config.local.h` (Git lo ignora) y pon tu WiFi de 2.4 GHz y la IP de la laptop. Placa **ESP32C3 Dev Module** con **USB CDC On Boot: Enabled**.
4. **App:** abre `app_movil` en Android Studio o compila con `gradlew assembleDebug`. En la app escribe `IP_de_la_laptop:8000` y toca **Conectar**.
5. **Firewall de Windows:** permitir entrada TCP en los puertos 1883 y 8000 en la red local.

La app muestra el estado de la puerta, si hay coche dentro y las lecturas del ultrasónico y del ToF, y avisa con notificaciones: *Se está abriendo la cochera*, *La cochera está abierta* y *Se cerró la cochera*.

## Contenido

| Carpeta | Qué hay |
|---|---|
| `firmware/SmartGate_ESP32_Cochera` | Firmware del nodo sensor y control de la puerta |
| `firmware/SmartGate_C3_Gateway` | Gateway WiFi/MQTT (QoS configurable) |
| `firmware/pruebas/SmartGate_ESP32_Diagnostico` | Escaneo I2C y lectura cruda del ultrasónico |
| `firmware/pruebas/SmartGate_C3_PruebaServo` | Prueba aislada del servo |
| `backend` | FastAPI, modelos Pydantic, dashboard web, simulador, pruebas y configuración de Mosquitto |
| `app_movil` | App Android SmartGate (Kotlin + Jetpack Compose) |
| `docs/semana1` | Reporte de la semana 1 y evidencias (fotos y monitor serie) |
| `docs/practica2` | Reporte de la práctica 2 y evidencias (consolas, capturas, experimento de QoS) |

## Compilar y cargar

Arduino IDE o arduino-cli con **esp32 by Espressif 3.3.x**, placa **ESP32 Dev Module**.
Bibliotecas: **VL53L0X de Pololu 1.3.1** y **ArduinoJson 7.4.3**.

Monitor serie a **115200** con **Nueva línea**. Comandos: `estado`, `abrir`, `cerrar`, `auto`, `manual`, `gNN` (mover el servo a NN grados para calibrar).
