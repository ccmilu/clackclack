# bridge daemon

Mac 侧常驻进程，把 ESP32 串口和 Claude Code hook 桥接起来。**让设备按钮能在 Claude Code 的权限弹窗上按"回车 = Yes"**。

## 工作原理

ESP32-C3 的 USB 是 USB-Serial-JTAG 专用外设，不支持 HID（按钮当不了 USB 键盘），所以绕路：

```
设备按钮 → 串口 "PRESS\n" → bridge.py → KeyReturn.app 模拟回车 → 目标终端窗口
hook    → unix socket   → bridge.py → 串口 → 设备
```

bridge.py 独占串口，正反两个方向都走它，避免端口竞争。

`KeyReturn.app` 是 daemon 启动时用 `osacompile` 编译出来的最小 helper —— 写死只能"按回车"，让 macOS 辅助功能权限范围收窄到这个工具（不归到整个 Python 解释器）。常驻进程 + polling `/tmp/xiaoke-keyreturn.trigger` 文件触发，按按钮延迟 ~535ms。

## 安装：什么都不用做

第一次给 Claude Code 发消息触发 hook 时，hook 会自动调 `bridge.py --ensure`，自动生成 launchd plist + 加载启动 daemon + 编译 KeyReturn.app。**只有一次手动步骤**：

> [!warning] 第一次按设备按钮后，macOS 会弹辅助功能 / 自动化授权请求
> 弹窗内容："KeyReturn 想要使用辅助功能 / 控制 System Events / 控制 <你的终端>"。点 **打开"系统设置"** → 把列表里的 **KeyReturn** 勾上。**这步必须手动做**，macOS 不允许程序绕过。
>
> 授权完成后再按一次设备按钮，目标终端窗口就会收到回车。
>
> 详细授权流程见下面的"权限"章节。

## 工作验证

```bash
# 看 daemon 是否在跑（应输出一行）
launchctl list | grep com.xiaoke.bridge

# 看 daemon 日志（看到 BRIDGE_START / SERIAL_OPEN / SOCKET_LISTEN 三行就 OK）
tail -f "$(dirname "$0")/bridge.log"   # 或者用项目内的实际路径

# 按一下设备按钮，日志会出 SERIAL_RECV line='PRESS' + KEYSTROKE_TRIGGER
```

## 手动操作（一般用不到）

把 `<DAEMON_DIR>` 替换成项目内 `xiaoke-local-plugin/daemon/` 的绝对路径：

```bash
# 显式安装
python3 <DAEMON_DIR>/bridge.py --install

# 显式检查并自愈（探测 daemon 没在跑就装/起）
python3 <DAEMON_DIR>/bridge.py --ensure

# 强制重启（代码更新后）
python3 <DAEMON_DIR>/bridge.py --reinstall

# 卸载
python3 <DAEMON_DIR>/bridge.py --uninstall
```

`--install` 会重新生成 `~/Library/LaunchAgents/com.xiaoke.bridge.plist`，里面的路径根据 bridge.py 自身位置自动算（搬目录后重新跑一次 `--install` 即可）。

bridge.py 启动时会自动找一个装了 `pyserial` 的 python（依次扫：当前解释器、PATH 中的 `python3`、用户家目录下的 miniconda / anaconda、Homebrew 路径），写进 plist。所以你只要在**任一**这些位置上装了 pyserial 都行。

## 调试

```bash
# daemon 没起来 / 行为异常时，手动跑前台版看实时输出
python3 <DAEMON_DIR>/bridge.py

# 不连设备直接测 socket（确认 daemon 正确响应字符）
echo -n "N" | nc -U /tmp/xiaoke-bridge.sock
# 应该屏幕切到 N、电磁铁咔咔

# 直接测 keystroke（绕过设备）
touch /tmp/xiaoke-keyreturn.trigger
# 焦点窗口 50ms 内应收到一个回车
```

## 权限

KeyReturn.app 需要**两类** macOS 权限：

| 权限 | 触发场景 | 如何授权 |
|------|---------|---------|
| **辅助功能**（Accessibility） | 模拟键盘按键 | 第一次按按钮自动弹"打开系统设置"对话框；或手动拖 KeyReturn.app 到 系统设置 → 隐私 → 辅助功能 |
| **自动化**（Automation） | 控制 System Events 和你的终端 app | 第一次调用会自动弹"KeyReturn 想要控制 X"对话框，点"好" |

> [!warning]- 看到列表里 KeyReturn 已勾上但按钮还是没反应？
> macOS TCC 数据库的辅助功能授权记录绑定 **bundle ID + 代码签名 hash**。如果 KeyReturn.app 重新编译过（比如代码更新触发），签名 hash 变了 → TCC 不认旧记录。
>
> 解法：**删掉旧条目**（系统设置 → 辅助功能 → 选中 KeyReturn → 按减号），下次按按钮会让 macOS 重新弹授权请求，勾上新的就行。
>
> 代码层做了"脚本 hash 指纹"机制让 .app 只在真的需要时重建，正常迭代很少触发签名变化。

## 已知行为

> [!note]- 同 app 多窗口能精确切到 Claude Code 等的那个
> hook 在权限请求时通过 OSC 0 给对应窗口标题加 "🔴 CLAUDE_WAIT" 标记，按按钮时 KeyReturn.app 用 macOS AX API 找带标记的窗口 AXRaise。Cmd+Tab 也能直接看到标记。

> [!note]- 同 app 内 split（如 Ghostty 的 Cmd+D 分屏）不支持精确切
> macOS 系统层看不到 app 内部 split 布局。建议在多任务时用 tab（Cmd+T）代替 split。

> [!note]- 设备拔了重插会自动续上
> bridge 1 秒重试一次串口，串口名变了也无所谓（每次重连都 glob 扫）。日志里能看到 `SERIAL_OPEN_FAIL` 和后续的 `SERIAL_OPEN`。

> [!note]- daemon 进程异常退出 launchd 会自动拉起
> plist 配了 `KeepAlive=true`。如果反复重启说明启动就崩，看 `bridge.stderr.log`。

> [!tip]- 跨桌面 / 用完切回原 app
> 按按钮记下当前 frontmost 的 PID，发完回车用 `NSRunningApplication.activateWithOptions` 切回（AppKit 层，跨 Spaces 可靠，AppleEvent activate 跨桌面经常失败）。
