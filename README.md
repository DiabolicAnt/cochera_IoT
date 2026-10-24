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

## Contenido

| Carpeta | Qué hay |
|---|---|
| `firmware/SmartGate_ESP32_Cochera` | Firmware del nodo sensor y control de la puerta |
| `firmware/pruebas/SmartGate_ESP32_Diagnostico` | Escaneo I2C y lectura cruda del ultrasónico |
| `firmware/pruebas/SmartGate_C3_PruebaServo` | Prueba aislada del servo |
| `docs/semana1` | Reporte de la semana 1 y evidencias (fotos y monitor serie) |

## Compilar y cargar

Arduino IDE o arduino-cli con **esp32 by Espressif 3.3.x**, placa **ESP32 Dev Module**.
Bibliotecas: **VL53L0X de Pololu 1.3.1** y **ArduinoJson 7.4.3**.

Monitor serie a **115200** con **Nueva línea**. Comandos: `estado`, `abrir`, `cerrar`, `auto`, `manual`, `gNN` (mover el servo a NN grados para calibrar).
