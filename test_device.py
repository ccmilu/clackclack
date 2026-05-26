#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_device.py — Claude 物理状态机硬件测试工具
================================================
用法：
    python test_device.py [/dev/cu.usbmodem...]
    （不传端口会自动找第一个 cu.usbmodem*）

功能：
  - 按键盘 1-6 切换状态 T/W/N/D/E/I（同步发到 ESP32）
  - a   自动循环演示 6 个状态（每个 5 秒，再按 a 停止）
  - m   切换静音模式
  - q   退出
  - 实时显示 ESP32 上报的事件（PRESS/RELEASE/LONG/STATE/MUTE）

依赖：
    pip install pyserial   （conda 环境 claude-device 已装）
"""
import argparse
import glob
import select
import sys
import termios
import threading
import time
import tty
from pathlib import Path

import serial


KEY_TO_STATE = {
    "1": "T",  # Thinking
    "2": "W",  # Writing
    "3": "N",  # Notify
    "4": "D",  # Done
    "5": "E",  # Error
    "6": "I",  # Idle
}
STATE_LABELS = {
    "T": "思考",
    "W": "写代码",
    "N": "通知",
    "D": "完成",
    "E": "报错",
    "I": "空闲",
}

# ANSI 颜色
GREEN = "\033[92m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
MAGENTA = "\033[95m"
RED = "\033[91m"
GRAY = "\033[90m"
BOLD = "\033[1m"
RESET = "\033[0m"


def auto_find_port() -> str:
    """自动找第一个 ESP32 串口"""
    candidates = sorted(glob.glob("/dev/cu.usbmodem*") + glob.glob("/dev/cu.usbserial*"))
    if not candidates:
        print(f"{RED}[错误] 找不到 /dev/cu.usbmodem* 串口。{RESET}")
        print("       检查 ESP32 是否插上 USB，运行 ls /dev/cu.* 看看。")
        sys.exit(1)
    return candidates[0]


def get_key_nowait():
    """非阻塞读一个键。读不到返回 None。"""
    r, _, _ = select.select([sys.stdin], [], [], 0)
    if r:
        return sys.stdin.read(1)
    return None


def render_event(line: str) -> str:
    """给 ESP32 上报加上彩色"""
    if line.startswith("STATE "):
        c = line[6]
        return f"{CYAN}● 切换 → {c} ({STATE_LABELS.get(c, '?')}){RESET}"
    if line == "READY":
        return f"{GREEN}✓ 设备就绪{RESET}"
    if line == "PRESS":
        return f"{YELLOW}⊕ 按钮按下{RESET}"
    if line.startswith("RELEASE "):
        ms = line.split()[1]
        return f"{GRAY}  松开 ({ms}ms){RESET}"
    if line == "LONG":
        return f"{MAGENTA}◉ 长按触发{RESET}"
    if line.startswith("MUTE "):
        v = line.split()[1]
        return f"{MAGENTA}🔇 静音 {'开' if v == '1' else '关'}{RESET}"
    if line.startswith("LOG: "):
        return f"{GRAY}  {line[5:]}{RESET}"
    return f"{GRAY}  {line}{RESET}"


def reader_thread(ser: serial.Serial, stop_flag: dict) -> None:
    """后台线程：读串口并打印 ESP32 上报"""
    buf = b""
    while not stop_flag["stop"]:
        try:
            data = ser.read(64)
            if data:
                buf += data
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    text = line.decode("utf-8", errors="replace").strip()
                    if text:
                        print(f"\r{render_event(text)}", flush=True)
                        print(prompt(), end="", flush=True)
        except Exception as e:
            print(f"\n{RED}[串口错误] {e}{RESET}", flush=True)
            break


def prompt() -> str:
    return f"{BOLD}» {RESET}"


def print_menu():
    print(f"{BOLD}===== Claude 物理状态机硬件测试 ====={RESET}")
    print(f"  {CYAN}1{RESET} 思考(T)   {CYAN}2{RESET} 写代码(W)  {CYAN}3{RESET} 通知(N)")
    print(f"  {CYAN}4{RESET} 完成(D)   {CYAN}5{RESET} 报错(E)    {CYAN}6{RESET} 空闲(I)")
    print(f"  {YELLOW}a{RESET} 自动循环演示  {MAGENTA}m{RESET} 切换静音  {RED}q{RESET} 退出")
    print(f"  ─ 按物理按钮会看到 PRESS/RELEASE 事件 ─")


def auto_demo_loop(ser, stop_flag):
    """后台线程：每 5 秒自动切下一个状态"""
    order = "TWNDEI"
    idx = 0
    while stop_flag.get("auto", False):
        c = order[idx % len(order)]
        try:
            ser.write(c.encode())
        except Exception:
            return
        idx += 1
        for _ in range(50):   # 5 秒 = 50 × 0.1 秒，方便随时中断
            if not stop_flag.get("auto", False):
                return
            time.sleep(0.1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("port", nargs="?", default=None,
                        help="串口路径（默认自动找 /dev/cu.usbmodem*）")
    parser.add_argument("--baud", type=int, default=115200)
    args = parser.parse_args()

    port = args.port or auto_find_port()
    print(f"{GRAY}打开串口 {port} @ {args.baud}{RESET}")

    try:
        ser = serial.Serial(port, args.baud, timeout=0.05)
    except Exception as e:
        print(f"{RED}[错误] 打开串口失败：{e}{RESET}")
        sys.exit(1)

    time.sleep(0.5)   # 等 ESP32 重启

    stop_flag = {"stop": False, "auto": False}
    auto_thread = None

    reader = threading.Thread(target=reader_thread, args=(ser, stop_flag), daemon=True)
    reader.start()

    print_menu()
    print()

    # 切换到无缓冲键盘输入模式
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        print(prompt(), end="", flush=True)

        while True:
            key = get_key_nowait()
            if key is None:
                time.sleep(0.02)
                continue

            if key == "q":
                print("\n退出。")
                break
            elif key in KEY_TO_STATE:
                if stop_flag["auto"]:
                    stop_flag["auto"] = False   # 手动切换 → 停止自动演示
                state = KEY_TO_STATE[key]
                ser.write(state.encode())
                print(f"\r{GRAY}→ 发送 {state}{RESET}", flush=True)
                print(prompt(), end="", flush=True)
            elif key == "a":
                if stop_flag["auto"]:
                    stop_flag["auto"] = False
                    print(f"\r{YELLOW}⏸ 自动演示已停止{RESET}", flush=True)
                else:
                    stop_flag["auto"] = True
                    auto_thread = threading.Thread(target=auto_demo_loop,
                                                    args=(ser, stop_flag),
                                                    daemon=True)
                    auto_thread.start()
                    print(f"\r{YELLOW}▶ 自动演示开始（再按 a 停止）{RESET}", flush=True)
                print(prompt(), end="", flush=True)
            elif key == "m":
                # 静音切换由 ESP32 长按触发，这里没法直接发指令，提示一下
                print(f"\r{GRAY}（静音切换请长按物理按钮）{RESET}", flush=True)
                print(prompt(), end="", flush=True)
            elif key == "?":
                print()
                print_menu()
                print(prompt(), end="", flush=True)

    finally:
        stop_flag["stop"] = True
        stop_flag["auto"] = False
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        ser.close()


if __name__ == "__main__":
    main()
