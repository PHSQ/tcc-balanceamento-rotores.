/*
  Bancada Didatica - Leitura do ADXL345 (eixo Y apenas)
  Biblioteca Adafruit_ADXL345_U (I2C gerenciada pela lib).
  Envia: tempo_ms, accel_y em g.

  Conexoes:
    ADXL345 SDA -> Arduino A4
    ADXL345 SCL -> Arduino A5
    ADXL345 VCC -> 3.3V
    ADXL345 GND -> GND

  Por que so eixo Y (e nao modulo):
  Desbalanceamento gera vibracao radial (perpendicular ao eixo de rotacao).
  Se o eixo Y do sensor esta alinhado com uma direcao radial do mancal,
  o sinal 1x aparece DIRETO em Y, sem diluicao da gravidade ou de outros
  eixos (que mascarariam o sinal no calculo do modulo).

  ATENCAO - orientacao:
  - Y deve estar perpendicular ao eixo de rotacao (radial).
  - Se Y for VERTICAL: o sinal tem offset DC ~1g (gravidade) que e
    removido pela FFT (subtracao da media antes de janelar).
  - Se Y for HORIZONTAL radial: offset ~0g.
  - Se Y estiver AXIAL (paralelo ao eixo de rotacao): nao captura
    desbalanceamento — trocar montagem ou trocar pra outro eixo no codigo.
*/

#include <Wire.h>
#include <Adafruit_ADXL345_U.h>

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
  Serial.println("tempo_ms,accel_y_g");
  proximo_tempo = millis();
}

void loop() {
  unsigned long agora = millis();
  if (agora < proximo_tempo) return;

  sensors_event_t event;
  accel.getEvent(&event);

  // Sinal invertido: na orientacao desta bancada, Y aponta pra baixo
  // (gravidade -> -1g). Inverter deixa o leitura "natural" (~+1g em
  // repouso). Nao afeta a FFT, que ja remove a media antes do janelamento.
  float ay = -event.acceleration.y / G_MS2;

  Serial.print(agora);
  Serial.print(",");
  Serial.println(ay, 4);

  proximo_tempo += INTERVALO_MS;
}
