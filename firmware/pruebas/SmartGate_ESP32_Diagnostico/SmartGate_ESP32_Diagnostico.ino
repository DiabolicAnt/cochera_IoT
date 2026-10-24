// Diagnostico de cableado en la ESP32 grande: busca dispositivos I2C en 21/22
// (y con SDA/SCL invertidos) y mide el ultrasonico TRIG23/ECHO25 en crudo.
#include <Arduino.h>
#include <Wire.h>

constexpr uint8_t TRIG_PIN = 23, ECHO_PIN = 25;

void escanear(uint8_t sda, uint8_t scl) {
  Wire.end();
  Wire.begin(sda, scl); Wire.setClock(100000);
  Serial.printf("I2C SDA=%u SCL=%u -> lineas en reposo SDA=%d SCL=%d | encontrados:", sda, scl,
                digitalRead(sda), digitalRead(scl));
  int n = 0;
  for (uint8_t dir = 1; dir < 127; ++dir) {
    Wire.beginTransmission(dir);
    if (Wire.endTransmission() == 0) { Serial.printf(" 0x%02X", dir); ++n; }
  }
  Serial.println(n ? (n == 1 ? "  (0x29 = VL53L0X)" : "") : " NINGUNO");
}

void setup() {
  Serial.begin(115200);
  delay(300);
  Serial.println("\n=== DIAGNOSTICO ESP32 GRANDE ===");
  pinMode(TRIG_PIN, OUTPUT); digitalWrite(TRIG_PIN, LOW);
  pinMode(ECHO_PIN, INPUT);
}

void loop() {
  escanear(21, 22);
  escanear(22, 21);
  for (int i = 0; i < 3; ++i) {
    const int reposo = digitalRead(ECHO_PIN);
    digitalWrite(TRIG_PIN, HIGH); delayMicroseconds(10); digitalWrite(TRIG_PIN, LOW);
    const uint32_t us = pulseIn(ECHO_PIN, HIGH, 30000);
    Serial.printf("Ultrasonico: ECHO en reposo=%d | pulso=%lu us | %lu mm\n", reposo,
                  static_cast<unsigned long>(us), static_cast<unsigned long>(us * 343UL / 2000UL));
    delay(100);
  }
  delay(1500);
}
