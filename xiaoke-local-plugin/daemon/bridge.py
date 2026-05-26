#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Mac ↔ ESP32 桥接 daemon
==========================
长驻进程。一端独占 ESP32 串口；另一端在 unix socket 上接收 hook 发的状态字符。

子命令
------
  bridge.py             daemon 模式（默认，launchd 拉起后跑这个）
  bridge.py --install   生成 launchd plist 并加载启动（首次安装）
  bridge.py --ensure    探测 daemon 是否在跑，没在跑就自动 install / kickstart
                        （由 hook 异步触发，实现"开箱即用"）
  bridge.py --uninstall 卸载 launchd plist 并停止 daemon

为什么需要 daemon
------------------
ESP32-C3 硬件 USB 是 USB-Serial-JTAG 专用外设，做不到 USB HID 键盘。
所以"按设备按钮 = Mac 收到回车"必须靠 Mac 这边的常驻进程把串口的 PRESS 翻译成
模拟按键事件。既然要常驻，干脆把发指令的方向也接管——hook 不再直接开串口，
而是把字符发到 daemon 的 unix socket，daemon 统一写串口，避免端口被多个进程抢。

数据流
------
反向（设备 → Mac）：
    ESP32 按钮 → 串口 "PRESS\\n" → bridge 读到 → osascript 模拟回车
                                            → 前台窗口（Claude Code）收到 ↵
正向（Mac → 设备）：
    Claude Code hook → physical-status.py → unix socket → bridge 转发 → 串口

依赖：pyserial（仅 daemon 模式需要，install/ensure 不需要）
"""
import glob
import os
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path


# ============ 配置 ============
LABEL        = "com.xiaoke.bridge"
SOCKET_PATH  = "/tmp/xiaoke-bridge.sock"
# 版本指纹文件：daemon 启动时写入自身源码 mtime，hook 读它和当前 bridge.py 的 mtime 比对，
# 不一致就说明 daemon 在跑旧代码 → hook 异步触发 --reinstall 让 launchd 拉起新版。
# 这是"修代码自动生效"的关键机制——避免每次改完手动 bootout/bootstrap。
VERSION_FILE = Path("/tmp/xiaoke-bridge.version")
BAUD_RATE    = 115200
RECONNECT_DELAY_S = 1.0

PRESS_TOKEN = "PRESS"   # ESP32 固件按下时 println("PRESS")，识别后模拟回车

SCRIPT_DIR        = Path(__file__).resolve().parent
LOG_FILE          = SCRIPT_DIR / "bridge.log"
LAUNCH_AGENT_DIR  = Path.home() / "Library" / "LaunchAgents"
PLIST_PATH        = LAUNCH_AGENT_DIR / f"{LABEL}.plist"

# Helper .app：预编译的 JXA（JavaScript for Automation）脚本
# 用 JXA 而不是 AppleScript：JXA 能调 ObjC bridge，可以主动调用
# AXIsProcessTrustedWithOptions(prompt: true) 让 macOS 弹"KeyReturn 想要使用辅助功能"
# 对话框 + "打开系统设置"按钮——AppleScript 没有这个能力。
HELPER_APP        = SCRIPT_DIR / "KeyReturn.app"
HELPER_LANG       = "JavaScript"   # osacompile -l 参数

# 常驻模式相关文件：
#   PID 文件：daemon 用它判断 KeyReturn.app 是否还在跑（活着就不重启）
#   触发文件：daemon 写这个文件来让常驻的 KeyReturn.app 执行一次按回车
# 这种"常驻 + 文件触发"避免每次按按钮都重启 .app（osacompile applet 启动要 200-500ms）
HELPER_PID_FILE   = Path("/tmp/xiaoke-keyreturn.pid")
HELPER_TRIGGER    = Path("/tmp/xiaoke-keyreturn.trigger")
# 设计：常驻模式
# .app 启动后进入死循环 polling 触发文件 /tmp/xiaoke-keyreturn.trigger
# daemon 收到 PRESS 时只写一下这个文件 → .app 立刻执行按回车，无需重启 .app
# 启动开销（osacompile JXA runtime ~300ms）只付一次，之后按按钮接近瞬时响应
#
# 一次性初始化（启动时一次）：
#   - 写 PID 文件 /tmp/xiaoke-keyreturn.pid（daemon 用它判断 .app 还活着不）
#   - 主动检查辅助功能权限（prompt: true 让 macOS 弹首次授权请求）
#
# 每次触发的工作（被主循环 polling 到 trigger 文件后执行）：
#   3. 记下当前 frontmost app 的 PID + name（切回的依据）
#   4. 读 /tmp/xiaoke-target-app.txt 拿目标终端 app → activate
#   5. 多窗口精确切：找标题带 "🔴 CLAUDE_WAIT" 的窗口 AXRaise
#   6. 发回车 → 用 NSRunningApplication 把原 app 切回前台（跨桌面可靠）
# 全程 try/catch 吞错误，不弹烦人的 JS 错误对话框
HELPER_SCRIPT     = """
ObjC.import('Foundation');
ObjC.import('AppKit');
ObjC.import('ApplicationServices');

