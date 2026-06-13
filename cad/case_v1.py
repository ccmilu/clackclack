"""
小克物理状态机 — 外壳 v0.13

v0.12 → v0.13 改动：
- 参数重构成 4 个分区：零件实测尺寸 / 配合公差 / 外壳设计 / 自动推导
- 外壳整体尺寸 (W) 和所有内部位置从零件尺寸 + 配合公差自动推导
- 拿到打印件松/紧后，只需调 "第 2 部分：配合公差" 里对应的 FIT_* 即可

打印件松紧调节速查：
- 按钮装不进去 → 加 FIT_BTN_Y
- 按钮在腔里晃     → 减 FIT_BTN_Y（可取 0 甚至 -0.1，压配合）
- 电磁铁塞不进     → 加 FIT_SOL_XY
- 电磁铁晃         → 减 FIT_SOL_XY
- 顶部柱卡         → 加 FIT_SOL_POST
- 屏幕显示被挡     → 加 FIT_SCREEN_WIN
- USB 线插不进     → 加 FIT_USB_X / FIT_USB_Z
- 螺丝拧不动       → 加 FIT_CASE_SCREW（或 FIT_SOL_SCREW）
- 前后壳合不拢     → 加 FIT_ASSEMBLY
- 前后壳错位晃动   → 减 FIT_ASSEMBLY
"""

import cadquery as cq
from ocp_vscode import show
import math

# ===================================================================
# 第 1 部分：零件实测尺寸（拿到新批次零件，量了不一样就改这里）
# ===================================================================

# --- 电磁铁（5V 推拉式）---
# 三视图（电磁铁竖立放，顶部出柱朝上，螺丝孔面朝外即 +Y 方向）：
#
#         ┌──────┐   ← 顶部出柱（直径 SOL_FRONT_POST_D = 8mm）
#         │      │
#     ┌───┴──────┴───┐  ─┐
#     │              │   │
#     │      ◯      │   │   ← 螺丝孔朝你这面（+Y 面）
#     │      │ 15mm  │   │     这面尺寸 = SOL_W × SOL_BODY_L = 13 × 30
#     │      ◯      │   │     宽 = SOL_W（横向）
#     │              │   │     长 = SOL_BODY_L（竖向，沿电磁铁主轴）
#     │              │   │
#     └──────────────┘  ─┘  ← SOL_BODY_L = 30mm（长 / 高）
#     ←  SOL_W = 13mm →     ← SOL_W 是螺丝孔面的"宽"
#                                SOL_H = 15.5mm 是垂直纸面的厚度
#
SOL_W = 16.0              # X 宽 — 螺丝孔所在面（+Y 面）的"宽"，改这个会带动外壳 W 变
SOL_H = 15.5              # Y 厚 — 电磁铁从前到后的厚度（垂直于螺丝孔面）
SOL_BODY_L = 30.0         # Z 长 — 线圈本体长度，也是螺丝孔所在面的"长"（两螺丝孔沿这方向排列）
SOL_FRONT_POST_D = 8.0    # 顶部出柱直径（线圈正面 +Z 方向那根固定柱）
SOL_PISTON_D = 6.0        # 底部推杆直径（线圈底面 -Z 方向，可伸缩，电磁触发时压按钮）
# 推杆位置参数（外壳让位孔自动按"推杆能到的最低位置"= max(BOT_L, BOT_L+STROKE) 计算）
#
# BOT_L = 推杆最大伸出长度（无外力挡时的自然伸出，或手按到底的位置）
#   = 推杆完全伸出时，从线圈底面 SOL_Z_BOT 朝 -Z 伸出多少 mm
#
# STROKE = 电磁通电时推杆从自然位置的运动量
#   正数 = 电磁推出（推式电磁铁），推杆向下伸更长
#   负数 = 电磁吸入（拉式电磁铁），推杆向上缩回更短
#   0    = 不用电磁触发（纯手按）
#
# 例：用户场景 — 推杆自然伸 19mm，被按钮顶后实际停在 17mm；手按到底压弹片 ~19mm
#     BOT_L=19（推杆能到的最大伸出），STROKE=-5（电磁吸入 5mm 释放按钮）
SOL_PISTON_BOT_L = 19.0
SOL_PISTON_STROKE = -5.0
SOL_SCREW_SPACING = 15.0  # 两 M2 固定孔中心距（沿 SOL_BODY_L 方向 = Z）

# --- 微动按钮 ---
BTN_W = 12.8              # X 长（含杠杆方向）
BTN_D = 5.5               # Y 厚
BTN_H = 6.5               # Z 高（按钮本体，不含杠杆和引脚）
BTN_LEVER_L = 11          # 杠杆长度
BTN_LEVER_HEIGHT = 2      # 杠杆静止翘起高度
BTN_PIN_H = 3.6           # 引脚长度

# --- 屏幕（ST7735 0.96 寸 TFT）---
SCREEN_VW = 23.5          # 可视区宽
SCREEN_VH = 13            # 可视区高
SCREEN_BOARD_W = 32       # PCB 宽
SCREEN_BOARD_H = 30       # PCB 高

# --- ESP32-C3 USB-C 接口 ---
USB_W = 14.0              # USB 口宽
USB_H = 9.0               # USB 口高
USB_Z_FROM_BOTTOM = 14.0  # USB 中心距 case 底【内表面】（面包板贴内表面，从内表面往上算）
                          # 实际几何位置 Z = WALL + USB_Z_FROM_BOTTOM = 2 + 14 = 16

# --- 面包板（ESP32 插在面包板上）---
# 面包板长边必须沿 Y 方向放置，才能让 ESP32 USB 对到 case 后壁的开孔
# 默认值：mini SYB-170 面包板（最常见的 ESP32-C3 SuperMini 配套）
# 改这个会带动外壳 D（如果 BB_D + 2*WALL > 当前 D，D 自动扩大）
BB_W = 36.0               # 面包板 X 宽（短边）
BB_D = 47.0               # 面包板 Y 长（长边，决定外壳 D 最小值）
BB_H = 8.5                # 面包板 Z 厚（含背胶）

# --- 电磁铁驱动板（IRF540N 光耦隔离 MOS 模块）---
# 安装姿态：板长沿 Z 竖直、板宽沿 Y、厚度沿 X 塞进"按钮 holder 侧面↔内壁"的侧隙。
# 板子厚度不均匀：底部那一排接线端子（供电 + 负载螺丝端子）最厚 14mm，
# 其余部分（含光耦/MOS 等元件）最高只有 9mm。所以放置方案是：
#   · 厚 14mm 的端子段放在按钮 holder 正下方（holder 下方 X 不受 holder 挤占）
#   · 薄 9mm 的其余段贴内壁、沿 holder 侧面竖直往上
# 这些尺寸驱动下方"第 4 部分"里的装配约束 assert（改外壳参数后自动校验是否还放得下）。
DRV_W = 30.0              # 板宽（沿 Y）
DRV_L = 47.0              # 板长（沿 Z，竖直方向）= 端子段 14 + 薄段 33
DRV_TERMINAL_LEN = 14.0   # 底部端子段沿板长(Z)方向的长度
DRV_THICK_TERMINAL = 14.0 # 端子段厚度（沿 X）— 最厚，塞不进侧隙，必须放 holder 下方
DRV_THICK_BODY = 9.0      # 其余薄段最高厚度（沿 X）— 这一段才插进 holder↔内壁 侧隙

