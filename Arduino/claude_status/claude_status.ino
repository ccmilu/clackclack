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
    短按按钮 = 上报 PRESS 到串口（由 Mac 端 daemon 监听后用 CGEvent 模拟回车，
              详见 xiaoke-local-plugin/daemon/bridge.py）
  注：ESP32-C3 硬件是 USB-Serial-JTAG 专用外设，不支持 TinyUSB / HID，
     所以"按钮直接当键盘"的方案做不了，必须走 Mac daemon 模拟按键。
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

// N 状态（紧急召唤）：3 短脉冲 + 1 长脉冲 = "咔咔咔 咔——"，播一次即停。
// 时长与 N_melody 完全对齐（30ms 电磁铁 ←→ 30ms 短叮，250ms 电磁铁 ←→ 250ms 长咚）
// MAG_PATTERNS 里设 loop=false，整段 540ms 播完后电磁铁保持静止，不再循环骚扰用户。
const MagStep N_steps[] = {
  {30, 255}, {60, 0}, {30, 255}, {60, 0}, {30, 255}, {80, 0}, {250, 255},
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
  {nullptr, 0, false},  // T 思考：静止
  {nullptr, 0, false},  // W 写代码：静止
  {N_steps, sizeof(N_steps)/sizeof(MagStep), false},  // N：只蹦一次（与蜂鸣器对齐）
  {D_steps, sizeof(D_steps)/sizeof(MagStep), true},   // D：循环（实际有 6s 自动转 I 兜底，只播一轮）
  {E_steps, sizeof(E_steps)/sizeof(MagStep), true},   // E：报错持续提醒，每 3.4s 蹦一次
  {nullptr, 0, false},  // I 空闲：静止
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
// N 通知：3 短叮 + 1 长咚，与 N_steps 完全同步
// 时长与电磁铁严格对齐：30ms 响 + 60/80ms 间隔，最后 250ms 长尾
const BuzzNote N_melody[] = {
  {1000, 30,  60, 100},   // 短叮
  {1000, 30,  60, 100},   // 短叮
  {1000, 30,  80, 100},   // 短叮（间隔 80ms 配合电磁铁）
  {600,  250, 0,  100},   // 长咚（600Hz 中低频温和收尾）
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
bool btn_long_fired = false;     // 当次按下中，长按动作是否已触发（避免松开时重复触发）

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
// 静音图标占据的屏幕矩形（必须和 drawMuteIcon 的位置一致）
constexpr int ICON_BOX_X = SCREEN_W - 18;   // 142
constexpr int ICON_BOX_Y = SCREEN_H - 18;   // 62
constexpr int ICON_BOX_W = 16;
constexpr int ICON_BOX_H = 16;

// drawFrame 当 muted=true 时不写图标矩形那 16×16 像素，避免每帧"擦掉再重画"图标导致闪烁。
// 实现：按行解码 RLE 到 row buffer，再按行 setAddrWindow + writePixels；
// 图标所在行（cur_y 在 ICON_BOX_Y..ICON_BOX_Y+ICON_H-1）拆成左右两段写，中间空 16 像素。
void drawFrame(const CrabState* s, const uint8_t* rle, uint16_t sz, int ox, int oy) {
  static uint16_t row_buf[FRAME_W];

  const int RIGHT_X = ICON_BOX_X + ICON_BOX_W;       // 图标右边缘 = 158
  const int RIGHT_W = FRAME_W - RIGHT_X;             // 右段宽度 = 2

  uint16_t row_pos = 0;     // 当前行内填充位置
  uint16_t cur_y   = 0;
  uint16_t rle_i   = 0;
  uint8_t  count   = 0;
  uint8_t  idx     = 0;
  uint16_t c       = 0;

  tft.startWrite();

  while (cur_y < FRAME_H) {
    // 取下一个 RLE 段
    if (count == 0) {
      if (rle_i >= sz) break;
      count = rle[rle_i];
      idx   = rle[rle_i + 1];
      c     = ((uint16_t)s->palette[idx * 2] << 8) | s->palette[idx * 2 + 1];
      rle_i += 2;
    }

    // 填行 buffer
    uint16_t fit = (count < (FRAME_W - row_pos)) ? count : (FRAME_W - row_pos);
    for (uint16_t k = 0; k < fit; k++) row_buf[row_pos++] = c;
    count -= fit;

    // 行满 → 写出去
    if (row_pos >= FRAME_W) {
      bool is_icon_row = muted &&
                         (cur_y >= ICON_BOX_Y) &&
                         (cur_y <  ICON_BOX_Y + ICON_BOX_H);

      if (is_icon_row) {
        // 左段（0 ~ ICON_BOX_X - 1）
        tft.setAddrWindow(ox, oy + cur_y, ICON_BOX_X, 1);
        tft.writePixels(row_buf, ICON_BOX_X);
        // 右段（图标右侧到行尾）
        if (RIGHT_W > 0) {
          tft.setAddrWindow(ox + RIGHT_X, oy + cur_y, RIGHT_W, 1);
          tft.writePixels(row_buf + RIGHT_X, RIGHT_W);
        }
        // 中间 16 像素图标区不写 → 保留 drawMuteIcon 画好的图标
      } else {
        // 普通行：一次性整行写
        tft.setAddrWindow(ox, oy + cur_y, FRAME_W, 1);
        tft.writePixels(row_buf, FRAME_W);
      }

      row_pos = 0;
      cur_y++;
    }
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

// 16×16 静音图标位图（从一个喇叭被划掉的 SVG 渲染而来）。
// 转换流程：
//   cairosvg.svg2png(url=<source.svg>, output_width=16, output_height=16)
//   → PIL alpha > 100 二值化 → 1bpp 按行 MSB first
// 每行 2 bytes（16 像素），共 32 bytes。
const uint8_t MUTE_ICON_BMP[] PROGMEM = {
  0x00, 0x00,  0x60, 0x00,  0x71, 0x80,  0x3B, 0xC0,
  0x1D, 0xC0,  0x7E, 0xC4,  0x7F, 0x56,  0x7F, 0x9A,
  0x7F, 0xDA,  0x7F, 0xE2,  0x7F, 0xF6,  0x07, 0xF8,
  0x03, 0xDC,  0x01, 0x8E,  0x00, 0x06,  0x00, 0x00,
};

// 每个状态的背景色（与 SVG 设计中状态背景 fill 一致）
// 用作 drawMuteIcon 的 bit=0 像素，让图标看起来"无边框"透明融入状态背景
// 升级到 Material 100：比 50 号深一档但仍柔和，在 ST7735 屏上区分度更好
uint16_t getStateBgColor(char id) {
  switch (id) {
    case 'T': return 0xFF76;  // #FFECB3 Amber 100
    case 'W': return 0xBDDF;  // #BBDEFB Blue 100
    case 'N': return 0xFF16;  // #FFE0B2 Orange 100
    case 'D': return 0xCE79;  // #C8E6C9 Green 100
    case 'E': return 0xFE7A;  // #FFCDD2 Red 100
    case 'I': return 0xCEBB;  // #CFD8DC Blue Grey 100
    default:  return 0x0000;
  }
}

// 静音图标：右下角 16×16 像素喇叭 + 斜线"划掉"。
// drawFrame 已经会跳过图标区域（见 drawFrame 的 muted 分支），所以这里：
//   - 把"bit=1=图标灰色 + bit=0=当前状态背景色"一次性装进 buf[256]
//   - 用 setAddrWindow + writePixels 一次性发完，0.15ms 内完成
//   - 视觉上看不到边框，图标"无缝融入"状态色背景
void drawMuteIcon() {
  if (!current_crab) return;

  const uint16_t ICON_COLOR = 0xB5B6;   // RGB(180, 180, 180) 淡灰图标
  const uint16_t BG = getStateBgColor(current_crab->id);

  static uint16_t buf[ICON_BOX_W * ICON_BOX_H];  // 256 像素 = 512 字节，static 进 BSS
  for (int j = 0; j < ICON_BOX_H; j++) {
    uint16_t bits = ((uint16_t)pgm_read_byte(&MUTE_ICON_BMP[j * 2]) << 8)
                  | pgm_read_byte(&MUTE_ICON_BMP[j * 2 + 1]);
    for (int i = 0; i < ICON_BOX_W; i++) {
      buf[j * ICON_BOX_W + i] = ((bits >> (15 - i)) & 1) ? ICON_COLOR : BG;
    }
  }

  tft.startWrite();
  tft.setAddrWindow(ICON_BOX_X, ICON_BOX_Y, ICON_BOX_W, ICON_BOX_H);
  tft.writePixels(buf, ICON_BOX_W * ICON_BOX_H);
  tft.endWrite();
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
    mag_step_idx++;
    if (mag_step_idx >= mag_pattern->count) {
      if (mag_pattern->loop) {
        mag_step_idx = 0;
      } else {
        // 整段播完即停：断电 + 清掉 pattern，避免下一次 tick 再进来
        magnetSet(0);
        mag_pattern = nullptr;
        return;
      }
    }
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

// 长按到达阈值时立刻触发的动作（无需等松开）
void onLongPressFired() {
  Serial.println("LONG");
  muted = !muted;
  Serial.printf("MUTE %d\n", muted ? 1 : 0);
  if (muted) { buzzerOff(); buzz_melody = nullptr; }   // 只静蜂鸣器，电磁铁继续
  need_redraw = true;   // 强制下一帧重绘，让静音图标立即显示/消失
}

void tickButton() {
  int reading = digitalRead(BTN_PIN);
  if (reading != btn_last_read) {
    btn_last_change = millis();
    btn_last_read = reading;
  }

  // 边沿检测：按下 / 松开瞬间
  if ((millis() - btn_last_change) > BTN_DEBOUNCE_MS && reading != btn_last_stable) {
    btn_last_stable = reading;
    if (btn_last_stable == LOW) {
      // 刚按下
      btn_press_start = millis();
      btn_long_fired = false;
      onPress();
    } else {
      // 刚松开：长按已经触发过就不再打 RELEASE，避免冗余
      if (!btn_long_fired) {
        Serial.printf("RELEASE %lu\n", millis() - btn_press_start);
      }
    }
  }

  // 持续按下中：达到长按阈值的瞬间立刻触发（不等松开）
  if (btn_last_stable == LOW && !btn_long_fired &&
      (millis() - btn_press_start) >= BTN_LONG_PRESS_MS) {
    btn_long_fired = true;
    onLongPressFired();
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
      if (muted) drawMuteIcon();   // 静音时盖一层右下角图标（每次螃蟹帧覆盖了整屏）
    }
    need_redraw = false;
  }
}
