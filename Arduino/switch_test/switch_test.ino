/*
  Phase 5：微动开关测试
  ====================
  接线：
    开关 C  → ESP32 GP3
    开关 NO → ESP32 GND
    开关 NC → 不接（悬空）
  （注意：用 GP3 而不是 GP0，GP0 是 strapping pin，上电被拉低会进 bootloader）

  工作原理：
    - GP3 内部上拉到 3.3V（INPUT_PULLUP）
    - 未按下：开关断开 → GP3 = HIGH
    - 按下：开关闭通 → GP3 通过开关接 GND → GP3 = LOW
  检测 HIGH → LOW 的下降沿 = 按了一下。

  打开串口监视器（115200）：
    - 每按一下，串口打印 "PRESSED (#N)"
    - 长按 > 800ms 打印 "LONG PRESS"
    - 同时板载 LED 短暂闪烁作为视觉反馈
*/

#define BTN_PIN 3
#define LED_PIN 8           // 板载 LED（可能是 8 或 10，看 Phase 1 验证）

const unsigned long DEBOUNCE_MS    = 30;   // 去抖间隔
const unsigned long LONG_PRESS_MS  = 800;  // 长按阈值

int  lastStableState   = HIGH;
int  lastReadState     = HIGH;
unsigned long lastChangeMs = 0;
unsigned long pressStartMs = 0;
int  pressCount        = 0;

void flashLed(int times, int intervalMs) {
  for (int i = 0; i < times; i++) {
    digitalWrite(LED_PIN, HIGH);
    delay(intervalMs);
    digitalWrite(LED_PIN, LOW);
    delay(intervalMs);
  }
}

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("=== Switch test start ===");
  Serial.println("按一下微动开关试试...");

  pinMode(BTN_PIN, INPUT_PULLUP);
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);
}

void loop() {
  int reading = digitalRead(BTN_PIN);

  // 状态变化时记录时间戳（去抖起点）
  if (reading != lastReadState) {
    lastChangeMs  = millis();
    lastReadState = reading;
  }

  // 状态稳定超过 DEBOUNCE_MS 才认为是真变化
  if ((millis() - lastChangeMs) > DEBOUNCE_MS && reading != lastStableState) {
    lastStableState = reading;

    if (lastStableState == LOW) {
      // 下降沿：按下了
      pressStartMs = millis();
      pressCount++;
      Serial.printf("[%lu ms] PRESSED (#%d)\n", millis(), pressCount);
      digitalWrite(LED_PIN, HIGH);
    } else {
      // 上升沿：松开了
      unsigned long heldMs = millis() - pressStartMs;
      digitalWrite(LED_PIN, LOW);
      if (heldMs >= LONG_PRESS_MS) {
        Serial.printf("[%lu ms] RELEASED (LONG PRESS, %lums)\n", millis(), heldMs);
        flashLed(3, 80);
      } else {
        Serial.printf("[%lu ms] RELEASED (%lums)\n", millis(), heldMs);
      }
    }
  }
}
