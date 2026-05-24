/*
  板载 LED 诊断 sketch
  =====================
  SuperMini 板载 LED 可能是：
    - 普通 LED 接 GPIO 8 或 10（用 digitalWrite 控制）
    - WS2812 RGB 灯珠接 GPIO 8 或 10（用 NeoPixel 库控制）
  这段代码 4 个阶段轮流试，每个阶段约 4 秒，看哪个阶段板载灯有反应。
  串口监视器（115200）可以看到当前在哪个阶段。
*/

#include <Adafruit_NeoPixel.h>

#define PIN_8  8
#define PIN_10 10

Adafruit_NeoPixel pix8(1, PIN_8, NEO_GRB + NEO_KHZ800);
Adafruit_NeoPixel pix10(1, PIN_10, NEO_GRB + NEO_KHZ800);

void setup() {
  Serial.begin(115200);
  pix8.begin();
  pix10.begin();
  pix8.setBrightness(60);
  pix10.setBrightness(60);
}

void blinkDigital(int pin, const char* name) {
  Serial.printf("[Stage] digitalWrite on GPIO %s\n", name);
  pinMode(pin, OUTPUT);
  for (int i = 0; i < 4; i++) {
    digitalWrite(pin, HIGH); delay(400);
    digitalWrite(pin, LOW);  delay(400);
  }
}

void blinkNeoPixel(Adafruit_NeoPixel& p, const char* name) {
  Serial.printf("[Stage] NeoPixel on GPIO %s (RGB cycle)\n", name);
  for (int i = 0; i < 3; i++) {
    p.setPixelColor(0, p.Color(255, 0, 0)); p.show(); delay(400);  // 红
    p.setPixelColor(0, p.Color(0, 255, 0)); p.show(); delay(400);  // 绿
    p.setPixelColor(0, p.Color(0, 0, 255)); p.show(); delay(400);  // 蓝
  }
  p.setPixelColor(0, 0); p.show();
}

void loop() {
  Serial.println("==== 一轮诊断开始 ====");
  blinkDigital(PIN_8,  "8");
  delay(500);
  blinkDigital(PIN_10, "10");
  delay(500);
  blinkNeoPixel(pix8,  "8");
  delay(500);
  blinkNeoPixel(pix10, "10");
  delay(1500);
}
