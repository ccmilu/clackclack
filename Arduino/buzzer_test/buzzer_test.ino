/*
  Phase 3：蜂鸣器测试（带音量控制 + 防发热 + 防卡死）
  =====================================================
  接线：
    蜂鸣器 VCC → 面包板 5V 总线
    蜂鸣器 GND → 面包板 GND 总线
    蜂鸣器 I/O → ESP32 GP4
  调节 VOLUME（0-100）：100=最大 / 20=推荐 / 0=静音
  重听按板子 RESET 键。

  ⚠️ 低电平触发的 3 线蜂鸣器模块的两个坑：
    1. I/O 持续 LOW → 模块内三极管常导通 → 发热
    2. 仅靠 ledcWrite(255) 不一定能让引脚恒高，LEDC 频率还在时可能继续输出方波 → 一直叫
  正解：播音之间 ledcDetach + digitalWrite(HIGH)，下次播音重新 ledcAttach。
*/

#define BUZZER_PIN 4
#define VOLUME     10      // 0-100，建议 10-50

// ---------- 音符频率 ----------
const int NOTE_C4 = 262;
const int NOTE_D4 = 294;
const int NOTE_E4 = 330;
const int NOTE_F4 = 349;
const int NOTE_G4 = 392;
const int NOTE_A4 = 440;
const int NOTE_B4 = 494;
const int NOTE_C5 = 523;
const int NOTE_E5 = 659;
const int NOTE_G5 = 784;

const int MAX_DUTY = 127;  // LEDC 8 位分辨率，50% = 127 = 最大音量

// 停音 = detach LEDC + 把引脚拉 HIGH。
// 必须 detach 才能让 digitalWrite 接管引脚，否则 LEDC 还在控制输出。
void stopBuzzer() {
  ledcDetach(BUZZER_PIN);
  pinMode(BUZZER_PIN, OUTPUT);
  digitalWrite(BUZZER_PIN, HIGH);
}

// 播一个音 dur 毫秒，再静音 gap 毫秒
void playNote(int freq, int dur, int gap = 30) {
  if (freq > 0 && VOLUME > 0) {
    // 每次播音重新 attach（freq 作为初始频率，duty 设音量）
    ledcAttach(BUZZER_PIN, freq, 8);
    int duty = (MAX_DUTY * VOLUME) / 100;
    ledcWrite(BUZZER_PIN, duty);
    delay(dur);
  } else {
    delay(dur);
  }
  stopBuzzer();   // 立刻断电
  delay(gap);
}

// 纯静默间隔
void silence(int ms) {
  stopBuzzer();
  delay(ms);
}

void playDoneMelody() {
  Serial.println("[1/3] Done melody");
  playNote(NOTE_C5, 120);
  playNote(NOTE_E5, 120);
  playNote(NOTE_G5, 240);
}

void playNotifyMelody() {
  Serial.println("[2/3] Notify melody");
  playNote(NOTE_A4, 100, 80);
  playNote(NOTE_A4, 100);
}

void playErrorMelody() {
  Serial.println("[3/3] Error melody");
  playNote(NOTE_E4, 150);
  playNote(NOTE_D4, 150);
  playNote(NOTE_C4, 300);
}

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.printf("=== Buzzer test start (VOLUME=%d) ===\n", VOLUME);

  // 上电立刻把引脚拉 HIGH（哪怕没 attach 过 LEDC 也安全）
  pinMode(BUZZER_PIN, OUTPUT);
  digitalWrite(BUZZER_PIN, HIGH);
  delay(500);

  playDoneMelody();
  silence(1000);

  playNotifyMelody();
  silence(1000);

  playErrorMelody();
  silence(500);

  // 长期 HIGH，不发热不响
  stopBuzzer();
  Serial.println("=== Done. Press RESET to replay. ===");
}

void loop() {
  // 空 loop
}
