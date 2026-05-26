// 自定义类型集中放这里，避免 Arduino IDE 自动生成函数原型时找不到类型。
// 这个 .h 会被 Arduino IDE 优先处理，原型就能找到 Segment / MagStep / BuzzNote 等。
#pragma once
#include <Arduino.h>

enum Segment { SEG_INTRO, SEG_LOOP };

struct MagStep {
  uint16_t duration_ms;
  uint8_t  duty;          // 0-255
};

struct MagPattern {
  const MagStep* steps;
  uint16_t count;
};

struct BuzzNote {
  uint16_t freq;          // Hz, 0 = 静音
  uint16_t duration_ms;
  uint16_t gap_ms;
  uint8_t  vol;           // 0-100, 该音符相对音量（在全局 BUZZER_VOLUME 基础上再乘以这个百分比）
};

struct BuzzMelody {
  const BuzzNote* notes;
  uint16_t count;
};