const TRIGGER_PATH = "/tmp/xiaoke-keyreturn.trigger";
const PID_PATH     = "/tmp/xiaoke-keyreturn.pid";
const fm           = $.NSFileManager.defaultManager;

function initOnce() {
    // 写 PID 让 daemon 知道这个常驻进程的 PID（用于活性检查）
    try {
        const pid = $.NSProcessInfo.processInfo.processIdentifier;
        $.NSString.stringWithString(String(pid))
            .writeToFileAtomicallyEncodingError(PID_PATH, true, $.NSUTF8StringEncoding, $());
    } catch (e) {}

    // 检查辅助功能权限（prompt: true 触发首次授权对话框）
    try {
        const opts = $.NSMutableDictionary.alloc.init;
        opts.setValueForKey(true, "AXTrustedCheckOptionPrompt");
        $.AXIsProcessTrustedWithOptions(opts);
    } catch (e) {}
}

function doKeystroke() {
    const SE = Application("System Events");

    // 第二步：记下当前 frontmost app 的 PID + name
    // PID 用于切回时走 NSRunningApplication（AppKit 层，跨桌面可靠）；
    // name 仅用于判断"自切自"避免无谓折腾
    let prevPID = -1;
    let prevAppName = "";
    try {
        const frontProcs = SE.processes.whose({frontmost: true});
        if (frontProcs.length > 0) {
            prevPID = frontProcs[0].unixId();
            prevAppName = frontProcs[0].name();
        }
    } catch (e) {}

    // 第三步：读目标终端 app 名（由 hook 每次触发时写入 /tmp 文件）
    let target = "";
    try {
        const ns = $.NSString.stringWithContentsOfFileEncodingError(
            "/tmp/xiaoke-target-app.txt", $.NSUTF8StringEncoding, $());
        if (ns && !ns.isNil()) {
            target = ns.js.trim();
        }
    } catch (e) {}

    // 第四步：activate 目标终端（切到前台 / 跨桌面切到目标桌面）
    const needSwitch = (target && target !== prevAppName);
    if (needSwitch) {
        try {
            Application(target).activate();
            delay(0.25);   // 跨桌面切换要更多时间，否则 keystroke 可能发到旧桌面的窗口

            // 多窗口精确定位：枚举目标 app 的所有 windows，找标题带 WAIT_MARK 的
            // （由 hook 在 permission_request 时通过 OSC 写到对应 tty 的终端窗口标题）
            // AXRaise 把那个窗口顶到前台。这样即使 app 内开了多个窗口也能切对。
            try {
                const proc = SE.processes.byName(target);
                const wins = proc.windows;
                const wcount = wins.length;
                for (let i = 0; i < wcount; i++) {
                    let title = "";
                    try { title = wins[i].name(); } catch (e) {}
                    if (title && title.indexOf("🔴 CLAUDE_WAIT") >= 0) {
                        try {
                            // AXRaise：把这个窗口顶到 app 内最前
                            wins[i].actions.byName("AXRaise").perform();
                            delay(0.1);
                        } catch (e) {}
                        break;
                    }
                }
            } catch (e) {}
        } catch (e) {}
    }

    // 第五步：发回车给当前 frontmost 进程（此时应是目标终端）
    try {
        SE.keystroke("\\r");
    } catch (e) {}

    // 第六步：用 AppKit NSRunningApplication 把原 app 切回前台
    // 关键：AppleEvent 的 activate 在跨 Spaces（多桌面）时常被 macOS 忽略；
    // NSRunningApplication.activateWithOptions 走 AppKit 层，跨桌面切回稳定
    if (needSwitch && prevPID > 0) {
        delay(0.15);   // 给 Claude Code 处理回车一点时间，否则桌面切换可能截胡按键
        try {
            const prevApp = $.NSRunningApplication.runningApplicationWithProcessIdentifier(prevPID);
            if (prevApp && !prevApp.isNil()) {
                // 1 = NSApplicationActivateAllWindows
                // 2 = NSApplicationActivateIgnoringOtherApps
                // 用 3 = 两个 flag 都设，跨桌面切回更稳
                prevApp.activateWithOptions(3);
            }
        } catch (e) {}
    }
}

