#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_assets.py — 资产构建脚本（端到端最小验证版）
====================================================
SVG 源 → Playwright Chromium 加载并采样多帧
       → 缩放到 FRAME_W × FRAME_H
       → 每像素转 RGB565（与 ESP32 真机一致）
       → 输出 build/crab_data.py（simulator.py import 它）

后续扩展（不在本轮范围）：
- 同步生成 build/crab_data.h 给 ESP32 固件
- 6 个状态并行采样
- 每状态独立的帧数 / fps / 循环规则
"""
import re
import sys
from io import BytesIO
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

# === 项目路径 ===
PROJECT    = Path(__file__).resolve().parent
ASSETS_DIR = PROJECT / "assets" / "svg"
BUILD_DIR  = PROJECT / "build"

# === 渲染参数 ===
FRAME_W, FRAME_H = 160, 80     # 全屏渲染（SVG 已改为 2:1 横屏布局，每帧就是整块屏幕）
FPS              = 15           # 采样帧率（每秒 15 帧 → 间隔 66ms）
INTERVAL_MS      = 1000 // FPS
DEFAULT_DURATION_MS = 2000      # 若 SVG 里没有任何 animation 声明，用这个兜底

# 采样到 SVG 动画时长的多少比例
SAMPLE_DURATION_RATIO = 1.0

# 每个状态可以有两段：
#   intro: 可选的入场动画，进入状态时播放一次后停止（如 yawn 哈欠一次）
#   loop:  必须的稳态动画，无限循环（如 doze 打盹）
# 每段 = (svg_filename, duration_ms_or_None)
#   duration_ms = None → 自动从 SVG 里扫 `animation: ... Xs` 取最大值
STATES = {
    "E": {"loop":  ("E_error.svg", None)},
    "I": {"intro": ("I_idle.svg",  None),       # yawn 哈欠一次
          "loop":  ("I_doze.svg",  None)},      # doze 打盹循环
    "T": {"loop":  ("T_think.svg", None)},      # 思考（左右气泡 + got it），12s 循环
    "D": {"loop":  ("D_done.svg",  None)},      # 完成（举花挥手 + ^^眼 + 闪光）
    "W": {"loop":  ("W_write.svg", None)},      # 写代码（坐在笔记本前打字 + 数据粒子飘起）
    "N": {"loop":  ("N_notify.svg", None)},     # 通知（感叹号弹出 → 看左 → 眨眼 → 变 >< 大叉眼 + 甩手）
}


def rgb_to_rgb565(r: int, g: int, b: int) -> int:
    """24 位 RGB → 16 位 RGB565（ESP32 TFT 库通用颜色格式）"""
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)


def png_to_rgb_image(png_bytes: bytes) -> Image.Image:
    """PNG → RGB PIL Image，固定到 FRAME_W × FRAME_H"""
    img = Image.open(BytesIO(png_bytes)).convert("RGB")
    if img.size != (FRAME_W, FRAME_H):
        img = img.resize((FRAME_W, FRAME_H), Image.LANCZOS)
    return img


def build_state_palette(frame_imgs: list, n_colors: int = 256) -> Image.Image:
    """把一个状态的所有帧拼成大图，量化得到该状态专属调色板（PIL P 模式图）"""
    n = len(frame_imgs)
    combined = Image.new("RGB", (FRAME_W, FRAME_H * n))
    for i, im in enumerate(frame_imgs):
        combined.paste(im, (0, i * FRAME_H))
    return combined.convert("P", palette=Image.Palette.ADAPTIVE, colors=n_colors)


def palette_to_rgb565_bytes(pal_img: Image.Image) -> bytes:
    """从 P 模式图里取出调色板，转成 256×2 字节 RGB565（大端）"""
    pal = pal_img.getpalette() or []
    pal = (pal + [0] * (768 - len(pal)))[:768]   # 不足 256 色补 0
    out = bytearray(256 * 2)
    for i in range(256):
        r, g, b = pal[i * 3], pal[i * 3 + 1], pal[i * 3 + 2]
        c = rgb_to_rgb565(r, g, b)
        out[i * 2]     = (c >> 8) & 0xFF
        out[i * 2 + 1] = c & 0xFF
    return bytes(out)


def rgb_image_to_indices(rgb_img: Image.Image, pal_img: Image.Image) -> bytes:
    """把 RGB 图按给定调色板量化，返回 FRAME_W*FRAME_H 字节的索引数组（行优先）。
    关闭 dither 是有意为之：dither 会把大色块变噪点，让 RLE 失效。"""
    indexed = rgb_img.quantize(palette=pal_img, dither=Image.Dither.NONE)
    return bytes(indexed.getdata())


def rle_encode(indices: bytes) -> bytes:
    """简单 RLE：连续相同索引 → (count, index) 对。count ∈ [1, 255]。
    解码端就 2 字节一组拆，循环 push 即可。"""
    if not indices:
        return b""
    out = bytearray()
    cur = indices[0]
    count = 1
    for b in indices[1:]:
        if b == cur and count < 255:
            count += 1
        else:
            out.append(count)
            out.append(cur)
            cur = b
            count = 1
    out.append(count)
    out.append(cur)
    return bytes(out)


def infer_duration_ms(svg_text: str) -> int:
    """扫 SVG 里所有 `animation: name Xs ...` 或 `Xms ...`，取最长那段当采样窗口。
    确保循环回到第 0 帧时，最慢的那个动画也已走完整个周期，避免跳变。"""
    pattern = r'animation\s*:\s*[^;]*?(\d+(?:\.\d+)?)\s*(s|ms)'
    matches = re.findall(pattern, svg_text)
    if not matches:
        return DEFAULT_DURATION_MS
    durations_ms = [
        int(float(v) * 1000) if u == "s" else int(float(v))
        for v, u in matches
    ]
    return max(durations_ms)


def build_html(svg_text: str) -> str:
    """把 SVG inline 进 HTML，去 svg 标签的 width/height 让其按 viewBox 自适应填满 viewport。
    用 !important 强制所有动画 fill-mode: both，避免 paused + currentTime ≈ duration 时
    浏览器把元素回退到原始 style。

    注意：regex 必须只匹配 <svg> 顶层标签的 width/height，而不能误删后续 <rect width=...> 等！
    早期版本用 `\s+(width|height)="..."` count=2 会在 <svg> 没 width/height 时
    匹配到 background <rect>，把 rect 渲染成 0×0 看不见。
    """
    svg_inline = re.sub(
        r'(<svg\b[^>]*?)\s+(width|height)="[^"]*"',
        r'\1',
        svg_text,
        count=2,
    )
    return f"""<!DOCTYPE html>