# --- 标准件 ---
M2_THREAD_D = 2.0         # M2 螺丝公称直径（用于光孔）


# ===================================================================
# 第 2 部分：配合公差（FIT_*）— 拿到 3D 打印件后，松紧不对就调这里
# ===================================================================
# 第一次打印：用初值
# 拿到件后：松了减 PAD（甚至取 0 或负值压配合）；紧了加 PAD
# 经验值：MJF/SLS 尼龙 ±0.2~0.3mm；FDM PLA ±0.3~0.5mm
# 注意：所有"单边"FIT 是指单边间隙，腔体尺寸 = 零件 + 2*FIT

# --- 按钮装配（Y 紧夹持，X/Z 滑动调位）---
FIT_BTN_Y = 0.2           # 按钮 Y 单边夹持余量（紧贴关键）
FIT_BTN_X_PLAY = 5.0      # 按钮可 X 滑动距离（单边，即整体 ±5mm）
FIT_BTN_Z_PLAY = 5.0      # 按钮可 Z 滑动距离（单边）

# --- 电磁铁装配 ---
FIT_SOL_XY = 0.3          # U 形框内侧到电磁铁单边间隙
FIT_SOL_POST = 0.5        # 顶部出柱穿孔单边余量（避免摩擦阻碍动作）
FIT_SOL_PISTON = 0.5      # 底部推杆穿过按钮支架的让位单边余量（避免推杆动作时摩擦）
FIT_SOL_SCREW = 0.25      # 电磁铁固定螺丝孔单边余量（孔径 = 2.0 + 2*0.25 = 2.5）

# --- 屏幕装配 ---
FIT_SCREEN_WIN = 0.5      # 屏幕开窗每边余量（避免遮挡显示）

# --- USB 装配 ---
FIT_USB_X = 1.0           # USB 孔 X 单边余量
FIT_USB_Z = 1.5           # USB 孔 Z 单边余量（含 PCB 安装高度公差）

# --- 面包板装配 ---
# 面包板放 case 底（贴内表面），X 方向被底部 2 个固定柱内缘夹，Y 方向被前后壁夹
# 单边余量 < 0.5mm 几乎贴死，没有装配公差，应给至少 1mm 单边让面包板能放进去
FIT_BB_X = 1.0            # 面包板 X 单边余量（柱内缘到面包板边）
FIT_BB_Y = 0.75           # 面包板 Y 单边余量（case 内壁到面包板边）

# --- 驱动板装配 ---
# 注意：与多数单边 FIT 不同，FIT_DRV 是"侧隙里要给板子留的最小总余量"（不是单边）。
# 板子竖插时一面贴内壁、一面朝 holder，是一条单缝，所以用总余量更直观。
# 实测板厚若含背面焊脚，要把焊脚算进 DRV_THICK_BODY 再留这个余量。
FIT_DRV = 0.5             # 驱动板与周边结构的最小装配余量

# --- 外壳螺丝 ---
FIT_CASE_SCREW = 0.25     # 外壳 4 角 M2 长螺丝过孔单边余量

# --- 前后壳接合（销外两面贴 case 外壁内表面，2 平面摩擦）---
# 销 7×7 矩形 + 后壳 4 角 case 内角（L 形），销外两面（X 外+Z 外）贴外壁内表面
# 摩擦力 = 2 平面接触 × 4 角，靠紧配合（FIT_ASSEMBLY 单边间隙）锁紧
# MJF 公差 ±0.2~0.3mm，默认 0.05 = 紧配合（推一下到位）
FIT_ASSEMBLY = 0.05       # 销外缘到 case 外壁内表面单边间隙
                          # 装不进 → 加到 0.15；装上后晃 → 减到 0 甚至 -0.05（过盈）


# ===================================================================
# 第 3 部分：外壳设计参数（一般不用动，调形状/打印参数时才改）
# ===================================================================

WALL = 2.0                # 主壁厚（嘉立创 MJF 推荐 ≥ 2mm）
WALL_PILLAR = 2.0         # 空心柱壁厚（嘉立创要求 ≥ 2mm，2.0 正好达标，比 2.25 节材）
TILT_ANGLE = 20           # 屏幕仓前倾角度
FILLET_R = 2              # 外壳圆角

CLAMP_T = 2.0             # 电磁铁 U 形框壁厚
CLAMP_Z_MARGIN = 3.0      # U 形框超出电磁铁螺丝孔上下的 Z 余量（不需要覆盖整个线圈本体）
CLAMP_TAPER_KEEP_BOT = 0.0 # U 形框 X 侧壁底部 Y 方向保留宽度（贴 +Y 后壁那一小段）
                          # X 侧壁原本是矩形 YZ 板，cut 成"上宽下窄"梯形节材
                          # 顶部 Y 全宽 ~16mm，底部 Y 只保留贴后壁的 3mm
                          # 电磁铁固定靠 +Y 后壁螺丝，X 侧壁底部 -Y 段不参与固定
BTN_HOLDER_WALL = 2.0     # 按钮支架壁厚

# M2 螺母（按钮固定用，嵌入后壳六边形凹槽）
M2_NUT_AF = 4.0           # 螺母对边距 (across flats)
M2_NUT_T = 1.6            # 螺母厚度
FIT_NUT = 0.2             # 螺母凹槽单边余量
SUPPORT_PILLAR_W = 4      # 按钮支架吊柱 X 宽（缩到 4 节材，仍 > 嘉立创最小 1.2）
SUPPORT_PILLAR_D = 6      # 按钮支架吊柱 Y 厚（不再跟 BTN_HOLDER_D=11 齐宽，缩到 6 节材）
ASM_PIN_L = 8.0           # 前后壳装配销长度（4 角柱端部 +Y 凸出 / 凹入 长度）
ASM_SOCKET_CHAMFER = 0.5  # socket 入口倒角（让销容易对中插入，避免插偏卡住）

# 4 角柱 = 1/4 扇形截面（贴壁两面平 + 外缘 1/4 圆弧朝内腔，省体积）
# 前壳柱 Y[-23.75, -10] + 凸出销 Y[-10, -2]
# 后壳柱 Y[-2, +23.75]（缩进去让出 Y[-10, -2] 给前壳销凸出）
# 销外两面贴 case 外壁内表面，2 平面接触摩擦力锁紧
# 注：PILLAR_R 在第 4 部分定义（依赖 SCREW_PILLAR_D 推导）

# 内部布局选择
SCREEN_OFFSET_UP = 8      # 屏幕窗沿斜面向上偏移（PCB 居中粘内侧时，屏在 PCB 上半）
TOP_HEADROOM = 2          # 电磁铁顶 → 顶板内表面预留间隙

# 按钮 Z 位置（由 MIN_BTN_BOTTOM_Z 锚定到 case 底，与电磁铁位置解耦）
# 电磁铁 Z 反推：SOL_Z_BOT = BTN_LEVER_TOP_Z + (BOT_L - BTN_REST_DEPRESS)
# H 自适应：max(BASE_H+BAY_H+TOP_H, SOL_Z_TOP + 余量)
MIN_BTN_BOTTOM_Z = 30     # 按钮 holder 底面距 case 底最小值
                          # 必须 > 面包板+ESP+杜邦线竖插的总高度（实测 ~30mm）
                          # 否则面包板装不进 / 卡住。改大会让 case 总高 H 自动增加
