/*
  Claude 物理状态机 — 显示固件 v0（仅 TFT 屏）
  ==============================================
  把 6 个状态的螃蟹动画显示到 TFT 屏。
  默认行为：
    - 上电后自动循环演示 6 个状态，每个停留 5 秒
    - 同时监听串口（115200），收到 T/W/N/D/E/I 字符立刻切换并重置自动计时
  后续会在这个基础上加蜂鸣器 / 电磁铁 / 微动开关 / Mac hook 联动。

  TFT 参数（之前调好的）：
    INITR_MINI160x80_PLUGIN, ROTATION=3, offset=0/0, INVERT=false, SWAP_RB=false
*/

#include <Adafruit_GFX.h>
#include <Adafruit_ST7735.h>
#include <SPI.h>
#include "crab_data.h"

// ===== TFT 接线 =====
#define TFT_CS    10
#define TFT_RST    8
#define TFT_DC     7
#define TFT_MOSI   5   // SDA
#define TFT_SCLK   6   // SCL

// hardware SPI 构造函数（用 &SPI 实例，速度可达 27MHz+，比 software SPI 快 50 倍）
// 接线不变，仍是 GP5/GP6 那两根，下面 SPI.begin() 会把硬件 SPI 重映射到这两脚
Adafruit_ST7735 tft(&SPI, TFT_CS, TFT_DC, TFT_RST);

// ===== 屏幕参数 =====
constexpr int SCREEN_W = 160;
constexpr int SCREEN_H = 80;
// 螃蟹画布居中位置（FRAME_W/H 来自 crab_data.h）
constexpr int CRAB_X = (SCREEN_W - FRAME_W) / 2;
constexpr int CRAB_Y = (SCREEN_H - FRAME_H) / 2;

// ===== 自动演示参数 =====
const char DEMO_ORDER[] = "TWNDEI";    // 演示顺序：思考→写代码→通知→完成→报错→空闲
constexpr unsigned long STATE_DEMO_MS = 5000;  // 每个状态显示 5 秒

// ===== 运行时状态 =====
const CrabState* current = nullptr;
enum Segment { SEG_INTRO, SEG_LOOP };
Segment current_segment = SEG_LOOP;
uint16_t frame_idx = 0;
unsigned long last_swap_ms = 0;
unsigned long state_start_ms = 0;
int demo_index = 0;

// ===== 取当前段的帧数 / 帧数据 =====
uint16_t segmentFrameCount(const CrabState* s, Segment seg) {
  if (!s) return 0;
  return (seg == SEG_INTRO) ? s->intro_count : s->loop_count;
}
const uint8_t* segmentFrameData(const CrabState* s, Segment seg, uint16_t i) {
  return (seg == SEG_INTRO) ? s->intro_frames[i] : s->loop_frames[i];
}
uint16_t segmentFrameSize(const CrabState* s, Segment seg, uint16_t i) {
  return (seg == SEG_INTRO) ? s->intro_sizes[i] : s->loop_sizes[i];
}

// ===== 切换到指定状态（按字符 T/W/N/D/E/I）=====
void switchState(char c) {
  const CrabState* s = getStateByChar(c);
  if (!s) {
    Serial.printf("[warn] unknown state '%c'\n", c);
    return;
  }
  current = s;
  // 有 intro 段就先播 intro，否则直接 loop
  current_segment = (s->intro_count > 0) ? SEG_INTRO : SEG_LOOP;
  frame_idx = 0;
  last_swap_ms = millis();
  Serial.printf("[switch] -> %c (%s, %u frames)\n",
                c,
                (current_segment == SEG_INTRO) ? "intro" : "loop",
                segmentFrameCount(s, current_segment));
}

// ===== 绘制单帧 =====
// 用 setAddrWindow + writeColor 批量推像素，比 drawPixel 快十几倍
void drawFrame(const CrabState* s, const uint8_t* rle_data, uint16_t rle_size,
               int ox, int oy) {
  tft.startWrite();
  tft.setAddrWindow(ox, oy, FRAME_W, FRAME_H);
  for (uint16_t i = 0; i < rle_size; i += 2) {
    uint8_t count = rle_data[i];
    uint8_t idx   = rle_data[i + 1];
    // palette 是大端 RGB565：[hi, lo]
    uint16_t c = ((uint16_t)s->palette[idx * 2] << 8) | s->palette[idx * 2 + 1];
    tft.writeColor(c, count);
  }
  tft.endWrite();
}

// ===== 翻帧 + intro→loop 段切换（与 simulator.py 完全对等的逻辑）=====
void advanceFrame() {
  if (!current) return;
  uint16_t total = segmentFrameCount(current, current_segment);
  if (total == 0) return;

  unsigned long now = millis();
  if (now - last_swap_ms < MS_PER_FRAME) return;

  frame_idx++;
  if (frame_idx >= total) {
    // 当前段播完
    if (current_segment == SEG_INTRO && current->loop_count > 0) {
      current_segment = SEG_LOOP;
      frame_idx = 0;
      Serial.printf("[seg] %c: intro -> loop\n", current->id);
    } else {
      frame_idx = 0;  // loop 段无限循环
    }
  }
  last_swap_ms = now;
}

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("=== Claude status display starting ===");

  // ESP32-C3 的硬件 SPI 默认引脚不是我们接的 GP5/GP6，
  // 用 SPI.begin(SCK, MISO, MOSI, SS) 重映射到接线方案
  // （MISO 不需要，传 -1）
  SPI.begin(TFT_SCLK, -1, TFT_MOSI, TFT_CS);

  // TFT 初始化（之前调好的参数）
  tft.initR(INITR_MINI160x80_PLUGIN);
  tft.setRotation(3);
  tft.invertDisplay(false);
  tft.setSPISpeed(27000000);   // 27MHz，ST7735 标准上限；不稳定可降到 20MHz
  tft.fillScreen(ST77XX_BLACK);

  Serial.printf("Screen: %dx%d, crab: %dx%d, FPS=%d\n",
                SCREEN_W, SCREEN_H, FRAME_W, FRAME_H, FPS);
  Serial.printf("Demo order: %s (each %lums)\n", DEMO_ORDER, STATE_DEMO_MS);
  Serial.println("串口输入 T/W/N/D/E/I 可手动切换");

  switchState(DEMO_ORDER[0]);
  state_start_ms = millis();
}

void loop() {
  unsigned long now = millis();

  // 1. 串口手动切换
  while (Serial.available()) {
    char c = Serial.read();
    if (c >= 'a' && c <= 'z') c -= 32;   // 转大写
    if (getStateByChar(c)) {
      switchState(c);
      state_start_ms = now;
    }
  }

  // 2. 自动演示切换
  if (now - state_start_ms >= STATE_DEMO_MS) {
    demo_index = (demo_index + 1) % (sizeof(DEMO_ORDER) - 1);
    switchState(DEMO_ORDER[demo_index]);
    state_start_ms = now;
  }

  // 3. 翻帧
  advanceFrame();

  // 4. 渲染当前帧
  if (current) {
    uint16_t total = segmentFrameCount(current, current_segment);
    if (total > 0 && frame_idx < total) {
      const uint8_t* rle = segmentFrameData(current, current_segment, frame_idx);
      uint16_t sz        = segmentFrameSize(current, current_segment, frame_idx);
      drawFrame(current, rle, sz, CRAB_X, CRAB_Y);
    }
  }
}
