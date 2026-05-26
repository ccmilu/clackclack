#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Claude Code 物理状态机 - Hook 入口脚本
============================================
被 Claude Code hooks 调用，把事件转成单字符指令发给 ESP32。
打包成插件后，由 ${CLAUDE_PLUGIN_ROOT}/hooks/hooks.json 触发。

用法（一般由 Claude Code 自动调用，不手动）：
    physical-status.py <event_type>
    event_type ∈ {notification, stop, user_prompt, pre_write_tool}

环境变量：
    CLAUDE_DEVICE_PORT  ESP32 串口设备路径（如 /dev/cu.usbmodem1101）
                        不设置时会自动扫描 /dev/cu.usbmodem* 和 /dev/cu.usbserial*，
                        找到任一就用。完全没插设备才进入"干跑模式"。

依赖：pyserial（pip install pyserial）
"""
import glob
import os
import sys
from datetime import datetime
from pathlib import Path

# 事件 → 单字符指令（与 ESP32 固件 claude_status.ino 的状态约定对齐）
#   user_prompt    用户提交输入        → T 思考（AI 开始处理新请求）
#   pre_write_tool AI 即将编辑代码     → W 写代码（Edit/Write/MultiEdit/NotebookEdit）
#   stop           Claude 输出停止     → D 完成（蹦一下电磁铁 + 上行音）
#                                       固件会在 D 状态保持几秒后自动转 I 空闲
#   notification   Claude 弹窗等用户   → D 完成（重置 D 计时器，再次蹦一下）
EVENT_TO_CMD = {
    "user_prompt":    "T",
    "pre_write_tool": "W",
    "stop":           "D",
    "notification":   "D",
}

# 日志文件：放在脚本所在目录（即插件目录），与项目无关
SCRIPT_DIR  = Path(__file__).resolve().parent
LOG_FILE    = SCRIPT_DIR / "events.log"
BAUD_RATE   = 115200


def auto_detect_port() -> str:
    """扫描所有 /dev/cu.usbmodem* 和 usbserial*，返回第一个找到的。没有就返回空串。
    macOS 的 ESP32-C3 SuperMini 串口名通常是 cu.usbmodemXXXX，每次拔插数字会变。"""
    candidates = sorted(
        glob.glob("/dev/cu.usbmodem*") + glob.glob("/dev/cu.usbserial*")
    )
    return candidates[0] if candidates else ""


# 优先取环境变量；没设就自动扫描第一个串口。两者都没就干跑。
DEVICE_PORT = os.environ.get("CLAUDE_DEVICE_PORT", "").strip() or auto_detect_port()


def log(msg: str) -> None:
    """追加一行带时间戳的日志，永远不抛异常以免影响 Claude Code 主流程。"""
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass


def send_to_device(cmd: str) -> None:
    """向 ESP32 发送单字符指令。串口未配置/未检测到则干跑（只写日志）。"""
    if not DEVICE_PORT:
        log(f"DRY_RUN  cmd={cmd}  (没找到 /dev/cu.usbmodem* 串口)")
        return
    try:
        import serial  # 延迟导入，干跑模式不要求装 pyserial
        with serial.Serial(DEVICE_PORT, BAUD_RATE, timeout=1) as ser:
            ser.write(cmd.encode("ascii"))
            log(f"SENT     cmd={cmd}  port={DEVICE_PORT}")
    except Exception as e:
        log(f"ERROR    cmd={cmd}  port={DEVICE_PORT}  err={e}")


def main() -> None:
    event = sys.argv[1].lower() if len(sys.argv) > 1 else "unknown"
    cmd = EVENT_TO_CMD.get(event)
    if cmd is None:
        log(f"WARN     未知事件: {event!r}")
        return
    log(f"EVENT    {event} → {cmd}")
    send_to_device(cmd)


if __name__ == "__main__":
    main()