BTN_BOTTOM_MARGIN = 1.5   # 在 MIN_BTN_BOTTOM_Z 之上的安全余量（防贴死）
BTN_REST_DEPRESS = 1.5    # 推杆静止伸到 BOT_L 时，对按钮弹片的下压量 mm
                          # 弹片最大行程 = BTN_LEVER_HEIGHT (=2mm)，1.5 = 75% 行程足触发
                          # 这个参数决定了电磁铁与按钮的 Z 相对位置

# USB 装配位置
USB_X_OFFSET = -5         # USB 孔 X 偏移（面包板上 ESP 不在中心，往 -X 偏 ~5mm）
                          # 改这个调节 USB 孔位置以对齐实际 ESP USB-C 接口

# 整体外壳分段高度
# BASE_H 必须 ≥ 按钮 holder 顶 + 安全余量（按钮 holder 不能凸进斜面段）
# 按钮固定螺丝从后壁外拧入，不切前壁，所以无需考虑前壁通孔与 fillet 干涉
_base_h_min_for_btn = (
    MIN_BTN_BOTTOM_Z + BTN_BOTTOM_MARGIN
    + (BTN_H + 2 * FIT_BTN_Z_PLAY) / 2     # holder 顶在按钮中心 + POCKET_H/2
    + 1                                     # 安全余量
)
BASE_H = max(40, _base_h_min_for_btn)  # 底部高度（USB 仓 + 按钮腔）
BAY_H = 36                # 屏幕仓斜面段高度
TOP_H = 20                # 顶部段高度（电磁铁尾部 + 顶板）

# 嘉立创免费打印券限制
MAX_EDGE = 100            # 最大边长
MAX_VOLUME_CM3 = 70       # 最大体积（用免费高值打样券时）

# 是否导出 STL
EXPORT_STL = True


# ===================================================================
# 第 4 部分：自动推导（从前 3 部分算出几何位置 + 外壳尺寸）
# ===================================================================

# --- 各零件 holder/腔体尺寸（含配合余量）---

# 电磁铁 U 形框（包电磁铁 +Y/X 三面，开口朝 -Y）
# +Y 后壁贴 case 后壁，让 M2 螺丝从 case 后壁外穿过 U 形框 → 拧入电磁铁后面（+Y 面）的 M2 孔
SOL_CAVITY_W = SOL_W + 2 * FIT_SOL_XY            # X 方向腔内宽
SOL_CAVITY_H = SOL_H + FIT_SOL_XY                # Y 方向腔深（电磁铁 +Y 单边加余量，-Y 开口）
CLAMP_OUTER_W = SOL_CAVITY_W + 2 * CLAMP_T       # X 外宽
CLAMP_OUTER_D = SOL_CAVITY_H + CLAMP_T           # Y 外深

# 顶部圆孔（电磁铁出柱穿过）
TOP_HOLE_D = SOL_FRONT_POST_D + 2 * FIT_SOL_POST

# 底部推杆让位孔（在按钮支架顶部沿 Z 挖圆柱让推杆通过）
SOL_PISTON_HOLE_D = SOL_PISTON_D + 2 * FIT_SOL_PISTON

# 电磁铁固定螺丝孔径
SOL_SCREW_HOLE_D = M2_THREAD_D + 2 * FIT_SOL_SCREW

# 按钮腔尺寸
BTN_POCKET_W = BTN_W + 2 * FIT_BTN_X_PLAY        # X 余量（滑动调位）
BTN_POCKET_D = BTN_D + 2 * FIT_BTN_Y             # Y 紧贴
BTN_POCKET_H = BTN_H + 2 * FIT_BTN_Z_PLAY        # Z 余量
# 螺丝离按钮中心 X 距离 = 按钮 X 半宽 + X 余量 + 1.5mm 离边缘
BTN_FIX_X_DIST = BTN_W / 2 + FIT_BTN_X_PLAY + 1.5
# Holder 宽 = max(按钮坑宽 + 壁厚, 螺丝两侧 + 边缘加固)
BTN_HOLDER_W = max(
    BTN_POCKET_W + 2 * BTN_HOLDER_WALL,
    2 * BTN_FIX_X_DIST + 6,
)
# Holder Y 取按钮坑和推杆让位径的较大值（自动保证挖完后外壁 ≥ BTN_HOLDER_WALL）
BTN_HOLDER_D = max(BTN_POCKET_D, SOL_PISTON_HOLE_D) + 2 * BTN_HOLDER_WALL
BTN_HOLDER_H = BTN_POCKET_H                       # 顶底贯穿，Z 高 = 坑高

BTN_FIX_SCREW_D = M2_THREAD_D + 2 * FIT_CASE_SCREW

# 外壳 4 角螺丝柱
SCREW_PILLAR_D = M2_THREAD_D + 2 * FIT_CASE_SCREW + 2 * WALL_PILLAR
SCREW_HOLE_D = M2_THREAD_D + 2 * FIT_CASE_SCREW

# 圆柱半径（=M2 通孔到圆外缘的距离基础）
PILLAR_R = SCREW_PILLAR_D / 2    # 3.5mm = 柱圆半径

# 销外两面到 case 外壁内表面的间隙 = 0（销贴外壁面接触）
# FIT_ASSEMBLY 实际通过销外缘 vs case 内表面的"轻微过盈/间隙"控制摩擦松紧

# 电磁铁固定螺丝穿外壁的空心支撑柱（U 形框后到 case 后壁）
CLAMP_SUPPORT_D = M2_THREAD_D + 2 * FIT_SOL_SCREW + 2 * WALL_PILLAR

# USB 开孔
USB_HOLE_W = USB_W + 2 * FIT_USB_X
USB_HOLE_H = USB_H + 2 * FIT_USB_Z


# --- 外壳整体 W (X 方向)：取所有内部组件 X 占用最大值，加边距 ---

# X 方向内腔需要的宽度（考虑 4 角螺丝柱在内腔角落，不算它）
# 面包板 X 需要单独加 FIT_BB_X 余量（底部柱内缘距离要 > BB_W + 2*FIT_BB_X，否则面包板放不进）
INNER_W_REQUIRED = max(
    SCREEN_BOARD_W,
    BTN_HOLDER_W,
    CLAMP_OUTER_W,
    USB_HOLE_W,
    BB_W + 2 * FIT_BB_X,   # 面包板 X 含装配单边余量
)
# 整体 W = 内腔 + 2*WALL + 4 角矩形柱宽（贴外壁内角）+ 0.5mm 单边缓冲
# 柱外缘 X = case 外壁内表面 X，柱内缘 X = W/2 - WALL - SCREW_PILLAR_D
W = INNER_W_REQUIRED + 2 * WALL + 2 * SCREW_PILLAR_D + 1
assert W <= MAX_EDGE, f"W={W} 超过 {MAX_EDGE}mm 嘉立创免费打印限制"


# --- 外壳 D (Y 方向)：取面包板需求和默认值的最大值 ---
# 面包板长边沿 Y 放置（USB 朝 +Y 出去对孔），所以 D ≥ BB_D + 2*WALL + 2*FIT_BB_Y
D_BASE = 45                                       # 不考虑面包板时的默认 D
D_REQUIRED_FOR_BB = BB_D + 2 * WALL + 2 * FIT_BB_Y
D = max(D_BASE, D_REQUIRED_FOR_BB)
assert D <= MAX_EDGE, f"D={D} 超过 {MAX_EDGE}mm"

