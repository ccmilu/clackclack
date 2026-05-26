/*
  Claude 物理状态机 — 整合固件 v1
  ================================
  同时驱动 TFT 屏 + 电磁铁 + 蜂鸣器 + 微动开关，非阻塞调度。
  6 个状态（T/W/N/D/E/I）每个有自己的：
    - 屏幕动画（来自 crab_data.h）
    - 电磁铁节奏（PWM 脚本，循环播放）
    - 蜂鸣器音效（切换时播一次，不循环）

  串口协议（115200 8N1）：
    Mac → ESP32：单字符 T/W/N/D/E/I/i 切换状态（i 是小写 I）
    ESP32 → Mac：
      "READY"        — 上电完成
      "STATE x"      — 状态已切换到 x
      "PRESS"        — 按钮按下
      "RELEASE n"    — 按钮松开（持续 n 毫秒）
      "LONG"         — 长按（>800ms）
      "MUTE 1/0"     — 静音模式切换
      "LOG: ..."     — 日志（调试用）

  特殊操作：
    长按按钮 = 切换静音模式（电磁铁 + 蜂鸣器关闭，屏继续显示）
*/

#include <Adafruit_GFX.h>
#include <Adafruit_ST7735.h>
#include <SPI.h>
#include "crab_data.h"
#include "types.h"   // 自定义 struct/enum，必须 #include 而不能写在 .ino 里

// ============ 引脚 ============
#define TFT_CS    10
#define TFT_RST    8
#define TFT_DC     7
#define TFT_MOSI   5
#define TFT_SCLK   6
#define MAG_PIN    2
#define BTN_PIN    3
#define BUZZER_PIN 4

// ============ 参数 ============
constexpr int  SCREEN_W = 160;
constexpr int  SCREEN_H = 80;
constexpr int  CRAB_X = (SCREEN_W - FRAME_W) / 2;
constexpr int  CRAB_Y = (SCREEN_H - FRAME_H) / 2;

constexpr int  LEDC_RES  = 8;
constexpr int  MAG_LEDC_FREQ = 1000;
constexpr int  BUZZER_VOLUME = 10;     // 0-100，全局音量基线（每个音符的 vol 在此基础上再缩放）
constexpr int  BUZZER_MAX_DUTY = 50;   // 满力 duty（原 127 = 50% 占空比；改小 = 整体更轻、VOLUME 数值更敏感）

constexpr unsigned long BTN_DEBOUNCE_MS   = 30;
constexpr unsigned long BTN_LONG_PRESS_MS = 800;

// ============ TFT ============
Adafruit_ST7735 tft(&SPI, TFT_CS, TFT_DC, TFT_RST);

// ============ 电磁铁节奏脚本 ============
// 类型 MagStep / MagPattern 定义在 types.h
//
// 设计原则：电磁铁是"强提醒"信号，只在需要用户介入的状态触发（N/D/E）。
// T 思考 / W 写代码 / I 空闲 是 AI 自己干活，电磁铁完全静止，避免持续打扰。

// N 状态（紧急召唤）：5 次 20Hz 急促颤抖（用户实测 20Hz @ duty 255 能完整推出）。
// 间隔渐增 800ms → 1500ms → 3000ms → 8000ms，避免用户走开后持续烦躁，但不会完全消音。
const MagStep N_steps[] = {
  // 第 1 组（紧急）
  {25, 255}, {25, 0}, {25, 255}, {25, 0}, {25, 255}, {25, 0},
  {25, 255}, {25, 0}, {25, 255}, {800, 0},
  // 第 2 组（间隔变长）
  {25, 255}, {25, 0}, {25, 255}, {25, 0}, {25, 255}, {25, 0},
  {25, 255}, {25, 0}, {25, 255}, {1500, 0},
  // 第 3 组
  {25, 255}, {25, 0}, {25, 255}, {25, 0}, {25, 255}, {25, 0},
  {25, 255}, {25, 0}, {25, 255}, {3000, 0},
  // 第 4+ 组（稀疏模式，跑到这里循环回第 1 组前会等 8 秒）
  {25, 255}, {25, 0}, {25, 255}, {25, 0}, {25, 255}, {25, 0},
  {25, 255}, {25, 0}, {25, 255}, {8000, 0},
};

// D 状态（完成报告）：和马里奥金币音"叮~咚"完全同步（前短后长）。
//   第一下 100ms 通电 + 30ms 间隔  ←→ 蜂鸣器 B5 100ms + 30ms gap
//   第二下 300ms 通电               ←→ 蜂鸣器 E6 300ms 长尾
// switchState 同时调用 startMelody 和 startMagnetPattern，两者基本同步启动。
const MagStep D_steps[] = {
  {100, 255}, {30,    0},   // 第一下短促（推杆顶一下马上回）
  {300, 255}, {60000, 0},   // 第二下顶上去停 300ms 再放 + 长静止
};