<html><head><style>
  html, body, svg {{
    margin: 0; padding: 0;
    width: 100%; height: 100%;
    display: block;
    background: #ffffff;
  }}
  /* 关键：让所有动画元素在 finished 状态保持末帧 keyframe，
     不要回退到原始 CSS style（站立姿势） */
  *, *::before, *::after {{ animation-fill-mode: both !important; }}
</style></head>
<body>{svg_inline}</body></html>"""


DEBUG_SAVE_RAW = False  # 调试用：把 Playwright 采到的原始 PNG 保存到 build/debug_raw/


def sample_svg_pngs(svg_path: Path, duration_ms: int) -> list:
    """精确采样 SVG 动画。
    用 Web Animations API 暂停所有动画 + 每帧显式设 currentTime，
    彻底脱离 Playwright 的真实时间，避免"截图慢导致动画跑过头"问题。
    返回 PNG 字节列表（不做量化和 RLE，留给外层批量做）"""
    svg_text = svg_path.read_text(encoding="utf-8")
    html = build_html(svg_text)
    n_frames = max(1, duration_ms * FPS // 1000)
    png_frames = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": FRAME_W, "height": FRAME_H})
        page.set_content(html, wait_until="networkidle")
        page.wait_for_timeout(100)   # 等 CSS animations 真正注册到 document.getAnimations()

        # 抓所有 CSS animation 实例，全部暂停 + 强制 fill 'both'
        # fill='both' 让 paused 状态下永远保持当前帧 keyframe，
        # 避免 currentTime 接近 duration 时浏览器进入 finished 状态、
        # 回退到原始 CSS style（即没有任何 keyframe transform 的"起始姿势"）
        page.evaluate("""() => {
            window.__anims = document.getAnimations();
            window.__anims.forEach(a => {
                a.pause();
                try { a.effect.updateTiming({ fill: 'both' }); } catch (e) {}
            });
        }""")

        for i in range(n_frames):
            t_ms = i * INTERVAL_MS
            # 把所有动画的 currentTime 同步设到 t_ms（无时间漂移）
            page.evaluate("(t) => { window.__anims.forEach(a => { a.currentTime = t; }); }", t_ms)
            # 等浏览器至少重绘一帧
            page.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
            png = page.screenshot()
            png_frames.append(png)
            if DEBUG_SAVE_RAW:
                raw_dir = BUILD_DIR / "debug_raw"
                raw_dir.mkdir(parents=True, exist_ok=True)
                (raw_dir / f"{svg_path.stem}_{i:02d}.png").write_bytes(png)
        browser.close()
    return png_frames


def quantize_and_compress(png_frames: list, pal_img: Image.Image) -> list:
    """给定一组 PNG 帧和一个调色板，量化 + RLE 压缩，返回 [rle_bytes per frame]"""
    out = []
    for png in png_frames:
        rgb = png_to_rgb_image(png)
        idx = rgb_image_to_indices(rgb, pal_img)
        out.append(rle_encode(idx))
    return out


def write_python_data(animations: dict, out_path: Path) -> None:
    """生成 crab_data.py。
    每状态结构：
        "palette": 512 字节 RGB565 大端
        "intro":   [RLE 字节流, ...]   # 可选，进入状态时播放一次
        "loop":    [RLE 字节流, ...]   # 必须，稳态循环播放
    解码：每 2 字节 (count, palette_index) → 查表得 RGB565 → 逐像素绘制
    """
    lines = [
        "# AUTO-GENERATED by build_assets.py — DO NOT EDIT",
        "# simulator.py 与 ESP32 固件共用同一份像素数据",
        "# 存储格式：每状态 256 色调色板（512B 大端 RGB565）+ intro/loop 两段 RLE 帧流",
        "",
        f"FRAME_W = {FRAME_W}",
        f"FRAME_H = {FRAME_H}",
        f"FPS = {FPS}",
        f"MS_PER_FRAME = {INTERVAL_MS}",
        "",
        "ANIMATIONS = {",
    ]
    for state, (palette, segments) in animations.items():
        lines.append(f"    {state!r}: {{")
        lines.append(f'        "palette": {palette!r},')
        for seg_name in ("intro", "loop"):
            if seg_name not in segments:
                continue
            lines.append(f'        {seg_name!r}: [')
            for f in segments[seg_name]:
                lines.append(f"            {f!r},")
            lines.append( "        ],")
        lines.append( "    },")
    lines.append("}")
    lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    if not ASSETS_DIR.exists():
        print(f"[错误] 找不到 {ASSETS_DIR}", file=sys.stderr)
        return 1
    BUILD_DIR.mkdir(exist_ok=True)

    raw_bytes_per_frame = FRAME_W * FRAME_H * 2
    animations = {}
    for state, segments_config in STATES.items():
        # 第 1 步：采每个 segment 的 PNG 帧（不压缩）
        segments_pngs = {}
        for seg_name, (fname, duration_override) in segments_config.items():
            svg_path = ASSETS_DIR / fname
            if not svg_path.exists():
                print(f"[跳过] {state}.{seg_name}: {svg_path} 不存在")
                continue
            svg_text = svg_path.read_text(encoding="utf-8")
            raw_ms = duration_override or infer_duration_ms(svg_text)
            duration_ms = int(raw_ms * SAMPLE_DURATION_RATIO)
            source = "覆盖" if duration_override else "自动"
            print(f"[{state}.{seg_name}] 采样 {fname}  时长 {duration_ms}ms"
                  f"  ({raw_ms}ms × {SAMPLE_DURATION_RATIO}, {source})...")
            segments_pngs[seg_name] = sample_svg_pngs(svg_path, duration_ms)
            print(f"      → {len(segments_pngs[seg_name])} 帧")

        if not segments_pngs:
            continue

        # 第 2 步：合并所有 segment 的帧构建一个统一的 256 色调色板
        # （让 intro 末帧 和 loop 首帧 用同一份调色板，颜色不跳变）
        all_imgs = []
        for pngs in segments_pngs.values():
            all_imgs.extend([png_to_rgb_image(p) for p in pngs])
        pal_img = build_state_palette(all_imgs, n_colors=256)
        palette = palette_to_rgb565_bytes(pal_img)

        # 第 3 步：用统一调色板量化压缩每个 segment
        segments_out = {}
        for seg_name, pngs in segments_pngs.items():
            segments_out[seg_name] = quantize_and_compress(pngs, pal_img)
            seg_bytes = sum(len(f) for f in segments_out[seg_name])
            seg_raw   = len(segments_out[seg_name]) * raw_bytes_per_frame
            print(f"      [{seg_name}]  {len(segments_out[seg_name])} 帧 → {seg_bytes}B"
                  f" (压缩前 {seg_raw}B, 比 {seg_bytes/seg_raw*100:.1f}%)")

        animations[state] = (palette, segments_out)

    out = BUILD_DIR / "crab_data.py"
    write_python_data(animations, out)

    # 总体积统计
    def state_size(palette, segments_out):
        return len(palette) + sum(sum(len(f) for f in fs) for fs in segments_out.values())
    def state_raw(segments_out):
        n = sum(len(fs) for fs in segments_out.values())
        return n * raw_bytes_per_frame
    total_compressed = sum(state_size(p, s) for p, s in animations.values())
    total_raw        = sum(state_raw(s)      for _, s in animations.values())
    print(f"\n输出: {out.relative_to(PROJECT)}")
    print(f"压缩前: {total_raw}  字节 ({total_raw/1024:.1f} KB)")
    print(f"压缩后: {total_compressed}  字节 ({total_compressed/1024:.1f} KB)  ←  {total_compressed/total_raw*100:.1f}% of 原大小")
    return 0


if __name__ == "__main__":
    sys.exit(main())