# --- 按钮 Z 位置（先算，锚定 case 底，优先级最高）---
BTN_CENTER_X = 0
BTN_CENTER_Y = 0   # 按钮 Y 必须对齐推杆轴线（=SOL_Y_CENTER=0），错开会让推杆撞按钮支架 -Y 壁
BTN_HOLDER_BOT_Z = MIN_BTN_BOTTOM_Z + BTN_BOTTOM_MARGIN
BTN_HOLDER_TOP_Z = BTN_HOLDER_BOT_Z + BTN_POCKET_H
BTN_POCKET_CENTER_Z = (BTN_HOLDER_BOT_Z + BTN_HOLDER_TOP_Z) / 2
BTN_BODY_TOP_Z = BTN_POCKET_CENTER_Z + BTN_H / 2
BTN_BODY_BOT_Z = BTN_POCKET_CENTER_Z - BTN_H / 2
BTN_LEVER_TOP_Z = BTN_BODY_TOP_Z + BTN_LEVER_HEIGHT
BTN_HOLDER_Z_CENTER = BTN_POCKET_CENTER_Z

# --- 电磁铁 Z 位置（由按钮位置 + 推杆静止下压量反推，确保推杆能下压弹片 BTN_REST_DEPRESS）---
# 推杆静止伸出 BOT_L，下压弹片 BTN_REST_DEPRESS → SOL_Z_BOT = BTN_LEVER_TOP_Z + (BOT_L - BTN_REST_DEPRESS)
BTN_TO_COIL_BOTTOM = SOL_PISTON_BOT_L - BTN_REST_DEPRESS    # 派生（弹片→线圈底距离）
SOL_X_CENTER = 0
SOL_Y_CENTER = 0
SOL_Z_BOT = BTN_LEVER_TOP_Z + BTN_TO_COIL_BOTTOM
SOL_Z_TOP = SOL_Z_BOT + SOL_BODY_L
SOL_Z_CENTER = (SOL_Z_TOP + SOL_Z_BOT) / 2
SOL_SCREW_Z_TOP = SOL_Z_CENTER + SOL_SCREW_SPACING / 2
SOL_SCREW_Z_BOT = SOL_Z_CENTER - SOL_SCREW_SPACING / 2

# --- 外壳 H (Z 方向)：自适应（max 分段累加 vs 电磁铁顶 + 余量）---
_h_from_layers = BASE_H + BAY_H + TOP_H
_h_from_sol = SOL_Z_TOP + TOP_HEADROOM + WALL
H = max(_h_from_layers, _h_from_sol)
PROTRUDE = BAY_H * math.tan(math.radians(TILT_ANGLE))   # 斜面突出量
assert H <= MAX_EDGE, f"H={H} 超过 {MAX_EDGE}mm"

# --- U 形框位置（开口朝 +Y）---
# Z 顶直接延伸到 case 顶板内表面（不悬空），Z 底覆盖下螺丝孔 + CLAMP_Z_MARGIN 余量
CLAMP_X_CENTER = SOL_X_CENTER
CLAMP_Y_CENTER = SOL_Y_CENTER + CLAMP_T / 2
CLAMP_Z_TOP = H - WALL
CLAMP_Z_BOT = SOL_SCREW_Z_BOT - CLAMP_Z_MARGIN
CLAMP_Z_LENGTH = CLAMP_Z_TOP - CLAMP_Z_BOT
CLAMP_Z_CENTER = (CLAMP_Z_TOP + CLAMP_Z_BOT) / 2

# --- 六边形螺母凹槽（嵌入后壳，按钮固定螺母）---
NUT_POCKET_AF = M2_NUT_AF + 2 * FIT_NUT
NUT_POCKET_DIAMETER = NUT_POCKET_AF * 2 / math.sqrt(3)
NUT_POCKET_T = M2_NUT_T + 0.5

# --- 校验 ---
assert BTN_HOLDER_BOT_Z >= MIN_BTN_BOTTOM_Z, (
    f"按钮 holder 底 Z = {BTN_HOLDER_BOT_Z}mm < MIN_BTN_BOTTOM_Z = {MIN_BTN_BOTTOM_Z}mm"
)
_btn_to_lower_screw = SOL_SCREW_Z_BOT - BTN_LEVER_TOP_Z
assert 20 <= _btn_to_lower_screw <= 30, (
    f"按钮弹片距电磁铁下螺丝孔 {_btn_to_lower_screw}mm 超出 [20, 30] 范围（电磁铁行程 ~10mm 必须落中间）"
)
# 注：按钮固定螺丝从后壁外拧入（不切前壁），前壁外表面无通孔
# 原来"通孔顶 < BASE_H - FILLET_R"约束已不适用

# 推杆让位 Z 范围
# 底 = max(按钮顶+0.5mm 缓冲, 推杆最伸出时底面-0.5mm)
# 顶 = holder 顶+1mm（切干净 holder 顶板）
_piston_static_extend = SOL_PISTON_BOT_L
_piston_trigger_extend = SOL_PISTON_BOT_L + SOL_PISTON_STROKE
_piston_max_extend = max(_piston_static_extend, _piston_trigger_extend)
_piston_z_min = SOL_Z_BOT - _piston_max_extend   # 推杆底面最低 Z（最伸出时位置）
PISTON_CLEAR_Z_BOT = max(BTN_BODY_TOP_Z + 0.5, _piston_z_min - 0.5)
PISTON_CLEAR_Z_TOP = BTN_HOLDER_TOP_Z + 1
PISTON_CLEAR_H = PISTON_CLEAR_Z_TOP - PISTON_CLEAR_Z_BOT
PISTON_NEEDS_CLEARANCE = SOL_PISTON_HOLE_D > BTN_POCKET_D and PISTON_CLEAR_H > 0

# 校验：BTN_TO_COIL_BOTTOM (按钮 Z 位置) 在推杆静止/触发伸出区间内
# 静止伸出 < 按钮距 < 触发伸出 (推式) 或反过来 (拉式)，用 min/max 兼容两种
_piston_extend_min = min(_piston_static_extend, _piston_trigger_extend)
_piston_extend_max = max(_piston_static_extend, _piston_trigger_extend)
assert _piston_extend_min < BTN_TO_COIL_BOTTOM < _piston_extend_max, (
    f"按钮距线圈底 {BTN_TO_COIL_BOTTOM}mm 不在推杆运动范围 "
    f"[{_piston_extend_min}, {_piston_extend_max}]mm 内（推杆触发不了按钮）"
)

# 校验：让位孔挖完后 holder -Y/+Y 外壁剩余壁厚 ≥ BTN_HOLDER_WALL（嘉立创 ≥ 2mm 推荐）
_remaining_wall_y = (BTN_HOLDER_D - SOL_PISTON_HOLE_D) / 2
assert _remaining_wall_y >= BTN_HOLDER_WALL - 0.01, (
    f"推杆让位孔挖完后 holder Y 外壁仅剩 {_remaining_wall_y:.2f}mm "
    f"< 要求的 {BTN_HOLDER_WALL}mm（嘉立创最小壁厚）"
)