// E 状态（报错警报）：猛弹一下然后长沉默
const MagStep E_steps[] = {
  {400, 255}, {3000, 0},
};

const MagPattern MAG_PATTERNS[] = {
  {nullptr, 0},  // T 思考：静止
  {nullptr, 0},  // W 写代码：静止
  {N_steps, sizeof(N_steps)/sizeof(MagStep)},
  {D_steps, sizeof(D_steps)/sizeof(MagStep)},
  {E_steps, sizeof(E_steps)/sizeof(MagStep)},
  {nullptr, 0},  // I 空闲：静止
};
const char MAG_IDS[] = "TWNDEI";

const MagPattern* getMagPattern(char id) {
  for (int i = 0; i < 6; i++) if (MAG_IDS[i] == id) return &MAG_PATTERNS[i];
  return nullptr;
}

// ============ 蜂鸣器音效 ============
// 类型 BuzzNote / BuzzMelody 定义在 types.h

// 每个音符多了 vol 字段（0-100，覆盖默认 BUZZER_VOLUME 的相对响度）
//   100 = 全局音量 100%
//    50 = 全局音量 50%
//    20 = 全局音量 20%（很轻）
const BuzzNote N_melody[] = {           // 通知：叮叮（紧急，两音同响度）
  {880, 80, 60, 100},
  {880, 80, 0,  100},
};
// D 完成：超级马里奥金币音（B5 → E6 高八度跳）— 短促清脆"叮~咚"
const BuzzNote D_melody[] = {
  {988,  100, 30, 100},  // B5
  {1319, 300, 0,  100},  // E6 长尾
};
// E 报错：mi re do 下行（沉重感）+ 渐强（让用户听到错误更重）
const BuzzNote E_melody[] = {
  {330, 120, 30, 60},
  {294, 120, 30, 80},
  {262, 240, 0,  100},
};
const BuzzNote PRESS_ding[] = {         // 按钮反馈：叮一声，中等响度
  {1200, 60, 0, 80},
};

const BuzzMelody BUZZ_MELODIES[] = {
  {nullptr, 0},                                                       // T 不响
  {nullptr, 0},                                                       // W 不响
  {N_melody, sizeof(N_melody)/sizeof(BuzzNote)},                      // N
  {D_melody, sizeof(D_melody)/sizeof(BuzzNote)},                      // D
  {E_melody, sizeof(E_melody)/sizeof(BuzzNote)},                      // E
  {nullptr, 0},                                                       // I 不响
};
const BuzzMelody PRESS_MELODY = {PRESS_ding, 1};

const BuzzMelody* getBuzzMelody(char id) {
  for (int i = 0; i < 6; i++) if (MAG_IDS[i] == id) return &BUZZ_MELODIES[i];
  return nullptr;
}

// ============ 运行时状态 ============
char current_state_id = 'I';
const CrabState* current_crab = nullptr;
// enum Segment 定义在 types.h
Segment current_segment = SEG_LOOP;
uint16_t frame_idx = 0;
unsigned long last_frame_swap = 0;
bool need_redraw = false;

const MagPattern* mag_pattern = nullptr;
uint16_t mag_step_idx = 0;
unsigned long mag_step_start = 0;

const BuzzMelody* buzz_melody = nullptr;
uint16_t buzz_note_idx = 0;
unsigned long buzz_note_start = 0;
bool buzz_in_gap = false;

int btn_last_stable = HIGH;
int btn_last_read   = HIGH;
unsigned long btn_last_change = 0;
unsigned long btn_press_start = 0;

bool muted = false;

// D 完成态超时：进入 D 后 D_AUTO_TO_I_MS 毫秒自动转 I。
// 这样每次 Claude 答完都能看到清晰的 D 反馈（蹦一下 + 上行音），
// 然后回到 I 静默态等下一轮，不会长时间停留在 D。
constexpr unsigned long D_AUTO_TO_I_MS = 6000;
unsigned long state_enter_ms = 0;

// ============ 工具函数 ============
uint16_t segCount(const CrabState* s, Segment seg) {
  if (!s) return 0;
  return (seg == SEG_INTRO) ? s->intro_count : s->loop_count;
}
const uint8_t* segData(const CrabState* s, Segment seg, uint16_t i) {
  return (seg == SEG_INTRO) ? s->intro_frames[i] : s->loop_frames[i];
}
uint16_t segSize(const CrabState* s, Segment seg, uint16_t i) {
  return (seg == SEG_INTRO) ? s->intro_sizes[i] : s->loop_sizes[i];
}

