#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
simulator.py — TFT 屏模拟器（双视图版）
=========================================
加载 build/crab_data.py（由 build_assets.py 生成），
逐像素 RGB565 解码绘制，与 ESP32 真机的绘制循环对等。
模拟器看到的画面 = ESP32 真机显示的画面。

窗口布局：
  ┌──────────────────────────────────────┐
  │                                       │
  │       5x 放大区（800 × 400）           │  ← 方便人眼看清像素
  │                                       │
  ├──────────────────────────────────────┤
  │     ┌─────────────┐    1:1 真实尺寸   │  ← 直观感受真机大小
  │     │ 160 × 80    │    （桌面上很小） │
  │     └─────────────┘                   │
  └──────────────────────────────────────┘

按键：
    1=T  2=W  3=N  4=D  5=E  6=I  切换状态
    Q / ESC                        退出
"""
import sys
from pathlib import Path

import pygame

# === 加载构建产物 ===
PROJECT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT / "build"))
try:
    import crab_data
except ImportError:
    print("[错误] 没找到 build/crab_data.py")
    print("       请先运行: python build_assets.py")
    sys.exit(1)

FRAME_W      = crab_data.FRAME_W
FRAME_H      = crab_data.FRAME_H
MS_PER_FRAME = crab_data.MS_PER_FRAME
ANIMATIONS   = crab_data.ANIMATIONS

# ============================================================
# 可调参数（直接改这里的数字即可，下面代码会自动适配）
# ============================================================

# 真实 TFT 屏物理分辨率（固定，不要改）
SCREEN_W, SCREEN_H = 160, 80

# 放大区倍率：上半窗口把 160x80 放大几倍显示，看清像素颗粒用
# 调大窗口更大、像素更清楚；调小整体更紧凑
ZOOM_SCALE = 5

# 1:1 真实尺寸视图的物理尺寸校准
# ------------------------------------------------------------
# 默认 1.0 时，下方视图按 1:1 像素绘制（160x80 个 Mac 像素）。
# 但 Mac Retina 屏物理像素密度（~220ppi）比 TFT 屏（~170ppi）高，
# 所以"逻辑 1:1"看起来比桌上真机大约大 30%。
# 拿你桌上 TFT 屏对着模拟器底部那个小框比较，调整这个值到尺寸匹配：
#   - 模拟器框比真机大 → 调小（如 0.75、0.65）
#   - 模拟器框比真机小 → 调大（如 1.1、1.2）
REAL_VIEW_SCALE = 0.75

# 屏外背景色（真机没有，仅模拟器窗口背景）
BG_COLOR  = (0, 0, 0)
WINDOW_BG = (28, 28, 32)

# ============================================================
# 下面是由参数自动派生的尺寸，一般无需修改
# ============================================================
ZOOM_W, ZOOM_H = SCREEN_W * ZOOM_SCALE, SCREEN_H * ZOOM_SCALE
REAL_W = max(1, int(SCREEN_W * REAL_VIEW_SCALE))
REAL_H = max(1, int(SCREEN_H * REAL_VIEW_SCALE))
GAP    = 40
PAD_Y  = 16
WIN_W  = ZOOM_W
WIN_H  = ZOOM_H + GAP + REAL_H + PAD_Y * 2
REAL_X = (WIN_W - REAL_W) // 2
REAL_Y = ZOOM_H + GAP + PAD_Y

KEY_TO_STATE = {
    pygame.K_1: "T", pygame.K_2: "W", pygame.K_3: "N",
    pygame.K_4: "D", pygame.K_5: "E", pygame.K_6: "I",
}


def load_cn_font(size: int) -> pygame.font.Font:
    """加载支持中文的字体。Mac 自带 PingFang SC 等。失败回退到默认字体（中文显示方框）。"""
    for name in ("PingFang SC", "Hiragino Sans GB", "Heiti SC",
                 "STHeiti", "Songti SC", "Arial Unicode MS"):
        path = pygame.font.match_font(name)
        if path:
            return pygame.font.Font(path, size)
    return pygame.font.Font(None, size)


def rgb565_to_rgb888(c: int) -> tuple:
    """16 位 RGB565 → 24 位 (R, G, B) 元组"""
    r = ((c >> 11) & 0x1F) << 3
    g = ((c >> 5)  & 0x3F) << 2
    b = ( c        & 0x1F) << 3
    return (r, g, b)


def draw_frame(surf: pygame.Surface, palette: bytes, rle_data: bytes,
               ox: int, oy: int) -> None:
    """RLE 解码 + 调色板查表 + 逐像素绘制。
    数据格式：rle_data 每 2 字节一组 (count, palette_index)
    ESP32 同款循环：解到颜色后 → tft.drawPixel(x, y, c) 一致"""
    pos = 0      # 当前像素线性下标（0..FRAME_W*FRAME_H-1）
    i = 0
    while i < len(rle_data):
        count = rle_data[i]
        idx   = rle_data[i + 1]
        c     = (palette[idx * 2] << 8) | palette[idx * 2 + 1]   # RGB565
        rgb   = rgb565_to_rgb888(c)
        for _ in range(count):
            x = pos % FRAME_W
            y = pos // FRAME_W
            surf.set_at((ox + x, oy + y), rgb)
            pos += 1
        i += 2


def initial_segment(state: str) -> str:
    """进入状态时该播哪一段：有 intro 就先 intro，否则直接 loop"""
    if state in ANIMATIONS and "intro" in ANIMATIONS[state]:
        return "intro"
    return "loop"


def main() -> None:
    pygame.init()
    pygame.display.set_caption(
        f"Claude 物理状态机模拟器  ·  TFT {SCREEN_W}x{SCREEN_H}  ·  螃蟹 {FRAME_W}x{FRAME_H}"
    )
    window = pygame.display.set_mode((WIN_W, WIN_H))
    canvas = pygame.Surface((SCREEN_W, SCREEN_H))   # 真实 TFT 画布
    clock  = pygame.time.Clock()
    font_big   = load_cn_font(20)
    font_small = load_cn_font(14)
    font_tiny  = load_cn_font(12)

    state        = "E" if "E" in ANIMATIONS else next(iter(ANIMATIONS), "E")
    segment      = initial_segment(state)
    frame_idx    = 0
    last_swap_ms = pygame.time.get_ticks()
    print(f"启动。当前状态: {state} ({segment})。按 1-6 切换，Q/ESC 退出。")
    print(f"已加载状态: {list(ANIMATIONS.keys())}")

    # 螃蟹在 160x80 真机画布上的居中位置
    crab_x = (SCREEN_W - FRAME_W) // 2
    crab_y = (SCREEN_H - FRAME_H) // 2

    while True:
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                pygame.quit(); sys.exit(0)
            if ev.type == pygame.KEYDOWN:
                if ev.key in (pygame.K_ESCAPE, pygame.K_q):
                    pygame.quit(); sys.exit(0)
                if ev.key in KEY_TO_STATE:
                    new_state = KEY_TO_STATE[ev.key]
                    if new_state != state:
                        state        = new_state
                        segment      = initial_segment(state)
                        frame_idx    = 0
                        last_swap_ms = pygame.time.get_ticks()
                        loaded = " (loaded)" if state in ANIMATIONS else " (no data)"
                        print(f"[切换] → {state} ({segment}){loaded}")

        # 翻帧 + intro→loop 切换
        now = pygame.time.get_ticks()
        frames = []
        if state in ANIMATIONS:
            frames = ANIMATIONS[state].get(segment, [])
            if frames and now - last_swap_ms >= MS_PER_FRAME:
                frame_idx += 1
                if frame_idx >= len(frames):
                    # 当前段播完
                    if segment == "intro" and "loop" in ANIMATIONS[state]:
                        segment   = "loop"
                        frame_idx = 0
                        # 关键：segment 变了，frames 局部变量必须同步重新取，
                        # 否则这一帧会绘制 intro[0]（站立姿势），看起来像"闪到打哈欠开头"
                        frames    = ANIMATIONS[state].get(segment, [])
                        print(f"[段切换] {state}: intro → loop")
                    else:
                        frame_idx = 0   # loop 段无限循环
                last_swap_ms = now

        # === 渲染真机画布（这是 ESP32 真机也会执行的逻辑）===
        canvas.fill(BG_COLOR)
        if frames:
            palette  = ANIMATIONS[state]["palette"]
            rle_data = frames[frame_idx]
            draw_frame(canvas, palette, rle_data, crab_x, crab_y)

        # === 模拟器窗口绘制 ===
        window.fill(WINDOW_BG)
        # 上：放大区
        zoomed = pygame.transform.scale(canvas, (ZOOM_W, ZOOM_H))
        window.blit(zoomed, (0, 0))
        # 下：真实尺寸区（按 REAL_VIEW_SCALE 缩放到物理匹配大小）
        real_view = pygame.transform.scale(canvas, (REAL_W, REAL_H))
        window.blit(real_view, (REAL_X, REAL_Y))
        # 1:1 区边框
        pygame.draw.rect(window, (80, 80, 90),
                         (REAL_X - 1, REAL_Y - 1, REAL_W + 2, REAL_H + 2), 1)

        # 标签
        if frames:
            zoom_text = (f"{ZOOM_SCALE}x 放大  ·  {state} [{segment}]"
                         f"  {frame_idx + 1}/{len(frames)}")
        else:
            zoom_text = f"{ZOOM_SCALE}x 放大  ·  {state} (无数据)"
        window.blit(font_big.render(zoom_text, True, (240, 240, 240)),
                    (12, ZOOM_H + 12))

        real_label = font_small.render(
            f"实际尺寸  (调 REAL_VIEW_SCALE 匹配真机大小，当前 {REAL_VIEW_SCALE})",
            True, (200, 200, 210))
        window.blit(real_label, (REAL_X + REAL_W + 14, REAL_Y + REAL_H // 2 - 8))

        tip = font_tiny.render("1-6 切换状态  /  Q 退出", True, (140, 140, 150))
        window.blit(tip, (WIN_W - tip.get_width() - 12, ZOOM_H + 16))

        pygame.display.flip()
        clock.tick(60)


if __name__ == "__main__":
    main()