# === 电磁铁驱动板竖直安装约束（改外壳参数后自动校验是否还放得下）===
# 几何位置：端子段顶面顶住按钮 holder 底面 → 端子段在 holder 下方、薄段沿 holder 侧面往上。
DRV_SIDE_GAP = (W / 2 - WALL) - BTN_HOLDER_W / 2     # holder 侧面 → 内壁 X 空隙（薄段塞这里）
DRV_BODY_LEN = DRV_L - DRV_TERMINAL_LEN              # 薄段长度（沿 Z）
DRV_BOT_Z = BTN_HOLDER_BOT_Z - DRV_TERMINAL_LEN     # 端子段底 = 整板最低点
DRV_TOP_Z = BTN_HOLDER_BOT_Z + DRV_BODY_LEN         # 薄段顶 = 整板最高点
_drv_bb_top_z = WALL + BB_H                          # 面包板本体顶面 Z

# 1) 薄段厚度必须塞得进侧隙 —— 改 W / FIT_BTN_X_PLAY 后最容易被破坏的约束
assert DRV_SIDE_GAP >= DRV_THICK_BODY + FIT_DRV, (
    f"驱动板薄段厚 {DRV_THICK_BODY} + 余量 {FIT_DRV} = {DRV_THICK_BODY + FIT_DRV} "
    f"> holder↔内壁侧隙 {DRV_SIDE_GAP:.2f}mm，竖插塞不进。"
    f"对策：加 W（加 BB_W 或外扩）或减小 BTN_HOLDER_W（减 FIT_BTN_X_PLAY）"
)
# 2) 厚端子段(14mm)塞不进侧隙，只能放 holder 下方 → 端子段底不能压到面包板本体
assert DRV_BOT_Z >= _drv_bb_top_z + FIT_DRV, (
    f"驱动板端子段底 Z={DRV_BOT_Z:.1f} < 面包板顶 Z={_drv_bb_top_z} + 余量 {FIT_DRV}，"
    f"端子段会压到面包板。对策：抬高 MIN_BTN_BOTTOM_Z 或减小 DRV_TERMINAL_LEN"
)
# 3) 整板顶不能顶穿外壳内顶
assert DRV_TOP_Z <= H - WALL, (
    f"驱动板顶 Z={DRV_TOP_Z:.1f} > 外壳内顶 Z={H - WALL:.1f}，板太长或外壳太矮"
)
# 4) 板宽(沿 Y)必须放得进 case 内 Y 空间
assert DRV_W + 2 * FIT_DRV <= D - 2 * WALL, (
    f"驱动板宽 {DRV_W}(沿Y) + 2*{FIT_DRV} > case 内 Y 空间 {D - 2 * WALL:.1f}mm"
)
# 注：端子段(贴壁 14mm 厚)的 X 内缘必然越过面包板 X 投影边，与面包板上 ESP32+竖插杜邦线
# (顶到 Z≈MIN_BTN_BOTTOM_Z) 在 X/Z 上重叠。这是接线走向问题，CAD 无法约束 → 见报告区的 ⚠ 提醒。

# 按钮支架吊柱（从顶板内表面伸下到 holder 顶）
SUPPORT_TOP_Z = H - WALL
SUPPORT_BOT_Z = BTN_HOLDER_TOP_Z
SUPPORT_H = SUPPORT_TOP_Z - SUPPORT_BOT_Z
SUPPORT_CENTER_Z = (SUPPORT_TOP_Z + SUPPORT_BOT_Z) / 2

# 4 角柱位置：柱圆心 = M2 通孔位置，距 case 外壁内表面 (PILLAR_R + WALL_PILLAR) 让壁厚足够
# 柱圆右切线距 case 外壁内表面 = WALL_PILLAR（空心柱壁厚要求 ≥2mm）
# 但柱有矩形附加延伸到外壁，所以柱圆心距外壁 = PILLAR_R + 矩形附加宽
# 简化：柱圆心 X = W/2 - WALL - PILLAR_R（圆右切线刚好在 X=W/2-WALL 内表面 - 0 处，柱外缘 = 内表面）
# 等下：让矩形附加从圆心延伸到外壁 → 圆心 X 任意，关键看圆内缘和零件距离
# 柱圆心 X = case 外壁内表面 X - PILLAR_R（圆与外壁内表面相切，矩形附加宽=0）
# 实际：圆心距外壁 = R 时矩形宽=0，柱=纯圆，外缘只切点接触。
# 用户要"贴壁两面平"，矩形附加宽 > 0。设矩形宽 RECT_W：圆心 X = W/2 - WALL - PILLAR_R - RECT_W
SCREW_PILLAR_X = W / 2 - WALL - PILLAR_R         # 圆心距外壁 = R，圆右切线 = 外壁内表面
SCREW_PILLAR_Z_BOT = WALL + PILLAR_R
SCREW_PILLAR_Z_TOP = H - WALL - PILLAR_R

# 销 = 圆半径 - FIT 间隙
PIN_R = PILLAR_R - FIT_ASSEMBLY

PILLAR_POSITIONS = [
    (-SCREW_PILLAR_X, SCREW_PILLAR_Z_BOT),
    (+SCREW_PILLAR_X, SCREW_PILLAR_Z_BOT),
    (-SCREW_PILLAR_X, SCREW_PILLAR_Z_TOP),
    (+SCREW_PILLAR_X, SCREW_PILLAR_Z_TOP),
]

# 前后壳切平面
Y_SPLIT = -10


# ===================================================================
# 第 5 部分：几何构建（从这往下都是从上面的参数自动算出来的）
# ===================================================================

# --- 主体侧视轮廓（YZ 平面）---
profile = [
    ( D/2,                    0              ),
    (-D/2,                    0              ),
    (-D/2,                    BASE_H         ),
    (-D/2 - PROTRUDE,         BASE_H         ),
    (-D/2,                    BASE_H + BAY_H ),
    (-D/2,                    H              ),
    ( D/2,                    H              ),
]

def make_y_pillar(x, z, radius, y_min, y_max):
    """沿 Y 方向圆柱（用于通孔、销等，在多处被引用所以提前定义）"""
    length = y_max - y_min
    return (
        cq.Workplane("XY")
        .transformed(rotate=(-90, 0, 0))
        .circle(radius)
        .extrude(length)
        .translate((x, y_min, z))
    )

outer = (
    cq.Workplane("YZ")
    .polyline(profile)
    .close()
    .extrude(W/2, both=True)
    .edges()
    .fillet(FILLET_R)
)

inner_cavity = (
    cq.Workplane("YZ")
    .polyline(profile)
    .close()
    .offset2D(-WALL, kind="intersection")
    .extrude((W - 2*WALL)/2, both=True)
)

# --- 屏幕开窗（贯穿前斜面）---
SCREEN_PLANE_ROT = 90 - TILT_ANGLE
cos_t = math.cos(math.radians(TILT_ANGLE))
sin_t = math.sin(math.radians(TILT_ANGLE))
wall_mid_offset_screen = (
    0,
    -D/2 - PROTRUDE/2 + (WALL/2) * cos_t + SCREEN_OFFSET_UP * sin_t,
    BASE_H + BAY_H/2 - (WALL/2) * sin_t + SCREEN_OFFSET_UP * cos_t,
)
screen_window = (
    cq.Workplane("XY")
    .transformed(offset=wall_mid_offset_screen, rotate=(SCREEN_PLANE_ROT, 0, 0))
    .rect(SCREEN_VW + 2*FIT_SCREEN_WIN, SCREEN_VH + 2*FIT_SCREEN_WIN)
    .extrude(WALL/2 + 0.5, both=True)
)