function run() {
    initOnce();
    // 主循环：50ms polling 触发文件。文件存在 → 删 + 执行 doKeystroke()
    // polling 间隔 50ms 平均增加 ~25ms 延迟，但省了每次重启 .app 的 200-500ms 启动开销
    while (true) {
        try {
            if (fm.fileExistsAtPath(TRIGGER_PATH)) {
                // 先删触发文件再执行，避免 doKeystroke 期间被重复触发
                fm.removeItemAtPathError(TRIGGER_PATH, $());
                doKeystroke();
            }
        } catch (e) {}
        delay(0.05);
    }
}
"""


# ============ 日志（永不抛异常） ============
def log(msg: str) -> None:
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass


# ============ install / ensure / uninstall ============
PLIST_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<!-- 由 bridge.py --install 自动生成，请勿手动编辑（重新运行 --install 会覆盖） -->
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>

    <key>ProgramArguments</key>
    <array>
        <string>{python}</string>
        <string>{bridge_py}</string>
    </array>

    <key>WorkingDirectory</key>
    <string>{work_dir}</string>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <true/>

    <key>ProcessType</key>
    <string>Background</string>

    <key>StandardOutPath</key>
    <string>{stdout}</string>

    <key>StandardErrorPath</key>
    <string>{stderr}</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/bin:/bin:/usr/sbin:/sbin</string>
    </dict>
</dict>
</plist>
"""


def find_python_with_pyserial() -> str:
    """挑一个真的能 import serial 的 python。优先 conda env，fallback 当前解释器。
    daemon 模式必须有 pyserial，install 时就把路径写死进 plist，免得 launchd 启动时
    踩到没装 pyserial 的系统 python。"""
    candidates = [
        "/opt/anaconda3/envs/claude-device/bin/python3",
        "/opt/anaconda3/envs/claude-device/bin/python",
        sys.executable,   # 当前跑这个脚本的 python（兜底）
    ]
    for py in candidates:
        if not py or not Path(py).exists():
            continue
        try:
            r = subprocess.run(
                [py, "-c", "import serial"],
                capture_output=True, timeout=5,
            )
            if r.returncode == 0:
                return py
        except Exception:
            continue
    # 真找不到也返回 sys.executable，让用户看日志报错
    return sys.executable


def generate_plist_content() -> str:
    bridge_py = Path(__file__).resolve()
    work_dir  = bridge_py.parent
    python    = find_python_with_pyserial()
    return PLIST_TEMPLATE.format(
        label     = LABEL,
        python    = python,
        bridge_py = str(bridge_py),
        work_dir  = str(work_dir),
        stdout    = str(work_dir / "bridge.stdout.log"),
        stderr    = str(work_dir / "bridge.stderr.log"),
    )


def launchctl(*args) -> subprocess.CompletedProcess:
    """统一封装：不抛异常，返回 CompletedProcess。"""
    return subprocess.run(
        ["launchctl", *args],
        check=False, capture_output=True, text=True, timeout=10,
    )


def is_daemon_running() -> bool:
    """daemon 跑着的时候 socket 文件存在且能 connect 上。"""
    if not os.path.exists(SOCKET_PATH):
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(0.3)
            s.connect(SOCKET_PATH)
        return True
    except Exception:
        return False


