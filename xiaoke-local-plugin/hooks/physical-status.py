#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Claude Code 物理状态机 - Hook 入口脚本
============================================
被 Claude Code hooks 调用，把事件转成单字符指令发给 ESP32。

通信策略
--------
1) 首选：unix socket → bridge.py daemon
   daemon 独占串口，hook 只发一个字节就走，极轻量。
2) Fallback：直接打开串口写
   daemon 没启动时用这条保底，让"还没装好 daemon"的场景也能切状态。
3) 自愈：socket 不通时异步触发 `bridge.py --ensure`
   下一次 hook 触发时 daemon 应该已经起来了，自动切到路径 1。

子进程是 fork-and-forget（start_new_session=True）：hook 不等它跑完，
也不读它的输出。这是为了不挤占 hook 的 5 秒 timeout。

用法（一般由 Claude Code 自动调用）：
    physical-status.py <event_type>
    event_type ∈ {notification, stop, user_prompt, pre_write_tool, permission_request}

依赖：pyserial（仅 fallback 模式需要）— socket 路径走通则无依赖
"""
import glob
import os
import socket
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# 事件 → 单字符指令（与 ESP32 固件 claude_status.ino 的状态约定对齐）
EVENT_TO_CMD = {
    "user_prompt":        "T",   # 用户提交输入 → 思考
    "pre_write_tool":     "W",   # 即将编辑代码 → 写代码
    "permission_request": "N",   # 等用户批准敏感操作 → 紧急召唤
    "stop":               "D",   # Claude 输出停止 → 完成
    "notification":       "D",   # 弹窗等用户 → 完成
}

SCRIPT_DIR     = Path(__file__).resolve().parent
LOG_FILE       = SCRIPT_DIR / "events.log"
SOCKET_PATH    = "/tmp/xiaoke-bridge.sock"
VERSION_FILE   = Path("/tmp/xiaoke-bridge.version")   # daemon 启动时写入自身源码 mtime
# 记录当前 Claude Code 跑在哪个 macOS app 里（终端）。
# 按设备按钮时 KeyReturn.app 读这个文件 activate 对应 app 再发回车，让按钮全局可用，
# 不需要焦点在终端上——即使你在浏览器里按按钮也能确认终端里的权限对话框。
TARGET_APP_FILE = Path("/tmp/xiaoke-target-app.txt")
# 标记"当前正在等用户响应权限弹窗"。permission_request 写、user_prompt/stop 清。
# notification 事件触发时如果这个 marker 在，就跳过——避免 Claude Code 的 idle reminder
# 把状态从 N（紧急召唤）覆盖到 D（完成），让用户误以为 Claude 已经放弃等待了。
WAITING_MARKER = Path("/tmp/xiaoke-waiting")
BAUD_RATE      = 115200
# daemon 脚本相对位置：plugin_root/daemon/bridge.py，hook 在 plugin_root/hooks/
DAEMON_PY      = SCRIPT_DIR.parent / "daemon" / "bridge.py"


def log(msg: str) -> None:
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass


def find_terminal_app_name() -> str:
    """沿进程树从 hook 自己往上找祖先，返回第一个 macOS .app bundle 的名字。
    用途：知道当前这次 Claude Code 跑在哪个终端里（Warp/iTerm/Terminal/...），
    按设备按钮时让 KeyReturn.app activate 这个终端再发回车。

    进程链通常长这样：
        python (hook) → sh → node (claude) → zsh → /Applications/Warp.app/Contents/MacOS/stable
    走到第一个 executable 路径含 `.app/` 的就是终端 app。失败/找不到返回空串。"""
    try:
        pid = os.getpid()
        # 沿 ppid 往上走，最多 20 层（防御异常情况下死循环）
        for _ in range(20):
            if pid <= 1:
                break
            r = subprocess.run(
                ["ps", "-p", str(pid), "-o", "ppid=,comm="],
                capture_output=True, text=True, timeout=2,
            )
            if r.returncode != 0:
                break
            parts = r.stdout.strip().split(None, 1)
            if len(parts) < 2:
                break
            ppid_str, comm = parts
            # comm 形如 /Applications/Warp.app/Contents/MacOS/stable
            if ".app/" in comm:
                idx = comm.index(".app/")
                slash = comm.rfind("/", 0, idx)
                return comm[slash + 1: idx]   # "Warp" / "iTerm2" / "Terminal" / ...
            try:
                pid = int(ppid_str)
            except ValueError:
                break
    except Exception:
        pass
    return ""


def record_target_app() -> None:
    """把当前终端 app 名字写到 TARGET_APP_FILE，KeyReturn.app 读它做 activate。
    每次 hook 触发都重写——这样如果用户同时开多个终端跑 Claude Code，
    最后一次 hook 触发的那个终端就是按钮要确认的目标。"""
    app_name = find_terminal_app_name()
    if not app_name:
        return
    try:
        TARGET_APP_FILE.write_text(app_name)
        log(f"TARGET_APP  {app_name}")
    except Exception as e:
        log(f"TARGET_APP_FAIL  err={e}")


# === 多窗口精确切换 ===
# 单个终端 app（Ghostty 等）开多个窗口时，activate 只能切到该 app 的最前窗口，
# 无法切到"Claude Code 正在等权限的那个窗口"。解决方案：
#   1) hook 从进程链找到 Claude Code 的 controlling tty
#   2) 写 OSC 0 escape sequence 到 tty → 改对应窗口标题加 WAIT_MARK
#   3) KeyReturn.app 用 System Events 枚举目标 app 所有窗口，AXRaise 标题带 WAIT_MARK 的那个
WAIT_MARK = "🔴 CLAUDE_WAIT"


def find_claude_tty() -> "Path | None":
    """从 hook 自身往上找父进程链，返回第一个 controlling tty（如 /dev/ttys003）。
    Hook 自身没有 tty（被 Claude Code 把 stdio redirect 掉了），所以要往上找。
    Claude Code → 用户 shell → 终端 app 这条链上 shell 那一级有 tty。"""
    try:
        pid = os.getpid()
        for _ in range(20):
            r = subprocess.run(
                ["ps", "-p", str(pid), "-o", "tty=,ppid="],
                capture_output=True, text=True, timeout=2,
            )
            if r.returncode != 0:
                break
            parts = r.stdout.strip().split()
            if len(parts) < 2:
                break
            tty_name = parts[0]
            ppid_str = parts[-1]
            # ps tty 字段：??（无 tty）/ ttys003 / pts/N
            if tty_name.startswith("ttys") or tty_name.startswith("pts/"):
                return Path(f"/dev/{tty_name}")
            try:
                pid = int(ppid_str)
            except ValueError:
                break
    except Exception:
        pass
    return None


def mark_terminal_window(mark: bool) -> None:
    """通过 OSC 0 修改 Claude Code 所在 tty 对应的终端窗口标题。

    mark=True  → 标题设为 WAIT_MARK（按按钮时切到此窗口的依据）
    mark=False → 标题清空（让 shell 下次提示符刷新自己的标题恢复正常）

    OSC 0 ANSI 序列：ESC ]0;TITLE BEL  几乎所有 xterm 兼容终端都解析。"""
    tty = find_claude_tty()
    if not tty:
        log(f"TTY_MARK_SKIP  no_tty_found  mark={mark}")
        return
    try:
        title = WAIT_MARK if mark else ""
        with open(tty, "w") as f:
            f.write(f"\033]0;{title}\007")
        log(f"TTY_MARK  tty={tty}  mark={mark}")
    except Exception as e:
        log(f"TTY_MARK_FAIL  tty={tty}  err={e}")


def send_via_socket(cmd: str) -> bool:
    """尝试把字符发给 daemon。成功 True，失败 False。"""
    if not os.path.exists(SOCKET_PATH):
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            s.connect(SOCKET_PATH)
            s.sendall(cmd.encode("ascii"))
        log(f"SENT_SOCK   cmd={cmd}")
        return True
    except Exception as e:
        log(f"SOCK_FAIL   cmd={cmd}  err={e}")
        return False


def auto_detect_port() -> str:
    candidates = sorted(
        glob.glob("/dev/cu.usbmodem*") + glob.glob("/dev/cu.usbserial*")
    )
    return candidates[0] if candidates else ""


def send_via_serial(cmd: str) -> None:
    """fallback：直接打开串口写。daemon 占着串口时这里会失败，但那时 socket 路径应该通了。"""
    port = os.environ.get("CLAUDE_DEVICE_PORT", "").strip() or auto_detect_port()
    if not port:
        log(f"DRY_RUN     cmd={cmd}  (没找到串口、也没启 daemon)")
        return
    try:
        import serial   # 延迟导入
        with serial.Serial(port, BAUD_RATE, timeout=1) as ser:
            ser.write(cmd.encode("ascii"))
        log(f"SENT_TTY    cmd={cmd}  port={port}")
    except Exception as e:
        log(f"TTY_FAIL    cmd={cmd}  port={port}  err={e}")


def fire_daemon_subcommand_async(subcmd: str) -> None:
    """fork-and-forget 一个 `bridge.py <subcmd>`。不等返回，不读输出。
    --ensure / --reinstall 都用这个入口，daemon 那边做幂等。"""
    if not DAEMON_PY.exists():
        log(f"DAEMON_SKIP  script not found: {DAEMON_PY}")
        return
    try:
        subprocess.Popen(
            [sys.executable, str(DAEMON_PY), subcmd],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,   # 脱离 hook 进程组，hook 退出也不影响
        )
        log(f"DAEMON_FIRED  cmd={subcmd}")
    except Exception as e:
        log(f"DAEMON_FAIL   cmd={subcmd}  err={e}")


def daemon_is_stale() -> bool:
    """探测当前在跑的 daemon 是否还是磁盘上 bridge.py 的版本。
    daemon 启动时把自身源码 mtime 写到 VERSION_FILE；hook 比对当前磁盘文件的 mtime
    和 VERSION_FILE 内容，不一致 = daemon 用的是旧代码（python 不会自动重读源文件）。

    VERSION_FILE 不存在 = 老版本 daemon 不带版本机制 → 也判为 stale 触发升级。
    任何异常都返回 False（避免误判反复 reinstall）。"""
    try:
        recorded = int(VERSION_FILE.read_text().strip())
        current  = int(DAEMON_PY.stat().st_mtime)
        return current != recorded
    except FileNotFoundError:
        return True   # 老 daemon 没有版本文件
    except Exception:
        return False


def main() -> None:
    event = sys.argv[1].lower() if len(sys.argv) > 1 else "unknown"
    cmd = EVENT_TO_CMD.get(event)
    if cmd is None:
        log(f"WARN        unknown event: {event!r}")
        return
    log(f"EVENT       {event} → {cmd}")

    # 每次 hook 触发都顺手更新一下"当前 Claude Code 在哪个终端"
    # → 之后按设备按钮时 KeyReturn.app 会 activate 这个终端再发回车
    record_target_app()

    # 多窗口精确定位：根据事件给当前 Claude Code 所在的终端窗口设/清标题标记
    # permission_request → 设 WAIT_MARK：等用户按按钮，按钮按下时 AX 找带标记的窗口切过去
    # user_prompt / stop → 清标记：用户已开始新交互 / Claude 已完成输出，不再等
    # 同时维护 WAITING_MARKER 文件，让下面的 notification 抑制逻辑知道"现在在等权限"
    if event == "permission_request":
        try: WAITING_MARKER.touch()
        except Exception as e: log(f"MARKER_FAIL  touch  err={e}")
        mark_terminal_window(True)
    elif event in ("user_prompt", "stop"):
        try: WAITING_MARKER.unlink()
        except FileNotFoundError: pass
        except Exception as e: log(f"MARKER_FAIL  unlink  err={e}")
        mark_terminal_window(False)

    # 权限弹窗等待中收到 notification（Claude Code 的 idle reminder）→ 直接跳过
    # 不发 D，否则屏幕从紧急的 N 切到温和的 D，用户会误以为 Claude 已经结束了
    if event == "notification" and WAITING_MARKER.exists():
        log("SKIP        notification ignored (waiting for permission)")
        return

    if send_via_socket(cmd):
        # 主路径走通了。顺手做一次版本检测：daemon 用的代码比磁盘上的旧
        # → 异步触发 reinstall 让 launchd 拉起新版（本次不受影响，下次 hook 走的就是新版）
        if daemon_is_stale():
            log("DAEMON_STALE  triggering reinstall")
            fire_daemon_subcommand_async("--reinstall")
        return

    # socket 不通：异步触发 ensure（装/起 daemon），本次走 fallback 串口
    fire_daemon_subcommand_async("--ensure")
    send_via_serial(cmd)


if __name__ == "__main__":
    main()
