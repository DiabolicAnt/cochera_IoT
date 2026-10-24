// Prueba del servo en la ESP32-C3, sin sensores.
// Misma senal en GPIO4 y GPIO1: conectar el naranja a cualquiera de los dos.
// Mueve 15 -> 90 -> 180 -> 90 -> 15 grados, 3 s en cada posicion, sin fin.
#include <Arduino.h>
#include <ESP32Servo.h>

constexpr uint8_t PIN_A = 4, PIN_B = 1;
constexpr int POSICIONES[] = {15, 90, 180, 90};
Servo servoA, servoB;

void setup() {
  Serial.begin(115200);
#if ARDUINO_USB_CDC_ON_BOOT
  Serial.setTxTimeoutMs(0);
#endif
  delay(300);
  servoA.setPeriodHertz(50); servoB.setPeriodHertz(50);
  const int a = servoA.attach(PIN_A, 500, 2400);
  const int b = servoB.attach(PIN_B, 500, 2400);
  Serial.printf("\n=== PRUEBA SERVO C3 === GPIO%u canal=%d | GPIO%u canal=%d\n", PIN_A, a, PIN_B, b);
}

void loop() {
  for (int g : POSICIONES) {
    servoA.write(g); servoB.write(g);
    Serial.printf("Servo a %d grados (GPIO4 y GPIO1)\n", g);
    delay(3000);
  }
}
