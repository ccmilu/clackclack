# 小克物理状态机 — Claude Code 插件

把 Claude Code 的运行状态映射到桌面 ESP32 装置：6 状态的像素螃蟹动画、电磁铁推杆节奏、蜂鸣器音效、微动开关交互。

## 这个插件做什么

插件通过 Claude Code hooks 监听以下事件，把每个事件转成一个单字符指令通过串口发给 ESP32：

| Claude Code 事件 | 单字符指令 | ESP32 状态 |
|------------------|-----------|-----------|
| `UserPromptSubmit`（用户提问） | `T` | 思考（黄底螃蟹） |
| `PreToolUse` matcher: `Edit\|Write\|MultiEdit` | `W` | 写代码（蓝底敲键盘） |
| `Stop`（Claude 输出停止） | `I` | 空闲（浅灰打盹） |
| `Notification`（弹窗等用户输入） | `D` | 完成（蹦一下提醒"该你了"） |

> 报错状态 E 暂未自动触发，需手动或自行扩展 PostToolUse hook。

## 安装

### 前提

1. **Python 3** 已安装（`python3 --version` 能跑）
2. **pyserial**（如果要真发指令到 ESP32）：
   ```bash
   pip3 install pyserial
   ```
3. ESP32 已烧本项目的 `claude_status.ino` 固件并接好硬件（详见上层项目 README）

### 三种安装方式

**方式 A：直接复制到本地插件目录（最简单）**
```bash
cp -r plugin ~/.claude/plugins/xiaoke-physical-statemachine
```
Claude Code 下次启动会自动加载。

**方式 B：本地开发用 symlink（改源码立即生效）**
```bash
ln -s "$(pwd)/plugin" ~/.claude/plugins/xiaoke-physical-statemachine
```

**方式 C：从 git 仓库安装（推荐分享给别人时用）**
```bash
/plugin install https://github.com/your-username/xiaoke-physical-statemachine --scope user
```

## 启用 / 验证

### 干跑模式（验证 hook 是否被触发）

**不接 ESP32 也能用**——hook 触发后只写日志不发串口。

1. 启动 Claude Code，正常使用
2. 看日志文件：
   ```bash
   tail -f ~/.claude/plugins/xiaoke-physical-statemachine/hooks/events.log
   ```
3. 应该看到类似：
   ```
   [2026-05-24 14:32:01] EVENT    user_prompt → T
   [2026-05-24 14:32:01] DRY_RUN  cmd=T  (未设置 CLAUDE_DEVICE_PORT)
   ```

### 活模式（真发指令到 ESP32）

设置环境变量后再启动 Claude Code：

```bash
export CLAUDE_DEVICE_PORT=/dev/cu.usbmodem1201   # 替换成你 ls /dev/cu.* 看到的实际路径
claude  # 重新启动 Claude Code
```

设了之后 hook 会真的开串口发指令。

要持久化（每次终端启动自动 export），加到 `~/.zshrc` 或 `~/.bash_profile`。

要关闭：`unset CLAUDE_DEVICE_PORT`。

## 用 conda / 自定义 Python 环境

`hooks/hooks.json` 里的 command 默认调用 `python3`。如果你的 pyserial 装在某个 conda 环境里、系统 `python3` 找不到：

**改 `plugin/hooks/hooks.json`**，把所有 `python3` 替换成你的 Python 绝对路径，例如：
```json
"command": "<你的 conda python 绝对路径> \"${CLAUDE_PLUGIN_ROOT}/hooks/physical-status.py\" notification"
```

绝对路径可以这样查：在能用的终端里跑 `conda activate <你的环境> && which python`。

或者**在系统 Python 装 pyserial** 让 `python3` 能用：
```bash
pip3 install pyserial
```

## 自定义事件映射

改 `hooks/physical-status.py` 里的 `EVENT_TO_CMD` 字典即可，例如想让 `notification` 触发紧急通知（N）而不是完成（D）：

```python
EVENT_TO_CMD = {
    "user_prompt":    "T",
    "pre_write_tool": "W",
    "notification":   "N",   # ← 改这里
    "stop":           "I",
}
```

## 卸载

```bash
rm -rf ~/.claude/plugins/xiaoke-physical-statemachine
unset CLAUDE_DEVICE_PORT
```

下次启动 Claude Code 时插件就消失了。

## 调试

- **没看到事件日志** → 检查 hook 是否被注册：Claude Code 启动时会扫描 `~/.claude/plugins/`，命令行运行 `claude` 时观察输出有没有报错
- **看到事件日志但 ESP32 没反应** → 检查 `CLAUDE_DEVICE_PORT` 是否正确（`ls /dev/cu.*` 看实际端口名）
- **ERROR 行带串口错误** → 端口被其他程序占用（Arduino IDE 串口监视器、test_device.py 等），关掉它们
- **PreToolUse 触发太频繁** → 把 `hooks.json` 里的 `matcher` 改窄（比如只 `Edit`）

## 关联文档

- 主项目 README：上层目录的 `README.md`
- ESP32 固件源码：上层目录的 `Arduino/claude_status/`
- 硬件清单 + 接线表：见上层 `README.md`
