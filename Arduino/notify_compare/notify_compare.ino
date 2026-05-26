/*
  N 紧急召唤候选对比 sketch（电磁铁 + 蜂鸣器同步）
  ===============================================
  烧上去后会依次播放 6 个候选搭配，每个之间间隔 2 秒。
  串口监视器（115200）同步显示当前在播哪个候选。
  听完告诉我编号 → 把那个搭配写进 claude_status.ino 的 N 状态。

  设计原则：每"一下"= 电磁铁通电同时蜂鸣器发音，时长相同 → 听觉触觉合拍

  接线（沿用主固件）：
    电磁铁 IRF540 SIG → GP2
    蜂鸣器 I/O        → GP4
*/

#define MAG_PIN     2
#define BUZZER_PIN  4

const int MAG_LEDC_FREQ = 1000;
const int BUZZER_VOLUME_DUTY = 8;   // 蜂鸣器 duty（数值小=轻），对应主固件 VOLUME=15 左右

// 一拍的完整描述：电磁铁通电（mag_duty 0-255） + 蜂鸣器同时发音（buzz_freq Hz）
// dur_ms 是两者共同持续时间；gap_ms 是这一拍结束后的静默时间。
struct Beat {
  uint8_t  mag_duty;
  uint16_t buzz_freq;   // 0 表示这拍不响
  uint16_t dur_ms;
  uint16_t gap_ms;
};

// ====== 6 个候选 ======

// A. 急促 5×20Hz（当前主固件的 N，最激烈）
const Beat A[] = {
  {255, 880, 25, 25},
  {255, 880, 25, 25},
  {255, 880, 25, 25},
  {255, 880, 25, 25},
  {255, 880, 25, 0},
};

// B. 中速 3×10Hz（缓和但仍清晰）
const Beat B[] = {
  {255, 880, 50, 50},
  {255, 880, 50, 50},
  {255, 880, 50, 0},
};

// C. 双拍清晰（两声大"叮叮"）
const Beat C[] = {
  {255, 988, 100, 80},
  {255, 988, 100, 0},
};

// D. 单猛拍 + 长尾（"咔！叮——"一下大响）
const Beat D[] = {
  {255, 700, 200, 0},
};

// E. 三连蹦 do-mi-sol（节奏感强，类似 D 的强化）
const Beat E[] = {
  {255, 523, 100, 60},
  {255, 659, 100, 60},
  {255, 784, 100, 0},
};

// F. 三短 + 一长（"叮叮叮 咚——"）
const Beat F[] = {
  {255, 1000, 30, 60},
  {255, 1000, 30, 60},
  {255, 1000, 30, 80},
  {255, 600,  250, 0},
};

struct Candidate {
  const char* name;
  const Beat* beats;
  int count;
};

const Candidate CANDIDATES[] = {
  {"A. 急促 5x20Hz（当前 N）",          A, sizeof(A)/sizeof(Beat)},
  {"B. 中速 3x10Hz（缓和）",             B, sizeof(B)/sizeof(Beat)},
  {"C. 双拍叮叮（两声大响）",            C, sizeof(C)/sizeof(Beat)},
  {"D. 单猛拍 + 长尾（咔！叮~~）",       D, sizeof(D)/sizeof(Beat)},
  {"E. 三连蹦 do-mi-sol（节奏感）",      E, sizeof(E)/sizeof(Beat)},
  {"F. 三短一长（叮叮叮 咚~~）",         F, sizeof(F)/sizeof(Beat)},
};

// ====== 蜂鸣器关断（低电平触发模块需要 detach + HIGH） ======
void buzzerOff() {
  ledcDetach(BUZZER_PIN);
  pinMode(BUZZER_PIN, OUTPUT);
  digitalWrite(BUZZER_PIN, HIGH);
}

// ====== 电磁铁关断（detach + LOW） ======
void magnetOff() {
  ledcDetach(MAG_PIN);
  pinMode(MAG_PIN, OUTPUT);
  digitalWrite(MAG_PIN, LOW);
}

// ====== 同步播一拍：电磁铁和蜂鸣器同时开始，同时结束 ======
void playBeat(const Beat& b) {
  // 1) 同时开
  ledcAttach(MAG_PIN, MAG_LEDC_FREQ, 8);
  ledcWrite(MAG_PIN, b.mag_duty);

  if (b.buzz_freq > 0) {
    ledcAttach(BUZZER_PIN, b.buzz_freq, 8);
    ledcWrite(BUZZER_PIN, BUZZER_VOLUME_DUTY);
  }

  // 2) 共同持续 dur_ms
  delay(b.dur_ms);

  // 3) 同时关
  magnetOff();
  buzzerOff();

  // 4) 静默 gap_ms
  delay(b.gap_ms);
}

void playCandidate(const Candidate& c) {
  for (int i = 0; i < c.count; i++) {
    playBeat(c.beats[i]);
  }
}

void setup() {
  Serial.begin(115200);
  delay(500);
  magnetOff();
  buzzerOff();

  Serial.println("===== N 紧急召唤候选对比（电磁铁 + 蜂鸣器同步）=====");
  Serial.println("听完哪个喜欢，记编号告诉 Claude");
  Serial.println("按 RESET 重听");
  Serial.println("⚠️ 桌面放稳，推杆会动作！3 秒后开始...");
  delay(3000);

  int total = sizeof(CANDIDATES) / sizeof(Candidate);
  for (int i = 0; i < total; i++) {
    Serial.printf("[%d/%d] %s\n", i + 1, total, CANDIDATES[i].name);
    delay(1200);
    playCandidate(CANDIDATES[i]);
    delay(2000);
  }

  Serial.println();
  Serial.println("===== 全部播完，按 RESET 重听 =====");
  magnetOff();
  buzzerOff();
}

void loop() {
  // 空 loop
}