def cmd_install() -> int:
    """生成 plist + 加载启动。已存在的会被覆盖+重新加载。"""
    # 顺便保证 helper app 编译好（首次按按钮就能用 KeyReturn.app 而不是 osascript）
    ensure_helper_app()

    print(f"[install] writing {PLIST_PATH}")
    LAUNCH_AGENT_DIR.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.write_text(generate_plist_content(), encoding="utf-8")

    uid = os.getuid()
    # 先 bootout 移除旧的（如果有）。失败说明本来就没装过，忽略。
    launchctl("bootout", f"gui/{uid}", str(PLIST_PATH))
    # 再 bootstrap 加载
    r = launchctl("bootstrap", f"gui/{uid}", str(PLIST_PATH))
    if r.returncode != 0:
        # 老 macOS fallback：load -w
        print(f"[install] bootstrap failed ({r.stderr.strip()}), trying legacy load")
        r2 = launchctl("load", "-w", str(PLIST_PATH))
        if r2.returncode != 0:
            print(f"[install] FAILED: {r2.stderr.strip()}")
            log(f"INSTALL_FAIL  bootstrap_err={r.stderr.strip()}  load_err={r2.stderr.strip()}")
            return 1

    log(f"INSTALL_OK  plist={PLIST_PATH}")
    print(f"[install] daemon loaded as {LABEL}")
    print("[install] 首次按设备按钮时 macOS 会弹辅助功能权限请求，记得授权 osascript")
    return 0


def cmd_reinstall() -> int:
    """强制重装：先 bootout 当前 daemon 让它退出，再走完整的 install 流程拉起新版。
    用于代码更新后让 launchd 加载新版本（python 已经加载到内存的代码不会自动刷新，必须重启进程）。
    幂等：daemon 没在跑就直接 install。"""
    uid = os.getuid()
    log("REINSTALL_TRIGGERED")
    if PLIST_PATH.exists():
        launchctl("bootout", f"gui/{uid}", str(PLIST_PATH))
    # 等一下让旧进程真正退出（bootout 是异步的，进程退出需要点时间）
    time.sleep(0.5)
    return cmd_install()


def cmd_uninstall() -> int:
    uid = os.getuid()
    launchctl("bootout", f"gui/{uid}", str(PLIST_PATH))
    launchctl("unload", "-w", str(PLIST_PATH))   # 兜底
    if PLIST_PATH.exists():
        PLIST_PATH.unlink()
    log("UNINSTALL_OK")
    print(f"[uninstall] removed {PLIST_PATH}")
    return 0


def cmd_ensure() -> int:
    """hook 异步触发的自愈入口。daemon 已在跑就立刻退出，否则装/起。
    设计成幂等：多次并发调用最坏只是多跑几次 launchctl，没副作用。"""
    if is_daemon_running():
        return 0
    log("ENSURE_TRIGGERED")
    if not PLIST_PATH.exists():
        log("ENSURE: plist missing, running install")
        return cmd_install()
    # plist 装了但 daemon 没跑：可能 launchctl 没加载，或加载了但崩了
    uid = os.getuid()
    # 先 kickstart 尝试拉起来
    r = launchctl("kickstart", "-k", f"gui/{uid}/{LABEL}")
    if r.returncode == 0:
        log("ENSURE: kickstart OK")
        return 0
    # kickstart 失败说明根本没加载 → 重新 install
    log(f"ENSURE: kickstart failed ({r.stderr.strip()}), running install")
    return cmd_install()


# ============ daemon 模式实现 ============
def auto_detect_port() -> str:
    """扫描 /dev/cu.usbmodem* 取第一个。ESP32-C3 SuperMini 拔插后串口名数字会变。"""
    candidates = sorted(glob.glob("/dev/cu.usbmodem*") + glob.glob("/dev/cu.usbserial*"))
    return candidates[0] if candidates else ""