// ============ TFT 渲染 ============
void drawFrame(const CrabState* s, const uint8_t* rle, uint16_t sz, int ox, int oy) {
  tft.startWrite();
  tft.setAddrWindow(ox, oy, FRAME_W, FRAME_H);
  for (uint16_t i = 0; i < sz; i += 2) {
    uint8_t count = rle[i];
    uint8_t idx   = rle[i + 1];
    uint16_t c    = ((uint16_t)s->palette[idx*2] << 8) | s->palette[idx*2 + 1];
    tft.writeColor(c, count);
  }
  tft.endWrite();
}

void advanceFrame() {
  if (!current_crab) return;
  uint16_t total = segCount(current_crab, current_segment);
  if (total == 0) return;

  unsigned long now = millis();
  if (now - last_frame_swap < MS_PER_FRAME) return;

  frame_idx++;
  if (frame_idx >= total) {
    if (current_segment == SEG_INTRO && current_crab->loop_count > 0) {
      current_segment = SEG_LOOP;
      frame_idx = 0;
    } else {
      frame_idx = 0;
    }
  }
  last_frame_swap = now;
  need_redraw = true;
}

// ============ 蜂鸣器 ============
void buzzerOff() {
  ledcDetach(BUZZER_PIN);
  pinMode(BUZZER_PIN, OUTPUT);
  digitalWrite(BUZZER_PIN, HIGH);  // 低电平触发模块 → HIGH 关断
}

// vol 0-100：该音符的相对响度，在全局 BUZZER_VOLUME 基础上再缩放
void buzzerTone(int freq, uint8_t vol) {
  ledcAttach(BUZZER_PIN, freq, 8);
  // duty = MAX_DUTY × 全局音量% × 音符音量%
  int duty = (BUZZER_MAX_DUTY * BUZZER_VOLUME * vol) / 10000;
  if (duty < 1) duty = 1;   // 太低听不到反而影响节奏感
  ledcWrite(BUZZER_PIN, duty);
}

void startMelody(const BuzzMelody* m) {
  buzz_melody = m;
  buzz_note_idx = 0;
  buzz_in_gap = false;
  if (m && m->count > 0 && m->notes[0].freq > 0 && !muted) {
    buzzerTone(m->notes[0].freq, m->notes[0].vol);
  } else {
    buzzerOff();
  }
  buzz_note_start = millis();
}

void tickBuzzer() {
  if (muted) { buzzerOff(); buzz_melody = nullptr; return; }
  if (!buzz_melody || buzz_melody->count == 0) return;

  unsigned long now = millis();
  const BuzzNote& n = buzz_melody->notes[buzz_note_idx];

  if (!buzz_in_gap) {
    if (now - buzz_note_start >= n.duration_ms) {
      buzzerOff();
      buzz_in_gap = true;
      buzz_note_start = now;
    }
  } else {
    if (now - buzz_note_start >= n.gap_ms) {
      buzz_note_idx++;
      if (buzz_note_idx >= buzz_melody->count) {
        buzz_melody = nullptr;  // 整段播完
        return;
      }
      const BuzzNote& nn = buzz_melody->notes[buzz_note_idx];
      if (nn.freq > 0) buzzerTone(nn.freq, nn.vol);
      buzz_in_gap = false;
      buzz_note_start = now;
    }
  }
}

// ============ 电磁铁 ============
// duty=0 时彻底断电（detach + LOW），避免 LEDC 在低 duty 时让线圈嗡嗡发热。
// duty>0 时确保 LEDC attach 上，再写 duty。
// 注：muted 不再影响电磁铁，只影响蜂鸣器（设计意图：静音仅消声音，电磁铁触觉反馈保留）
bool mag_ledc_attached = false;

void magnetOff() {
  if (mag_ledc_attached) {
    ledcDetach(MAG_PIN);
    mag_ledc_attached = false;
  }
  pinMode(MAG_PIN, OUTPUT);
  digitalWrite(MAG_PIN, LOW);
}

void magnetSet(uint8_t duty) {
  if (duty == 0) {
    magnetOff();
    return;
  }
  if (!mag_ledc_attached) {
    ledcAttach(MAG_PIN, MAG_LEDC_FREQ, LEDC_RES);
    mag_ledc_attached = true;
  }
  ledcWrite(MAG_PIN, duty);
}

