# bridge daemon

Mac 侧常驻进程，把 ESP32 串口和 Claude Code hook 桥接起来。**让设备按钮能在 Claude Code 的权限弹窗上按"回车 = Yes"**。

## 工作原理

ESP32-C3 的 USB 是专用外设，不支持 HID，所以"按设备按钮直接当键盘"做不到。绕路：

```
设备按钮 → 串口 "PRESS\n" → bridge.py → osascript 模拟回车 → 前台窗口
hook → unix socket → bridge.py → 串口 → 设备
```

bridge.py 独占串口，正反两个方向都走它，避免端口竞争。

## 安装：什么都不用做

第一次 Claude Code 触发 hook 时，hook 会自动调 `bridge.py --ensure`，自动生成 plist + launchctl 加载启动 daemon。**只有一次手动步骤**：

> [!warning] 第一次按设备按钮后，macOS 会弹"辅助功能"权限请求
> 弹窗内容："osascript 想要控制此电脑（使用辅助功能）"。点 **打开"系统设置"** → 把列表里的 **osascript** 勾上。**这步必须手动做**，macOS 不允许程序绕过。
>
> 授权完成后再按一次设备按钮，前台窗口就会收到回车。

## 工作验证

```bash
# 看 daemon 是否在跑
launchctl list | grep com.xiaoke.bridge

# 看 daemon 日志（看到 BRIDGE_START / SERIAL_OPEN / SOCKET_LISTEN 三行就 OK）
tail -f "/Users/milu/Documents/Code Local/兴趣项目开发/小克物理状态机/xiaoke-local-plugin/daemon/bridge.log"

# 按一下设备按钮，日志会出 SERIAL_RECV line='PRESS' + KEYSTROKE_RETURN
```

## 手动操作（一般用不到）

```bash
# 显式安装
/opt/anaconda3/envs/claude-device/bin/python3 \
  "/Users/milu/Documents/Code Local/兴趣项目开发/小克物理状态机/xiaoke-local-plugin/daemon/bridge.py" --install

# 显式检查并自愈
... bridge.py --ensure

# 卸载
... bridge.py --uninstall
```

`--install` 会重新生成 `~/Library/LaunchAgents/com.xiaoke.bridge.plist`，路径根据 bridge.py 自身位置自动算（搬目录后重新跑一次 `--install` 即可）。

## 调试

```bash
# daemon 没起来 / 行为异常时，手动跑前台版看实时输出
/opt/anaconda3/envs/claude-device/bin/python3 \
  "/Users/milu/Documents/Code Local/兴趣项目开发/小克物理状态机/xiaoke-local-plugin/daemon/bridge.py"

# 不连设备直接测 socket（确认 daemon 正确响应字符）
echo -n "N" | nc -U /tmp/xiaoke-bridge.sock
# 应该屏幕切到 N、电磁铁咔咔
```

## 已知行为

> [!note]- 回车发给当前焦点窗口
> 按按钮时焦点在哪个 app，回车就发给哪个。日常用没事，但要注意：聊天软件里按按钮 = 发送消息。

> [!note]- 设备拔了重插会自动续上
> bridge 1 秒重试一次串口，串口名变了也无所谓（每次重连都 glob 扫）。日志里能看到 `SERIAL_OPEN_FAIL` 和后续的 `SERIAL_OPEN`。

> [!note]- daemon 进程异常退出 launchd 会自动拉起
> plist 配了 `KeepAlive=true`。如果反复重启说明启动就崩，看 `bridge.stderr.log`。

> [!tip]- 第一次触发 hook 时 daemon 还没起，那次走 fallback
> hook 探测到 socket 不存在 → 异步 fork `bridge.py --ensure`（不阻塞 hook）→ 本次走 fallback 直接写串口。下次 hook 触发时 daemon 已经在跑了，自动切到 socket 路径。所以第一次 hook 的体验不会因为"还没装好 daemon"而异常。