class SerialManager:
    """自动重连 + 线程安全的写。"""

    def __init__(self):
        import serial   # 延迟到 daemon 模式才 import，install/ensure 不要求装
        self._serial_mod = serial
        self.ser = None
        self.write_lock = threading.Lock()

    def try_open(self) -> bool:
        port = os.environ.get("CLAUDE_DEVICE_PORT", "").strip() or auto_detect_port()
        if not port:
            return False
        try:
            self.ser = self._serial_mod.Serial(port, BAUD_RATE, timeout=1)
            log(f"SERIAL_OPEN  port={port}")
            return True
        except Exception as e:
            log(f"SERIAL_OPEN_FAIL  err={e}")
            return False

    def close(self):
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass
            self.ser = None

    def is_open(self) -> bool:
        return self.ser is not None and self.ser.is_open

    def write_char(self, c: str) -> bool:
        if not self.is_open():
            return False
        try:
            with self.write_lock:
                self.ser.write(c.encode("ascii"))
            log(f"SERIAL_WRITE  cmd={c}")
            return True
        except Exception as e:
            log(f"SERIAL_WRITE_FAIL  cmd={c}  err={e}")
            return False

    def readline_safe(self) -> str:
        if not self.is_open():
            raise IOError("serial not open")
        line = self.ser.readline()
        return line.decode("ascii", errors="replace").strip()


def _helper_script_hash() -> str:
    """HELPER_SCRIPT 的短 hash，作为 .app 的"版本指纹"。"""
    import hashlib
    return hashlib.sha256(HELPER_SCRIPT.encode("utf-8")).hexdigest()[:16]


