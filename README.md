# 小克物理状态机

一个**软硬件混合的桌面玩具**：把 Claude Code 的运行状态映射成 ESP32 屏幕动画 + 电磁铁推杆 + 蜂鸣器，让 AI 的工作"看得见、听得见、摸得着"。AI 需要批准权限时按一下硬件按钮就能确认（即使焦点在别的窗口、甚至别的桌面）。

```
Mac:  Claude Code hooks → Python 插件 → daemon ──USB──► ESP32 → 屏 / 电磁铁 / 蜂鸣器
                                                ◄──USB── 微动开关
```

## 当前状态

软硬件全部跑通，端到端已可日常使用。Phase 6-7 整合 + Mac 桥接 daemon 完成。**剩外壳打印 + 实际 dogfood 验证**。

## 状态映射

| Claude 事件 | 屏幕 | 电磁铁 | 蜂鸣器 |
|------|------|------|------|
| 用户提交输入 | 🟡 T 思考（黄底螃蟹） | 🔇 | 🔇 |
| AI 编辑代码 | 🔵 W 写代码（蓝底敲键盘） | 🔇 | 🔇 |
| AI 等用户批准 | 🟠 N 紧急召唤（橙底感叹号） | 咔咔咔 咔—— | 叮叮叮 咚~~ |
| AI 输出完成 | 🟢 D 完成（绿底花朵） | 咔！咔！ | 马里奥金币音 |
| 6 秒后超时 | ⚪ I 空闲（浅灰打盹） | 🔇 | 🔇 |

按硬件按钮：
- **短按**：自动切到 Claude Code 终端窗口 + 发回车（确认 N 紧急召唤的权限弹窗）
- **长按 ≥800ms**：静音切换（屏右下角显示灰色喇叭图标）

## 快速开始

> [!note]- 硬件清单（人民币约 80）
> | 模块 | 用途 | 价位 |
> |------|------|------|
> | ESP32-C3 SuperMini | 主控 | ~15 |
> | 0.96" ST7735 TFT 屏 80×160 | 状态画面 | ~15 |
> | IRF540N 光耦隔离 MOS 模块 | 驱动电磁铁 | ~10 |
> | KK-0530B 5V 推拉电磁铁 | 物理提醒 | ~15 |
> | 低电平触发 3 线无源蜂鸣器 | 声音提醒 | ~5 |
> | 4 脚 6×6mm 微动开关 | 用户按钮 | ~1 |
> | 杜邦线 / 面包板 | 接线 | ~10 |

### 1. 烧固件到 ESP32

```bash
# Arduino IDE 打开 Arduino/claude_status/claude_status.ino
# Tools 配置：
#   Board:            ESP32C3 Dev Module
#   USB CDC On Boot:  Enabled
#   Partition Scheme: Huge APP (3MB No OTA/1MB SPIFFS)   # 默认 1.25MB 装不下
# 点上传
```

固件已包含 6 个状态动画的像素数据（`crab_data.h`），不需要重新生成。

> [!tip]- 如果你想改 SVG 自己生成动画数据
> ```bash
> conda activate claude-device   # 需要 Python 3.11 + playwright + pillow
> python build_assets.py         # SVG → 调色板量化 → RLE → crab_data.py
> python build_h.py              # crab_data.py → crab_data.h
> # 重新上传固件
> ```

### 2. 装 Claude Code 插件

```bash
/plugin marketplace add /Users/<你>/Documents/Code\ Local/兴趣项目开发/小克物理状态机/xiaoke-local-plugin
/plugin install xiaoke-physical-statemachine@xiaoke-local
/plugin enable xiaoke-physical-statemachine
```

依赖 `pyserial`（装到系统 python3：`pip3 install pyserial`）。

### 3. 启 Mac 桥接 daemon —— 自动

什么都不用做。下次给 Claude Code 发消息触发 hook → hook 自动 fork `bridge.py --ensure` → 自动生成 launchd plist + 启动 daemon + 编译 KeyReturn.app。

**唯一手动步骤**：第一次按硬件按钮时 macOS 会弹"KeyReturn 想要使用辅助功能"对话框 → 在系统设置里**手动勾上**（macOS 强制要求，无法自动）。详见 [xiaoke-local-plugin/daemon/README.md](xiaoke-local-plugin/daemon/README.md)。

### 4. 测试

```bash
python test_device.py    # 串口手动切状态，按 1-6 看各状态画面 + 电磁铁 + 蜂鸣器
```

或者**直接和 Claude Code 对话**——发个消息看屏切到 T；让它跑个需要权限的命令看屏切到 N + 电磁铁咔咔；按硬件按钮看是否自动确认权限。

## 文档导航

| 文档 | 内容 |
|------|------|
| [xiaoke-local-plugin/README.md](xiaoke-local-plugin/README.md) | Claude Code 插件安装说明 |
| [xiaoke-local-plugin/daemon/README.md](xiaoke-local-plugin/daemon/README.md) | Mac 桥接 daemon 的工作原理 + 调试 |

## 设计要点（一眼能看明白的版本）

- **电磁铁 = 强提醒信号**：只在 N/D/E 三个需要用户介入的状态触发，T/W/I 完全静止。"动 = 重要"的语义。
- **蜂鸣器接 3.3V 而非 5V**：低电平触发模块的 PNP 三极管在 5V 时半导通发热 + 信号失真，接 3.3V 完全截止反而声音更响。
- **MOS 用 IRF540N 而非 IRF520**：IRF520 需要 ≥10V Vgs，3.3V ESP32 驱动不开。IRF540N 是逻辑电平 MOSFET 才行。
- **D 状态固件层 6 秒自动转 I**：hook 只标记完成事件，回归静默由固件本地维护，hook 时序乱不影响体验。
- **Mac 桥接 daemon**：ESP32-C3 的 USB-Serial-JTAG 不支持 HID（按钮当不了 USB 键盘），所以走 Mac daemon 监听串口 + 用 KeyReturn.app 模拟回车的方案。详见复盘文档。

## 文件结构

```
.
├── README.md                              # 本文件
├── Arduino/                               # ESP32 固件
│   ├── claude_status/                     # 整合固件（主，烧这个）
│   ├── buzzer_test/ magnet_test/ switch_test/ magnet_freq_test/  # 单模块测试
│   ├── melody_compare/ notify_compare/    # 蜂鸣器 / N 召唤候选对比工具
│   └── graphicstest_copy_*/               # TFT 屏调校 sketch
├── xiaoke-local-plugin/                   # Claude Code 插件
│   ├── hooks/                             # Python hook 入口 + 配置
│   └── daemon/                            # Mac 桥接 daemon（独占串口 + 模拟按键）
├── assets/svg/                            # 6 状态 SVG（美术资产）
├── build_assets.py                        # SVG → Playwright 采样 → 调色板量化 → RLE
├── build_h.py                             # 生成的 crab_data.py → Arduino .h
├── simulator.py                           # Pygame 屏幕模拟器（不烧设备就能看动画）
├── dump_frames.py                         # 调试：反解码 RLE 出 PNG
├── test_device.py                         # 串口测试工具
└── cad/                                   # 外壳 CadQuery 设计 + STL
```

## License

个人项目，无 license 声明 = 仅供个人参考。商用请联系作者。