# --- 电磁铁 U 形框（Z 范围只覆盖两个螺丝孔 ± 余量）---
clamp_outer_box = (
    cq.Workplane("XY")
    .box(CLAMP_OUTER_W, CLAMP_OUTER_D, CLAMP_Z_LENGTH)
    .translate((CLAMP_X_CENTER, CLAMP_Y_CENTER, CLAMP_Z_CENTER))
)
clamp_inner_box = (
    cq.Workplane("XY")
    .box(SOL_CAVITY_W, 100, CLAMP_Z_LENGTH + 100)
    .translate((
        SOL_X_CENTER,
        # inner +Y 边 = 电磁铁 +Y 面 + 配合余量
        # cut 后 U 形框留下：电磁铁 +Y 方向后壁（含螺丝穿孔）+ X 左右两侧壁
        SOL_Y_CENTER - 50 + (SOL_H/2 + FIT_SOL_XY),
        CLAMP_Z_CENTER
    ))
)
clamp_frame = clamp_outer_box.cut(clamp_inner_box)

# --- U 形框 X 两侧壁三角形 cut（上宽下窄节材）---
# 侧壁原本是矩形 YZ 板（Y 方向跨 CLAMP_OUTER_D），cut 成上宽下窄梯形
# 顶部 Y 全宽（贴 case 顶板），底部 Y 只保留贴 +Y 后壁的 CLAMP_TAPER_KEEP_BOT 段
_clamp_y_back = SOL_Y_CENTER + SOL_H / 2 + FIT_SOL_XY      # 内腔 +Y 后壁内表面（=8.05）
_clamp_y_front = CLAMP_Y_CENTER - CLAMP_OUTER_D / 2        # X 侧壁前缘 Y（=-7.9）
_clamp_y_keep_bot = _clamp_y_back - CLAMP_TAPER_KEEP_BOT   # 底部保留段前缘 Y

# 三角形 polyline（YZ 平面）：左下 → 左上 → 右下 → 闭合
# 切掉这个三角形等于把 X 侧壁的左下部分削成斜面
clamp_taper_cut = (
    cq.Workplane("YZ")
    .polyline([
        (_clamp_y_front, CLAMP_Z_BOT),
        (_clamp_y_front, CLAMP_Z_TOP),
        (_clamp_y_keep_bot, CLAMP_Z_BOT),
    ])
    .close()
    .extrude(CLAMP_OUTER_W / 2 + 1, both=True)   # X 全范围（含余量），跨过 inner 已挖区不影响
)
clamp_frame = clamp_frame.cut(clamp_taper_cut)

# --- 按钮支架（Y 紧贴，X/Z 滑动）---
btn_holder_outer = (
    cq.Workplane("XY")
    .box(BTN_HOLDER_W, BTN_HOLDER_D, BTN_HOLDER_H)
    .translate((BTN_CENTER_X, BTN_CENTER_Y, BTN_HOLDER_Z_CENTER))
)
btn_holder_inner = (
    cq.Workplane("XY")
    .box(BTN_POCKET_W, BTN_POCKET_D, BTN_HOLDER_H + 20)
    .translate((BTN_CENTER_X, BTN_CENTER_Y, BTN_HOLDER_Z_CENTER))
)
btn_holder = btn_holder_outer.cut(btn_holder_inner)
# 注意：按钮固定螺丝孔改在 case 主体 cut（全程贯穿前壁外 → 后壁内表面）
# 在 case 主体合并代码后 cut btn_fix_holes_through

# --- 按钮支架吊柱 ---
support_left = (
    cq.Workplane("XY")
    .box(SUPPORT_PILLAR_W, SUPPORT_PILLAR_D, SUPPORT_H)
    .translate((
        BTN_CENTER_X - BTN_HOLDER_W/2 + SUPPORT_PILLAR_W/2,
        BTN_CENTER_Y,
        SUPPORT_CENTER_Z
    ))
)
support_right = (
    cq.Workplane("XY")
    .box(SUPPORT_PILLAR_W, SUPPORT_PILLAR_D, SUPPORT_H)
    .translate((
        BTN_CENTER_X + BTN_HOLDER_W/2 - SUPPORT_PILLAR_W/2,
        BTN_CENTER_Y,
        SUPPORT_CENTER_Z
    ))
)
btn_support = btn_holder.union(support_left).union(support_right)

# --- 推杆让位（按钮坑顶部 Z 段额外扩 Y 让推杆通过）---
# 按钮中心 Y = SOL_Y_CENTER = 0 已对齐推杆轴线
# 推杆 D(=6) + 2*FIT_SOL_PISTON(=0.5) = 7mm > 按钮 Y 紧贴坑 6.2mm
# 解法：只在按钮顶以上 + 推杆触发段挖矩形让位孔（按钮本体 Z 段保持 Y 紧贴）
if PISTON_NEEDS_CLEARANCE:
    piston_clearance = (
        cq.Workplane("XY")
        .box(SOL_PISTON_HOLE_D, SOL_PISTON_HOLE_D, PISTON_CLEAR_H)
        .translate((SOL_X_CENTER, SOL_Y_CENTER, PISTON_CLEAR_Z_BOT + PISTON_CLEAR_H/2))
    )
    btn_support = btn_support.cut(piston_clearance)

# --- 顶部出柱穿孔 ---
top_hole = (
    cq.Workplane("XY")
    .circle(TOP_HOLE_D / 2)
    .extrude(WALL + 3)
    .translate((SOL_X_CENTER, SOL_Y_CENTER, H - WALL - 1))
)

# --- 电磁铁 M2 固定孔（从后壁外拧入：穿后壁 → U 形框后壁 → 进电磁铁）---
# 长度只到电磁铁后表面 + 5mm 进入电磁铁内孔，绝不能超过电磁铁前表面（否则会穿透前壁切出多余孔）
# 起点 Y=+D/2+2=+27.75 沿 -Y 方向，终点 ≈ 电磁铁前表面 - 1mm 余量
_clamp_screw_y_start = D/2 + 2
_clamp_screw_y_end = SOL_Y_CENTER - SOL_H/2 + 1   # 到电磁铁前表面留 1mm（穿透整个电磁铁本体）
_clamp_screw_length = _clamp_screw_y_start - _clamp_screw_y_end
clamp_screw_holes = (
    cq.Workplane("XZ", origin=(0, _clamp_screw_y_start, 0))
    .pushPoints([
        (SOL_X_CENTER, SOL_SCREW_Z_TOP),
        (SOL_X_CENTER, SOL_SCREW_Z_BOT)
    ])
    .circle(SOL_SCREW_HOLE_D / 2)
    .extrude(_clamp_screw_length)
)

# --- 电磁铁螺丝空心支撑柱（防长螺丝拧紧时变形，连接 U 形框 +Y 外表面 到 case 后壁内表面）---
clamp_support_y_min = CLAMP_Y_CENTER + CLAMP_OUTER_D / 2      # U 形框 +Y 外表面（修复 bug：原来算错 7.9mm）
clamp_support_y_max = D/2 - WALL                              # case 后内壁 Y
clamp_support_y_length = clamp_support_y_max - clamp_support_y_min

