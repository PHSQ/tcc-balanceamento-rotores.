/*
  Bancada Didatica - Leitura do ADXL345 (modulo |a|)
  Biblioteca Adafruit_ADXL345_U (I2C gerenciada pela lib).
  Envia: tempo_ms, |a| em g (raiz de x^2+y^2+z^2).

  Conexoes:
    ADXL345 SDA -> Arduino A4
    ADXL345 SCL -> Arduino A5
    ADXL345 VCC -> 3.3V
    ADXL345 GND -> GND

  Por que modulo: acelerometro mede 3 eixos; usando o modulo a leitura
  fica independente da orientacao do sensor (vibracao em qualquer direcao
  aparece na FFT). Baseline com bancada parada = ~1g (gravidade).
*/

#include <Wire.h>
#include <Adafruit_ADXL345_U.h>
#include <math.h>

Adafruit_ADXL345_Unified accel = Adafruit_ADXL345_Unified(12345);

const unsigned long INTERVALO_MS = 10;   // 100 Hz alvo
const float G_MS2 = 9.80665;             // fator m/s^2 -> g
unsigned long proximo_tempo = 0;

void setup() {
  Serial.begin(115200);
  delay(2000);
  Wire.begin();

  if (!accel.begin()) {
    Serial.println("ERRO: ADXL345 nao encontrado! Verifique conexoes.");
    while (1);
  }

  accel.setRange(ADXL345_RANGE_4_G);
  accel.setDataRate(ADXL345_DATARATE_100_HZ);

  delay(100);
  Serial.println("tempo_ms,accel_mag_g");
  proximo_tempo = millis();
}

void loop() {
  unsigned long agora = millis();
  if (agora < proximo_tempo) return;

  sensors_event_t event;
  accel.getEvent(&event);

  float ax = event.acceleration.x / G_MS2;
  float ay = event.acceleration.y / G_MS2;
  float az = event.acceleration.z / G_MS2;
  float mag = sqrt(ax * ax + ay * ay + az * az);

  Serial.print(agora);
  Serial.print(",");
  Serial.println(mag, 4);

  proximo_tempo += INTERVALO_MS;
}