def ensure_helper_app() -> bool:
    """编译一个最小化 .app：功能只有一件事——按回车。
    macOS TCC 把辅助功能权限按"调用方"归属，子进程的归属会算到父进程 Python；
    但走 `open -a` 启动独立 .app bundle 时，发起 AppleEvent 的是这个 .app 自己，
    TCC 把权限归到 .app 而不是 Python——这就把授权范围缩到了"只能按回车"。

    幂等 + 稳定签名：
    - .app 不存在 → 重建（首次安装）
    - .app 存在且 :XiaokeScriptHash 字段 = 当前 HELPER_SCRIPT 的 hash → 跳过
      （avoid 频繁重建导致 ad-hoc 签名 hash 变化让 TCC 授权失效，需要用户反复重新拖授权）
    - .app 存在但脚本指纹对不上 → 重建（HELPER_SCRIPT 改过，必须重新编）"""
    info_plist = HELPER_APP / "Contents" / "Info.plist"
    current_hash = _helper_script_hash()

    if HELPER_APP.exists():
        # 读 .app 里记录的脚本指纹，匹配就跳过重建（保持签名稳定，授权不失效）
        r = subprocess.run(
            ["/usr/libexec/PlistBuddy", "-c", "Print :XiaokeScriptHash", str(info_plist)],
            check=False, capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0 and r.stdout.strip() == current_hash:
            return True
        # 指纹对不上：脚本改过，得重建（删干净再编）
        import shutil
        shutil.rmtree(HELPER_APP, ignore_errors=True)
        log("HELPER_APP_REBUILD  reason=script_hash_changed")

    try:
        # -l JavaScript：编译成 JXA（JavaScript for Automation）而非默认 AppleScript
        # 这样 .app 内的 main.scpt 用 JS 写，可以调 ObjC bridge 触发辅助功能权限请求对话框
        r = subprocess.run(
            ["osacompile", "-l", HELPER_LANG, "-o", str(HELPER_APP), "-e", HELPER_SCRIPT],
            check=False, capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0:
            log(f"OSACOMPILE_FAIL  err={r.stderr.strip()}")
            return False

        # 加 LSUIElement：让 .app 启动时不出现在 Dock（否则每次按按钮 Dock 都会闪一下）
        subprocess.run(
            ["/usr/libexec/PlistBuddy", "-c", "Add :LSUIElement bool true", str(info_plist)],
            check=False, capture_output=True, timeout=5,
        )

        # 加 NSAppleEventsUsageDescription：macOS 弹"自动化授权"对话框的前提
        # 没这个字段，AppleEvent 发给 System Events 会被静默拒绝（errAEEventNotPermitted / -1743）
        # 这条描述会出现在系统设置 → 隐私与安全性 → 自动化里
        subprocess.run(
            ["/usr/libexec/PlistBuddy", "-c",
             'Add :NSAppleEventsUsageDescription string '
             '"小克物理状态机：按下设备按钮时模拟回车键，用于在 Claude Code 权限弹窗中点击确认。"',
             str(info_plist)],
            check=False, capture_output=True, timeout=5,
        )

        # 改 bundle 关键字段：
        #   CFBundleName / CFBundleDisplayName  → 系统设置 → 隐私 → 辅助功能/自动化 列表的显示名
        #   CFBundleIdentifier                  → 一个稳定可控的 bundle ID，
        #                                         便于将来 `tccutil reset Accessibility com.xiaoke.KeyReturn` 精确清缓存
        # 用"先 Delete 再 Add"的幂等模式：PlistBuddy 的 Set 只能改已存在字段，
        # 之前用 Set 设置 CFBundleIdentifier 静默失败（因为 osacompile 默认 plist 没有该字段）
        for key, value in [
            ("CFBundleName",         "KeyReturn"),
            ("CFBundleDisplayName",  "KeyReturn"),
            ("CFBundleIdentifier",   "com.xiaoke.KeyReturn"),
            # 自定义字段：脚本内容的 hash。下次启动时对比这个值决定要不要重建 .app
            ("XiaokeScriptHash",     _helper_script_hash()),
        ]:
            subprocess.run(
                ["/usr/libexec/PlistBuddy", "-c", f"Delete :{key}", str(info_plist)],
                check=False, capture_output=True, timeout=5,
            )
            subprocess.run(
                ["/usr/libexec/PlistBuddy", "-c", f"Add :{key} string {value}", str(info_plist)],
                check=False, capture_output=True, timeout=5,
            )

        # ad-hoc 重新签名：改了 Info.plist 后必须重新签，否则 macOS 会因 bundle 完整性
        # 校验失败而仍然拒绝 AppleEvent
        subprocess.run(
            ["codesign", "--force", "--deep", "--sign", "-", str(HELPER_APP)],
            check=False, capture_output=True, timeout=10,
        )

        log(f"HELPER_APP_OK  path={HELPER_APP}")
        return True
    except Exception as e:
        log(f"OSACOMPILE_FAIL  err={e}")
        return False


def is_keyreturn_alive() -> bool:
    """检查常驻 KeyReturn.app 进程是否还活着（PID 文件 + os.kill 探针）。"""
    if not HELPER_PID_FILE.exists():
        return False
    try:
        pid = int(HELPER_PID_FILE.read_text().strip())
        os.kill(pid, 0)   # 不发信号，只检查进程存在性
        return True
    except (ProcessLookupError, ValueError, PermissionError):
        return False
    except Exception:
        return False


def ensure_keyreturn_running() -> None:
    """保证常驻 KeyReturn.app 进程在跑。不在就 open -g -a 启动它。
    .app 启动后会自己写 PID 文件 + 进入死循环 polling 触发文件。
    幂等：已经在跑就不做任何事（避免反复启动多实例）。"""
    if is_keyreturn_alive():
        return
    if not HELPER_APP.exists() and not ensure_helper_app():
        log("KEYRETURN_LAUNCH_SKIP  helper app missing and compile failed")
        return
    try:
        # -g background：不抢前台焦点；-j junk：不在 Dock 显示（配合 LSUIElement）
        subprocess.run(
            ["open", "-g", "-a", str(HELPER_APP)],
            check=False, capture_output=True, timeout=2,
        )
        log(f"KEYRETURN_LAUNCH  path={HELPER_APP}")
    except Exception as e:
        log(f"KEYRETURN_LAUNCH_FAIL  err={e}")


def simulate_return_key():
    """触发常驻 KeyReturn.app 执行一次按回车。
    新方案（常驻 + 文件触发）：只写 trigger 文件，常驻的 .app polling 到后立刻执行，
    省去每次 open -a 重启 .app 的 200-500ms 启动开销。

    流程：
    1) 保证常驻 .app 在跑（不在就启动，正常情况下 daemon 启动时已经起来了）
    2) 写一下 trigger 文件 → .app 在 50ms 内 polling 到 → 执行 keystroke 逻辑
    """
    ensure_keyreturn_running()
    try:
        HELPER_TRIGGER.touch()
        log("KEYSTROKE_TRIGGER  via=resident_helper")
    except Exception as e:
        log(f"KEYSTROKE_TRIGGER_FAIL  err={e}")


def serial_read_loop(sm: SerialManager, stop_event: threading.Event):
    while not stop_event.is_set():
        if not sm.is_open():
            time.sleep(RECONNECT_DELAY_S)
            continue
        try:
            line = sm.readline_safe()
        except Exception as e:
            log(f"SERIAL_READ_FAIL  err={e}")
            sm.close()
            time.sleep(RECONNECT_DELAY_S)
            continue
        if not line:
            continue   # 1s timeout，正常
        log(f"SERIAL_RECV  line={line!r}")
        if PRESS_TOKEN in line:
            simulate_return_key()


def socket_serve_loop(sm: SerialManager, stop_event: threading.Event):
    try:
        os.unlink(SOCKET_PATH)
    except FileNotFoundError:
        pass

    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.settimeout(0.5)
    try:
        srv.bind(SOCKET_PATH)
        os.chmod(SOCKET_PATH, 0o666)
        srv.listen(8)
        log(f"SOCKET_LISTEN  path={SOCKET_PATH}")
    except Exception as e:
        log(f"SOCKET_BIND_FAIL  err={e}")
        return

    while not stop_event.is_set():
        try:
            conn, _ = srv.accept()
        except socket.timeout:
            continue
        except Exception as e:
            log(f"SOCKET_ACCEPT_FAIL  err={e}")
            continue
        with conn:
            try:
                data = conn.recv(8)
                if data:
                    sm.write_char(chr(data[0]))
            except Exception as e:
                log(f"SOCKET_HANDLE_FAIL  err={e}")

    srv.close()
    try:
        os.unlink(SOCKET_PATH)
    except FileNotFoundError:
        pass


def run_daemon() -> int:
    import signal

    # daemon 启动时保证 helper app 在位（自愈：用户删了 KeyReturn.app 也能自动重建）
    ensure_helper_app()
    # 启动常驻 KeyReturn.app，使其等待 trigger 文件——按按钮时延迟更低
    ensure_keyreturn_running()

    # 写版本指纹：用当前源码 mtime。下次代码改了，hook 读到磁盘 mtime ≠ 指纹文件内容 → 触发 reinstall
    try:
        VERSION_FILE.write_text(str(int(Path(__file__).stat().st_mtime)))
    except Exception as e:
        log(f"VERSION_WRITE_FAIL  err={e}")

    stop_event = threading.Event()

    def handle_signal(signum, _frame):
        log(f"SIGNAL  {signum}")
        stop_event.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        sm = SerialManager()
    except ImportError:
        log("DAEMON_FATAL  pyserial 没装，请装：pip3 install pyserial")
        print("ERROR: 缺 pyserial。装一下：pip3 install pyserial", file=sys.stderr)
        return 1

    t_read = threading.Thread(target=serial_read_loop, args=(sm, stop_event), daemon=True)
    t_sock = threading.Thread(target=socket_serve_loop, args=(sm, stop_event), daemon=True)
    t_read.start()
    t_sock.start()
    log("BRIDGE_START")

    while not stop_event.is_set():
        if not sm.is_open():
            sm.try_open()
            if not sm.is_open():
                time.sleep(RECONNECT_DELAY_S)
                continue
        time.sleep(2)

    sm.close()
    t_read.join(timeout=2)
    t_sock.join(timeout=2)
    log("BRIDGE_STOP")
    return 0


# ============ 入口 ============
def main() -> int:
    if len(sys.argv) > 1:
        sub = sys.argv[1]
        if sub == "--install":   return cmd_install()
        if sub == "--ensure":    return cmd_ensure()
        if sub == "--reinstall": return cmd_reinstall()
        if sub == "--uninstall": return cmd_uninstall()
        print(f"unknown subcommand: {sub}", file=sys.stderr)
        return 2
    return run_daemon()


if __name__ == "__main__":
    sys.exit(main())
