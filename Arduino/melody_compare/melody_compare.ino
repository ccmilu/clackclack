/*
  D 完成音 melody 候选对比 sketch（中国人熟悉版）
  =============================================
  烧上去后会依次播放 6 个中国人耳熟能详的旋律开头，每个之间间隔 2 秒。
  串口监视器（115200）同步显示当前在播哪个候选。
  听完告诉我你喜欢哪个编号 → 我换回 claude_status.ino 用那个版本。
  想重新听一遍按板子 RESET。

  接线：蜂鸣器 I/O → GP4，VCC → 3.3V，GND → GND（不变）
*/

#define BUZZER_PIN 4

const int DUTY = 6;

struct Note {
  uint16_t freq;
  uint16_t dur_ms;
  uint16_t gap_ms;
};

// ============ 6 个中国人熟悉的旋律候选 ============

// A. CCTV 新闻联播片头主题（"嗒嗒~ 嗒嗒~"）
//    G G B D5 G5 — 庄重经典，国内 100% 识别度
const Note A[] = {
  {392, 200, 40},   // G4 sol
  {392, 200, 40},   // G4 sol
  {494, 200, 40},   // B4 si
  {587, 200, 40},   // D5 re
  {784, 500, 0},    // G5 高 sol 长尾
};

// B. 两只老虎前 4 音（"两只老虎"）
//    do re mi do — 童谣最经典开头
const Note B[] = {
  {523, 220, 40},   // C5 do
  {587, 220, 40},   // D5 re
  {659, 220, 40},   // E5 mi
  {523, 500, 0},    // C5 do 长尾
};

// C. 天空之城主旋律开头（宫崎骏《天空之城》久石让）
//    mi fa mi re do — 5 音温柔下行
const Note C[] = {
  {659, 150, 30},   // E5 mi
  {698, 150, 30},   // F5 fa
  {659, 150, 30},   // E5 mi
  {587, 150, 30},   // D5 re
  {523, 450, 0},    // C5 do 长尾
};

// D. 西游记片头"登登登"（《敢问路在何方》前奏）
//    sol sol mi sol do↑ — 经典激昂上行
const Note D[] = {
  {784, 200, 30},   // G5 sol
  {784, 200, 30},   // G5 sol
  {659, 200, 30},   // E5 mi
  {784, 200, 30},   // G5 sol
  {1047, 450, 0},   // C6 do↑ 长尾
};

// E. 春节序曲开头（《春节序曲》李焕之）
//    sol sol do↑ si sol — 喜庆春节感
const Note E[] = {
  {784, 200, 40},   // G5 sol
  {784, 200, 40},   // G5 sol
  {1047, 200, 40},  // C6 do↑
  {988, 200, 40},   // B5 si
  {784, 500, 0},    // G5 sol 长尾
};

// F. 生日歌开头 4 音（"祝你 生日 快乐"）
//    sol sol la sol — 全球但中国也耳熟
const Note F[] = {
  {392, 180, 40},   // G4 sol
  {392, 180, 40},   // G4 sol
  {440, 220, 40},   // A4 la
  {392, 500, 0},    // G4 sol 长尾
};

struct Candidate {
  const char* name;
  const Note* notes;
  int count;
};

const Candidate CANDIDATES[] = {
  {"A. CCTV 新闻联播片头（庄重经典）",        A, sizeof(A)/sizeof(Note)},
  {"B. 两只老虎前 4 音（童谣 do-re-mi-do）",   B, sizeof(B)/sizeof(Note)},
  {"C. 天空之城开头（温柔下行 5 音）",         C, sizeof(C)/sizeof(Note)},
  {"D. 西游记片头·登登登（激昂上行）",         D, sizeof(D)/sizeof(Note)},
  {"E. 春节序曲开头（喜庆春节感）",            E, sizeof(E)/sizeof(Note)},
  {"F. 生日歌开头·祝你生日（祝福感）",         F, sizeof(F)/sizeof(Note)},
};

// ============ 蜂鸣器控制 ============

void buzzerOff() {
  ledcDetach(BUZZER_PIN);
  pinMode(BUZZER_PIN, OUTPUT);
  digitalWrite(BUZZER_PIN, HIGH);
}

void playNote(int freq, int dur_ms, int gap_ms) {
  if (freq > 0) {
    ledcAttach(BUZZER_PIN, freq, 8);
    ledcWrite(BUZZER_PIN, DUTY);
  }
  delay(dur_ms);
  buzzerOff();
  delay(gap_ms);
}

void playMelody(const Note* notes, int count) {
  for (int i = 0; i < count; i++) {
    playNote(notes[i].freq, notes[i].dur_ms, notes[i].gap_ms);
  }
}

void setup() {
  Serial.begin(115200);
  delay(500);
  buzzerOff();

  Serial.println("===== D melody 中国人熟悉版 =====");
  Serial.println("听完哪个喜欢，记编号告诉 Claude");
  Serial.println("按 RESET 重听");
  Serial.println();
  delay(1500);

  int total = sizeof(CANDIDATES) / sizeof(Candidate);
  for (int i = 0; i < total; i++) {
    Serial.printf("[%d/%d] %s\n", i + 1, total, CANDIDATES[i].name);
    delay(1200);
    playMelody(CANDIDATES[i].notes, CANDIDATES[i].count);
    delay(1500);
  }

  Serial.println();
  Serial.println("===== 全部播完，按 RESET 重听 =====");
  buzzerOff();
}

void loop() {
  // 空 loop
}
