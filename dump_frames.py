#!/opt/anaconda3/envs/claude-device/bin/python
# -*- coding: utf-8 -*-
"""
dump_frames.py — 把 crab_data.py 里的所有帧解码并保存为 PNG
                供肉眼检查"末尾闪烁"到底是哪一帧出问题
用法：python dump_frames.py
输出：build/debug/<state>_<segment>_NN.png
"""
import sys
from pathlib import Path
from PIL import Image

PROJECT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT / "build"))
import crab_data as cd

OUT_DIR = PROJECT / "build" / "debug"
OUT_DIR.mkdir(parents=True, exist_ok=True)

W, H = cd.FRAME_W, cd.FRAME_H


def decode_rle_to_image(palette: bytes, rle: bytes) -> Image.Image:
    """RLE → 调色板查表 → RGB888 PIL Image"""
    img = Image.new("RGB", (W, H))
    px = img.load()
    pos = 0
    i = 0
    while i < len(rle):
        count = rle[i]
        idx   = rle[i + 1]
        c     = (palette[idx * 2] << 8) | palette[idx * 2 + 1]
        r = ((c >> 11) & 0x1F) << 3
        g = ((c >> 5)  & 0x3F) << 2
        b = ( c        & 0x1F) << 3
        for _ in range(count):
            x = pos % W
            y = pos // W
            px[x, y] = (r, g, b)
            pos += 1
        i += 2
    return img


def main():
    for state, data in cd.ANIMATIONS.items():
        palette = data["palette"]
        for seg in ("intro", "loop"):
            if seg not in data:
                continue
            frames = data[seg]
            for i, rle in enumerate(frames):
                img = decode_rle_to_image(palette, rle)
                # 放大 5 倍方便肉眼看
                img = img.resize((W * 5, H * 5), Image.NEAREST)
                out = OUT_DIR / f"{state}_{seg}_{i:02d}.png"
                img.save(out)
            print(f"{state}.{seg}: {len(frames)} 帧已 dump 到 {OUT_DIR.name}/")
    print(f"\n打开看：open {OUT_DIR}")


if __name__ == "__main__":
    main()