clamp_support_top = (
    cq.Workplane("XY")
    .transformed(rotate=(-90, 0, 0))
    .circle(CLAMP_SUPPORT_D / 2)
    .extrude(clamp_support_y_length)
    .translate((SOL_X_CENTER, clamp_support_y_min, SOL_SCREW_Z_TOP))
)
clamp_support_bot = (
    cq.Workplane("XY")
    .transformed(rotate=(-90, 0, 0))
    .circle(CLAMP_SUPPORT_D / 2)
    .extrude(clamp_support_y_length)
    .translate((SOL_X_CENTER, clamp_support_y_min, SOL_SCREW_Z_BOT))
)
clamp_support_pillars = clamp_support_top.union(clamp_support_bot)

# 注：U 形框 Z 顶已直接到 case 顶板（CLAMP_Z_TOP = H - WALL），无需 brace 细柱

# --- 按钮固定螺丝支撑柱（前后段空心柱，让长螺丝拧紧不变形）---
# 前段：前壁内表面 → 按钮 holder 前壁外
# 后段：按钮 holder 后壁外 → 六边形螺母凹槽
# D=6mm（壁厚 1.75mm，满足嘉立创最小 1.2mm；推荐 2mm 略不达标但短柱 OK）
BTN_SUPPORT_D = 6.0
_btn_holder_y_front = BTN_CENTER_Y - BTN_HOLDER_D / 2
_btn_holder_y_back  = BTN_CENTER_Y + BTN_HOLDER_D / 2
# 后段柱直接延伸到 case 后壁内表面（视觉上"连接"外壳），凹槽 cut 会切到柱端 0.1mm
btn_screw_supports = None
for x_off in (-BTN_FIX_X_DIST, +BTN_FIX_X_DIST):
    post = make_y_pillar(
        BTN_CENTER_X + x_off, BTN_POCKET_CENTER_Z, BTN_SUPPORT_D / 2,
        _btn_holder_y_back, D/2 - WALL
    )
    btn_screw_supports = post if btn_screw_supports is None else btn_screw_supports.union(post)

# --- USB 出口 ---
usb_hole = (
    cq.Workplane("XY")
    .box(USB_HOLE_W, WALL*4, USB_HOLE_H)
    .translate((USB_X_OFFSET, D/2 - WALL/2, WALL + USB_Z_FROM_BOTTOM))
)

# --- 按钮固定螺丝孔（从后壁外凹槽 → 后段空心柱 → 按钮 holder → 停在 holder 前壁内）---
# 螺丝从后壳后壁外凹槽位置拧入：螺丝头沉凹槽 → 穿螺母 → 后壁 → case 内空气 →
# 后段空心柱 → holder 后壁 → 按钮腔（X 外侧不碰按钮）→ holder 前壁 → 盲孔停在 holder 前 1mm
# 起点 Y = -BTN_HOLDER_D/2 - 1：通孔完全不接触 case 前壁，前壁外表面保持平整无孔
def _make_btn_fix_hole(x_offset):
    return make_y_pillar(
        BTN_CENTER_X + x_offset, BTN_POCKET_CENTER_Z,
        BTN_FIX_SCREW_D / 2,
        -BTN_HOLDER_D / 2 - 1,     # holder 前壁外 1mm（盲孔停留余量），不延伸到 case 前壁
        D/2 - WALL                 # 后壁内表面（不贯穿后壁，螺母在凹槽里）
    )
btn_fix_holes_through = _make_btn_fix_hole(-BTN_FIX_X_DIST).union(_make_btn_fix_hole(+BTN_FIX_X_DIST))


# --- 合并主体 ---
case = (
    outer
    .cut(inner_cavity)
    .union(clamp_frame)
    .union(btn_support)
    .union(clamp_support_pillars)
    .union(btn_screw_supports)
    .cut(screen_window)
    .cut(top_hole)
    .cut(clamp_screw_holes)
    .cut(usb_hole)
    .cut(btn_fix_holes_through)
)


# --- 拆装：前壳 4 角矩形柱（楔子塞墙角）+ 后壳无柱 + Y 切前后分壳 ---
def make_y_quarter_pillar(px, pz, y_min, y_max, r, wall_clearance=0):
    """圆柱 + 朝外角的 X/Z 两片矩形附加（贴壁两面平 + 朝内腔 -X-Z 角 1/4 圆弧）
    px, pz: 柱圆心 = M2 通孔位置 (本身就是 PILLAR_POSITIONS)
    r: 圆半径（柱外接矩形 = 2r × 2r）
    wall_clearance: 矩形外缘到 case 外壁内表面单边间隙（柱=0 贴壁，销=FIT 留间隙）
    自动判定贴壁方向：px > 0 朝 +X 贴壁，pz > H/2 朝 +Z 贴壁"""
    dx = 1 if px > 0 else -1
    dz = 1 if pz > H/2 else -1
    length = y_max - y_min
    y_center = (y_min + y_max) / 2

    # 圆柱 (半径 r, 圆心 = M2 通孔位置)
    cyl = make_y_pillar(px, pz, r, y_min, y_max)

    # X-矩形附加 (从圆心朝 case 外壁 X 方向延伸到 外壁内表面 - wall_clearance)
    wall_x = ((W/2 - WALL) - wall_clearance) * dx
    rect_x_w = abs(wall_x - px)
    if rect_x_w > 0:
        rect_x = (
            cq.Workplane("XY")
            .box(rect_x_w, length, 2 * r)
            .translate(((px + wall_x) / 2, y_center, pz))
        )
        cyl = cyl.union(rect_x)

    # Z-矩形附加 (从圆心朝 case 外壁 Z 方向延伸到 外壁内表面 - wall_clearance)
    wall_z = (H - WALL - wall_clearance) if dz > 0 else (WALL + wall_clearance)
    rect_z_h = abs(wall_z - pz)
    if rect_z_h > 0:
        rect_z = (
            cq.Workplane("XY")
            .box(2 * r, length, rect_z_h)
            .translate((px, y_center, (pz + wall_z) / 2))
        )
        cyl = cyl.union(rect_z)

    return cyl

pillar_y_min = -(D/2 - WALL)
pillar_y_max = +(D/2 - WALL)

# 前壳柱本体 Y[-23.75, -10] + 后壳柱本体 Y[-2, +23.75]
# 中间段 Y[-10, -2] 留出（给前壳销凸出，切两半后单独 union 到前壳）
front_pillar_y_max = Y_SPLIT
rear_pillar_y_min = Y_SPLIT + ASM_PIN_L

all_pillars = make_y_quarter_pillar(*PILLAR_POSITIONS[0], pillar_y_min, front_pillar_y_max, PILLAR_R)
for px, pz in PILLAR_POSITIONS[1:]:
    all_pillars = all_pillars.union(make_y_quarter_pillar(px, pz, pillar_y_min, front_pillar_y_max, PILLAR_R))
for px, pz in PILLAR_POSITIONS:
    all_pillars = all_pillars.union(make_y_quarter_pillar(px, pz, rear_pillar_y_min, pillar_y_max, PILLAR_R))

case = case.union(all_pillars)