void startMagnetPattern(char state_id) {
  mag_pattern = getMagPattern(state_id);
  mag_step_idx = 0;
  mag_step_start = millis();
  if (mag_pattern && mag_pattern->count > 0) {
    magnetSet(mag_pattern->steps[0].duty);
  } else {
    magnetSet(0);   // T / W / I：完全静止
  }
}

void tickMagnet() {
  if (!mag_pattern || mag_pattern->count == 0) return;

  unsigned long now = millis();
  const MagStep& step = mag_pattern->steps[mag_step_idx];
  if (now - mag_step_start >= step.duration_ms) {
    mag_step_idx = (mag_step_idx + 1) % mag_pattern->count;
    magnetSet(mag_pattern->steps[mag_step_idx].duty);
    mag_step_start = now;
  }
}

// ============ 状态切换 ============
void switchState(char c) {
  const CrabState* s = getStateByChar(c);
  if (!s) { Serial.printf("LOG: unknown state '%c'\n", c); return; }

  current_state_id = c;
  current_crab = s;
  current_segment = (s->intro_count > 0) ? SEG_INTRO : SEG_LOOP;
  frame_idx = 0;
  last_frame_swap = millis();
  need_redraw = true;

  startMagnetPattern(c);
  startMelody(getBuzzMelody(c));

  state_enter_ms = millis();
  Serial.printf("STATE %c\n", c);
}

// D 状态超时自动转 I。每次 loop 调用一次。
void tickStateTimeout() {
  if (current_state_id == 'D' && millis() - state_enter_ms >= D_AUTO_TO_I_MS) {
    Serial.println("LOG: D timeout -> I");
    switchState('I');
  }
}

// ============ 按钮 ============
void onPress() {
  Serial.println("PRESS");
  // 本地反馈：覆盖当前蜂鸣器播一声"叮"
  if (!muted) startMelody(&PRESS_MELODY);
}

void onRelease(unsigned long heldMs) {
  if (heldMs >= BTN_LONG_PRESS_MS) {
    Serial.println("LONG");
    muted = !muted;
    Serial.printf("MUTE %d\n", muted ? 1 : 0);
    if (muted) { buzzerOff(); buzz_melody = nullptr; }   // 只静蜂鸣器，电磁铁继续
  } else {
    Serial.printf("RELEASE %lu\n", heldMs);
  }
}

void tickButton() {
  int reading = digitalRead(BTN_PIN);
  if (reading != btn_last_read) {
    btn_last_change = millis();
    btn_last_read = reading;
  }
  if ((millis() - btn_last_change) > BTN_DEBOUNCE_MS && reading != btn_last_stable) {
    btn_last_stable = reading;
    if (btn_last_stable == LOW) {
      btn_press_start = millis();
      onPress();
    } else {
      onRelease(millis() - btn_press_start);
    }
  }
}

// ============ setup / loop ============
void setup() {
  Serial.begin(115200);
  delay(500);

  // SPI 重映射到接线引脚（GP6/-/GP5/GP10）
  SPI.begin(TFT_SCLK, -1, TFT_MOSI, TFT_CS);

  // TFT
  tft.initR(INITR_MINI160x80_PLUGIN);
  tft.setRotation(3);
  tft.invertDisplay(false);
  tft.setSPISpeed(27000000);
  tft.fillScreen(ST77XX_BLACK);

  // 电磁铁默认断电（按需 attach LEDC，平时 detach 避免低 duty 嗡嗡发热）
  magnetOff();

  // 蜂鸣器（断开 LEDC + HIGH 关断）
  buzzerOff();

  // 按钮
  pinMode(BTN_PIN, INPUT_PULLUP);

  Serial.println("READY");
  switchState('I');   // 默认进入空闲
}

void loop() {
  // 串口指令
  while (Serial.available()) {
    char c = Serial.read();
    if (c >= 'a' && c <= 'z') c -= 32;
    if (getStateByChar(c)) switchState(c);
  }

  // 推进各 player
  advanceFrame();
  tickMagnet();
  tickBuzzer();
  tickButton();
  tickStateTimeout();

  // 只在帧变化时绘制（节省 SPI 带宽，给其他任务留时间）
  if (need_redraw && current_crab) {
    uint16_t total = segCount(current_crab, current_segment);
    if (total > 0 && frame_idx < total) {
      drawFrame(current_crab,
                segData(current_crab, current_segment, frame_idx),
                segSize(current_crab, current_segment, frame_idx),
                CRAB_X, CRAB_Y);
    }
    need_redraw = false;
  }
}