hole_y_min = -(D/2 + 2)
hole_y_max = +(D/2 + 2)
screw_holes = make_y_pillar(*PILLAR_POSITIONS[0], SCREW_HOLE_D/2, hole_y_min, hole_y_max)
for px, pz in PILLAR_POSITIONS[1:]:
    screw_holes = screw_holes.union(make_y_pillar(px, pz, SCREW_HOLE_D/2, hole_y_min, hole_y_max))

case = case.cut(screw_holes)

# Y=Y_SPLIT 平面切两半
_big = max(W, D, H) + 20
front_half = case.intersect(
    cq.Workplane("XY").box(_big, _big, _big).translate((0, Y_SPLIT - _big/2, H/2))
)
rear_half = case.intersect(
    cq.Workplane("XY").box(_big, _big, _big).translate((0, Y_SPLIT + _big/2, H/2))
)

# --- 后壳六边形螺母凹槽（容纳按钮固定 M2 螺母）---
# 螺母嵌入凹槽 → 螺丝从前壳前面拧入，穿过 case 到达此凹槽螺母 → 拧紧固定按钮
def make_hex_pocket_y(px, pz, y_min, y_max, diameter):
    length = y_max - y_min
    return (
        cq.Workplane("XY")
        .transformed(rotate=(-90, 0, 0))
        .polygon(6, diameter)
        .extrude(length)
        .translate((px, y_min, pz))
    )

# 朝外开口：凹槽从后壁外表面挖进去，让螺母从 case 外面塞入（在外表面可见）
_nut_pocket_y_max = D/2                                  # 凹槽外口 = 后壁外表面
_nut_pocket_y_min = _nut_pocket_y_max - NUT_POCKET_T    # 凹槽底 = 朝 case 内挖 T 深
for x_off in (-BTN_FIX_X_DIST, +BTN_FIX_X_DIST):
    nut_pocket = make_hex_pocket_y(
        BTN_CENTER_X + x_off, BTN_POCKET_CENTER_Z,
        _nut_pocket_y_min, _nut_pocket_y_max,
        NUT_POCKET_DIAMETER
    )
    rear_half = rear_half.cut(nut_pocket)

# 4 角壳体装配螺丝的六边形螺母凹槽（同样朝后壁外开口）
for px, pz in PILLAR_POSITIONS:
    nut_pocket = make_hex_pocket_y(
        px, pz,
        _nut_pocket_y_min, _nut_pocket_y_max,
        NUT_POCKET_DIAMETER
    )
    rear_half = rear_half.cut(nut_pocket)

# 电磁铁固定螺丝的六边形凹槽（让螺丝头沉入后壁外表面，视觉与其他凹槽一致）
# 注意：电磁铁本身有 M2 内螺纹，这里不嵌螺母；六边形 D=5.08 > M2 头径 3.8，刚好容纳螺丝头
for pz in (SOL_SCREW_Z_TOP, SOL_SCREW_Z_BOT):
    nut_pocket = make_hex_pocket_y(
        SOL_X_CENTER, pz,
        _nut_pocket_y_min, _nut_pocket_y_max,
        NUT_POCKET_DIAMETER
    )
    rear_half = rear_half.cut(nut_pocket)

# --- 装配方式：前壳 4 角矩形销凸出 → 塞进后壳 case 内角，靠 2 平面摩擦力固定 ---
# 前壳：4 角矩形柱本体 Y[-23.75, -10] + 矩形销凸出 Y[-10, -2]，销外两面贴外壁内表面
# 后壳：完全无柱，4 角是 case 外壁完整 L 形内角（X=W/2-WALL 平面 + Z=H-WALL 平面）
# 装配：前壳销凸出沿 -Y 滑入后壳 4 角 L 形墙角
#       销外两面（X 外 + Z 外）贴 case 外壁内表面 2 个内表面 = 2 个平面摩擦面
# M2 长螺丝穿过前壳柱 + 销中央通孔 + case 内腔空气 + case 后壁通孔，加螺母紧固
for px, pz in PILLAR_POSITIONS:
    # 前壳：union 销凸出（圆半径 = PIN_R = PILLAR_R - FIT，矩形外缘距外壁 FIT 间隙）
    pin = make_y_quarter_pillar(px, pz, Y_SPLIT, Y_SPLIT + ASM_PIN_L, PIN_R, wall_clearance=FIT_ASSEMBLY)
    pin_hole = make_y_pillar(px, pz, SCREW_HOLE_D/2, Y_SPLIT - 0.5, Y_SPLIT + ASM_PIN_L + 0.5)
    front_half = front_half.union(pin).cut(pin_hole)
    # 后壳无操作（柱已在 case 主体 union，端面在 Y=-2 顶前壳销端面）

show(
    front_half.translate((0, -30, 0)),
    rear_half.translate((0, 30, 0)),
)

# --- 打印参数报告 ---
front_vol = front_half.val().Volume() / 1000
rear_vol = rear_half.val().Volume() / 1000
total_vol = front_vol + rear_vol

print("=" * 60)
print(f"外壳尺寸:  W={W:.1f}  D={D:.1f}  H={H:.1f}  mm  (限制 ≤ {MAX_EDGE})")
print(f"前壳体积:  {front_vol:.2f} cm³")
print(f"后壳体积:  {rear_vol:.2f} cm³")
print(f"总体积:    {total_vol:.2f} cm³  (免费打样券限制 ≤ {MAX_VOLUME_CM3})")
print(f"按钮弹片→下螺丝孔距:  {_btn_to_lower_screw:.1f} mm  (要求 [20, 30])")
print(f"驱动板侧隙:  {DRV_SIDE_GAP:.2f} mm  (薄段需 {DRV_THICK_BODY}+{FIT_DRV}={DRV_THICK_BODY + FIT_DRV})")
print(f"驱动板 Z:    {DRV_BOT_Z:.1f} → {DRV_TOP_Z:.1f}  (面包板顶 {_drv_bb_top_z}, 内顶 {H - WALL:.1f})")
print("=" * 60)
if total_vol > MAX_VOLUME_CM3:
    print(f"⚠ 体积超 {MAX_VOLUME_CM3} cm³，需要缩减 W/D/H 或减薄壁厚")
# 端子段贴壁 14mm 厚，X 内缘越过面包板边 → 与面包板上 ESP32+竖插杜邦线在 X/Z 重叠
_drv_term_inner_x = (W / 2 - WALL) - DRV_THICK_TERMINAL
if _drv_term_inner_x < BB_W / 2:
    print(f"⚠ 驱动板端子段内缘 X={_drv_term_inner_x:.1f} 越过面包板边 X={BB_W / 2:.1f}："
          f"该侧 Z[{DRV_BOT_Z:.0f}, ~{MIN_BTN_BOTTOM_Z}] 的杜邦线需让开（CAD 无法约束接线）")

# --- 导出 STL ---
if EXPORT_STL:
    import os
    out_dir = os.path.dirname(os.path.abspath(__file__))
    front_path = os.path.join(out_dir, "case_front.stl")
    rear_path = os.path.join(out_dir, "case_rear.stl")
    cq.exporters.export(front_half, front_path, tolerance=0.05, angularTolerance=0.1)
    cq.exporters.export(rear_half, rear_path, tolerance=0.05, angularTolerance=0.1)
    print(f"前壳已导出: {front_path}")
    print(f"后壳已导出: {rear_path}")
