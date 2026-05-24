"""
小克物理状态机 — 外壳 v0.12

v0.11 → v0.12 改动：
1. 顶部 16mm 圆孔 → 12mm（电磁铁前柱 8mm + 每边 2mm 余量）
2. 加微动开关支架：在 U 形框下方延伸出按钮腔
   - 按钮腔 = 一个长方盒（带按钮坑），压杆朝上对准电磁铁后端
   - 按钮被腔壁夹住固定（按钮本体加 0.2mm 余量装配)
   - 顶部 6mm 圆孔让压杆穿出顶电磁铁后端
   - 底部矩形开口让引脚 + 杜邦线穿出
   - 通过 2 根连接柱挂在 U 形框底部
3. 按钮假设：12.8 × 6.5 × 7（X×Y×Z），压杆朝 +Z，引脚朝 -Z
   不对的话改 BTN_W / BTN_D / BTN_H

未做：Y 切分缝（前后分壳），下版做
"""

import cadquery as cq
from ocp_vscode import show
import math

# ============ 整体尺寸 ============
W = 60
D = 50
BASE_H = 55
BAY_H = 42
TOP_H = 30
TILT_ANGLE = 20
PROTRUDE = BAY_H * math.tan(math.radians(TILT_ANGLE))
WALL = 2.5
H = BASE_H + BAY_H + TOP_H
FILLET_R = 2

# ============ 屏幕双层开窗 ============
SCREEN_VW = 23.5
SCREEN_VH = 13
SCREEN_BOARD_W = 32
SCREEN_BOARD_H = 30
SCREEN_WIN_PAD = 0.5             # 屏幕开窗每边余量

SCREEN_OFFSET_UP = 8             # 屏幕开窗相对斜面中心向上偏移
                                  # （PCB 居中粘在前壁内侧，屏幕在 PCB 上半部分）

# ============ 电磁铁 + U 形框 ============
SOL_W = 13
SOL_H = 15.5
SOL_BODY_L = 30
SOL_SCREW_HOLE_D = 2.5
SOL_SCREW_SPACING = 15

SOL_X_CENTER = 0
SOL_Y_CENTER = 0
SOL_Z_TOP = H - WALL
SOL_Z_BOT = SOL_Z_TOP - SOL_BODY_L
SOL_Z_CENTER = (SOL_Z_TOP + SOL_Z_BOT) / 2

SOL_SCREW_Z_TOP = SOL_Z_CENTER + SOL_SCREW_SPACING / 2
SOL_SCREW_Z_BOT = SOL_Z_CENTER - SOL_SCREW_SPACING / 2

CLAMP_T = 2
CLAMP_OUTER_W = SOL_W + 2*CLAMP_T
CLAMP_OUTER_D = SOL_H + CLAMP_T
CLAMP_OUTER_H = SOL_BODY_L
CLAMP_X_CENTER = SOL_X_CENTER
CLAMP_Y_CENTER = SOL_Y_CENTER + CLAMP_T/2
CLAMP_Z_CENTER = SOL_Z_CENTER

# ============ 微动按钮 + 支架 ============
BTN_W = 12.8                 # 按钮长（X 方向）
BTN_D = 5.8                  # 按钮宽（Y 方向）
BTN_H = 6.5                  # 按钮本体高（Z 方向，不含杠杆和引脚）
BTN_LEVER_L = 11             # 杠杆长度（沿 X 方向）
BTN_LEVER_HEIGHT = 2         # 杠杆静止最高翘起 (mm)
BTN_PIN_H = 3.6              # 引脚长度
BTN_FIT_PAD = 0.2            # 按钮坑装配余量（每边）

# === 按钮位置调整参数（跑完看不对就改这三个数字）===
BTN_X_OFFSET = 0             # 按钮中心 X 偏移（左右调整）
BTN_Y_OFFSET = 0             # 按钮中心 Y 偏移
BTN_Z_OFFSET = 0             # 按钮 Z 偏移（上下调整，+ 往上）

# 按钮位置：弹片距电磁铁线圈底 (Z=94.5) = 17.5mm
# 即弹片 Z = 77，距下螺丝孔 (Z=102) = 25mm（在用户要求的 [20, 30] mm 范围中间）
# 这样电磁铁有 10mm 行程余地工作：底柱伸出 ~12mm 时（静止状态）距弹片 5.5mm，
# 通电后底柱再伸出 ~10mm（达到 ~22mm）压住弹片，触发按钮
BTN_TO_COIL_BOTTOM = 17.5
BTN_CENTER_X = BTN_X_OFFSET
BTN_CENTER_Y = CLAMP_Y_CENTER + BTN_Y_OFFSET
BTN_LEVER_TOP_Z = SOL_Z_BOT - BTN_TO_COIL_BOTTOM + BTN_Z_OFFSET            # 77
BTN_BODY_TOP_Z = BTN_LEVER_TOP_Z - BTN_LEVER_HEIGHT                        # 75
BTN_BODY_BOT_Z = BTN_BODY_TOP_Z - BTN_H                                    # 68.5

# 按钮坑余量（按钮 X、Z 方向可滑动，Y 方向紧贴）
BTN_X_PLAY = 10      # X 方向余量（按钮可调 ±5mm）
BTN_Z_PLAY = 10      # Z 方向余量（按钮可调 ±5mm）

# 按钮坑（Y 紧贴 5.8mm 不可调，X/Z 留余量可调）
BTN_POCKET_W = BTN_W + BTN_X_PLAY                           # 22.8 (X)
BTN_POCKET_D = BTN_D + 2*BTN_FIT_PAD                        # 6.2 (Y) 紧贴
BTN_POCKET_H = BTN_H + BTN_Z_PLAY                            # 16.5 (Z)
BTN_POCKET_CENTER_Z = (BTN_BODY_TOP_Z + BTN_BODY_BOT_Z) / 2  # 81.25

# 按钮支架（仅 4 壁，顶面和底面都开放 — 按钮从下方塞入）
# Holder 宽度比按钮坑额外加宽 9.2mm，给螺丝留足边缘余量（3D 打印强度）
BTN_HOLDER_WALL = 2
BTN_HOLDER_W = 32                                            # 加宽到 32mm（每边壁厚 4.6mm）
BTN_HOLDER_D = BTN_POCKET_D + 2*BTN_HOLDER_WALL              # 10.2
BTN_HOLDER_BOT_Z = BTN_POCKET_CENTER_Z - BTN_POCKET_H/2      # 73（底面开放）
BTN_HOLDER_TOP_Z = BTN_POCKET_CENTER_Z + BTN_POCKET_H/2      # 89.5（顶面开放）
BTN_HOLDER_H = BTN_HOLDER_TOP_Z - BTN_HOLDER_BOT_Z           # 16.5
BTN_HOLDER_Z_CENTER = (BTN_HOLDER_TOP_Z + BTN_HOLDER_BOT_Z) / 2

# 螺丝固定（M2 通孔，Y 方向贯穿 holder 前后壁，左右各 1 个共 2 个）
# 螺丝从前壁外拧入，穿过 holder 内空隙（按钮 X 两侧），从后壁出来，用螺母固定
# 离 holder 边缘 (16) 距离 = 16 - 13 - 1.25 = 1.75mm 足够 3D 打印
BTN_FIX_SCREW_D = 2.5
BTN_FIX_X_DIST = 13    # 螺丝 X 距按钮中心 13mm（让按钮可调 ±5mm + 螺丝离边缘 1.75mm）

# 支撑柱：从 case 顶板内表面伸下到 holder 顶（绕过 U 形框两侧）
SUPPORT_PILLAR_W = 3
SUPPORT_TOP_Z = H - WALL
SUPPORT_BOT_Z = BTN_HOLDER_TOP_Z
SUPPORT_H = SUPPORT_TOP_Z - SUPPORT_BOT_Z                    # 38
SUPPORT_CENTER_Z = (SUPPORT_TOP_Z + SUPPORT_BOT_Z) / 2

# ============ 其他开孔 ============
TOP_HOLE_D = 8               # 电磁铁前柱直径，没有余量（紧贴）
USB_W = 14
USB_HOLE_H = 9
USB_Z = 20

# ============ 主体侧视轮廓 ============
profile = [
    ( D/2,                    0              ),
    (-D/2,                    0              ),
    (-D/2,                    BASE_H         ),
    (-D/2 - PROTRUDE,         BASE_H         ),
    (-D/2,                    BASE_H + BAY_H ),
    (-D/2,                    H              ),
    ( D/2,                    H              ),
]

# ============ 外壳 + 圆角 ============
outer = (
    cq.Workplane("YZ")
    .polyline(profile)
    .close()
    .extrude(W/2, both=True)
    .edges()
    .fillet(FILLET_R)
)

# ============ 内腔 ============
inner_cavity = (
    cq.Workplane("YZ")
    .polyline(profile)
    .close()
    .offset2D(-WALL, kind="intersection")
    .extrude((W - 2*WALL)/2, both=True)
)

# ============ 屏幕双层开窗 ============
SCREEN_PLANE_ROT = 90 - TILT_ANGLE
cos_t = math.cos(math.radians(TILT_ANGLE))
sin_t = math.sin(math.radians(TILT_ANGLE))

# 外层小窗（屏幕可视区贯穿前壁）
# plane center 在前壁中心 + 沿斜面向上偏移 SCREEN_OFFSET_UP
wall_mid_offset_screen = (
    0,
    -D/2 - PROTRUDE/2 + (WALL/2) * cos_t + SCREEN_OFFSET_UP * sin_t,
    BASE_H + BAY_H/2 - (WALL/2) * sin_t + SCREEN_OFFSET_UP * cos_t,
)
screen_window_outer = (
    cq.Workplane("XY")
    .transformed(offset=wall_mid_offset_screen, rotate=(SCREEN_PLANE_ROT, 0, 0))
    .rect(SCREEN_VW + 2*SCREEN_WIN_PAD, SCREEN_VH + 2*SCREEN_WIN_PAD)
    .extrude(WALL/2 + 0.5, both=True)
)

# ============ U 形框 ============
clamp_outer_box = (
    cq.Workplane("XY")
    .box(CLAMP_OUTER_W, CLAMP_OUTER_D, CLAMP_OUTER_H)
    .translate((CLAMP_X_CENTER, CLAMP_Y_CENTER, CLAMP_Z_CENTER))
)
clamp_inner_box = (
    cq.Workplane("XY")
    .box(SOL_W, 100, SOL_BODY_L + 100)
    .translate((
        SOL_X_CENTER,
        SOL_Y_CENTER - 50 + SOL_H/2,
        SOL_Z_CENTER
    ))
)
clamp_frame = clamp_outer_box.cut(clamp_inner_box)

# ============ 微动开关支架（Y 紧贴，X/Z 可滑动，4 颗 M2 螺丝从前后锁定）============
# Holder outer
btn_holder_outer = (
    cq.Workplane("XY")
    .box(BTN_HOLDER_W, BTN_HOLDER_D, BTN_HOLDER_H)
    .translate((BTN_CENTER_X, BTN_CENTER_Y, BTN_HOLDER_Z_CENTER))
)
# Inner: 按钮坑（含 X/Z 余量）+ 顶底都贯穿（用户从下方塞入按钮，杠杆从顶面翘起）
btn_holder_inner = (
    cq.Workplane("XY")
    .box(BTN_POCKET_W, BTN_POCKET_D, BTN_HOLDER_H + 20)   # Z 超出，顶底都开
    .translate((BTN_CENTER_X, BTN_CENTER_Y, BTN_HOLDER_Z_CENTER))
)
# 2 个 Y 方向螺丝通孔（左右各 1 个，从前壁穿到后壁，用螺母锁紧）
btn_fix_holes = (
    cq.Workplane("XZ", origin=(0, BTN_CENTER_Y, 0))
    .pushPoints([
        (BTN_CENTER_X - BTN_FIX_X_DIST, BTN_POCKET_CENTER_Z),
        (BTN_CENTER_X + BTN_FIX_X_DIST, BTN_POCKET_CENTER_Z),
    ])
    .circle(BTN_FIX_SCREW_D / 2)
    .extrude(BTN_HOLDER_D/2 + 2, both=True)
)
btn_holder = (
    btn_holder_outer
    .cut(btn_holder_inner)
    .cut(btn_fix_holes)
)

# 支撑柱（从 case 顶板内表面伸下到 holder 顶，2 根，绕过 U 形框两侧）
support_left = (
    cq.Workplane("XY")
    .box(SUPPORT_PILLAR_W, BTN_HOLDER_D, SUPPORT_H)
    .translate((
        BTN_CENTER_X - BTN_HOLDER_W/2 + SUPPORT_PILLAR_W/2,
        BTN_CENTER_Y,
        SUPPORT_CENTER_Z
    ))
)
support_right = (
    cq.Workplane("XY")
    .box(SUPPORT_PILLAR_W, BTN_HOLDER_D, SUPPORT_H)
    .translate((
        BTN_CENTER_X + BTN_HOLDER_W/2 - SUPPORT_PILLAR_W/2,
        BTN_CENTER_Y,
        SUPPORT_CENTER_Z
    ))
)
btn_support = btn_holder.union(support_left).union(support_right)

# ============ 顶部 16mm 圆孔 ============
top_hole = (
    cq.Workplane("XY")
    .circle(TOP_HOLE_D / 2)
    .extrude(WALL + 3)
    .translate((SOL_X_CENTER, SOL_Y_CENTER, H - WALL - 1))
)

# ============ 电磁铁 M2 通孔（贯穿 case 后壁 + 内腔 + U 形框后壁）============
# 用 M2x25 长螺丝从 case 后壁外拧入电磁铁后面 M2 孔
# 路径：case 后壁外(Y=25) → case 后壁(2.5) → 空心支撑柱(12.75) → U 形框后壁(2) → 电磁铁
clamp_screw_holes = (
    cq.Workplane("XZ", origin=(0, D/2 + 2, 0))
    .pushPoints([
        (SOL_X_CENTER, SOL_SCREW_Z_TOP),
        (SOL_X_CENTER, SOL_SCREW_Z_BOT)
    ])
    .circle(SOL_SCREW_HOLE_D / 2)
    .extrude(22)   # 从 Y=+27 朝 -Y 22mm，覆盖 case 后壁 + 内腔 + U 形框后壁
)

# ============ 电磁铁螺丝支撑柱（防止 M2x25 长螺丝拧紧时变形）============
# 2 根实心圆柱，从 U 形框后壁外（Y=9.75）延伸到 case 后内壁（Y=22.5）
# 柱外径 5mm（含壁厚 1.25mm + 内通孔 2.5mm）
# 柱内通孔靠 clamp_screw_holes cut 自动形成
clamp_support_y_min = SOL_Y_CENTER + SOL_H/2 + CLAMP_T   # 9.75
clamp_support_y_max = D/2 - WALL                          # 22.5
clamp_support_y_length = clamp_support_y_max - clamp_support_y_min  # 12.75
clamp_support_d = 6                                       # 柱外径（壁厚 1.75mm，≥ 1.2mm 安全）

clamp_support_top = (
    cq.Workplane("XY")
    .transformed(rotate=(-90, 0, 0))
    .circle(clamp_support_d / 2)
    .extrude(clamp_support_y_length)
    .translate((SOL_X_CENTER, clamp_support_y_min, SOL_SCREW_Z_TOP))
)
clamp_support_bot = (
    cq.Workplane("XY")
    .transformed(rotate=(-90, 0, 0))
    .circle(clamp_support_d / 2)
    .extrude(clamp_support_y_length)
    .translate((SOL_X_CENTER, clamp_support_y_min, SOL_SCREW_Z_BOT))
)
clamp_support_pillars = clamp_support_top.union(clamp_support_bot)

# ============ 后部 USB 出口 ============
usb_hole = (
    cq.Workplane("XY")
    .box(USB_W, WALL*4, USB_HOLE_H)
    .translate((0, D/2 - WALL/2, USB_Z))
)

# ============ 合并 ============
case = (
    outer
    .cut(inner_cavity)
    .union(clamp_frame)
    .union(btn_support)
    .union(clamp_support_pillars)
    .cut(screen_window_outer)
    .cut(top_hole)
    .cut(clamp_screw_holes)
    .cut(usb_hole)
)

# ============ 拆装机制：4 角螺丝柱 + Y 切前后分壳 ============
# 4 个螺丝柱在 case 4 角内部，沿 Y 方向贯穿（前壳到后壳）
# M2x50 长螺丝 + M2 螺母固定
SCREW_PILLAR_D = 6                                              # 柱外径（壁厚 1.75mm，≥ 1.2mm 安全）
SCREW_HOLE_D = 2.5                                              # M2 通孔（带余量）
SCREW_PILLAR_X = W/2 - WALL - SCREW_PILLAR_D/2 - 1              # 24
SCREW_PILLAR_Z_BOT = WALL + SCREW_PILLAR_D/2 + 1                # 6
SCREW_PILLAR_Z_TOP = H - WALL - SCREW_PILLAR_D/2 - 1            # 119

PILLAR_POSITIONS = [
    (-SCREW_PILLAR_X, SCREW_PILLAR_Z_BOT),
    (+SCREW_PILLAR_X, SCREW_PILLAR_Z_BOT),
    (-SCREW_PILLAR_X, SCREW_PILLAR_Z_TOP),
    (+SCREW_PILLAR_X, SCREW_PILLAR_Z_TOP),
]


def make_y_pillar(x, z, radius, y_min, y_max):
    """创建一个沿 Y 方向延伸的圆柱"""
    length = y_max - y_min
    return (
        cq.Workplane("XY")
        .transformed(rotate=(-90, 0, 0))   # 绕 X 轴旋转 -90°，让 plane Z → 世界 +Y
        .circle(radius)
        .extrude(length)
        .translate((x, y_min, z))
    )


# 4 个螺丝柱（贯穿 case 内腔 Y range）
pillar_y_min = -(D/2 - WALL)
pillar_y_max = +(D/2 - WALL)
screw_pillars = make_y_pillar(*PILLAR_POSITIONS[0], SCREW_PILLAR_D/2, pillar_y_min, pillar_y_max)
for px, pz in PILLAR_POSITIONS[1:]:
    screw_pillars = screw_pillars.union(make_y_pillar(px, pz, SCREW_PILLAR_D/2, pillar_y_min, pillar_y_max))

case = case.union(screw_pillars)

# 4 个通孔（贯穿 case 外壁，让 M2 螺丝穿过）
hole_y_min = -(D/2 + 2)
hole_y_max = +(D/2 + 2)
screw_holes = make_y_pillar(*PILLAR_POSITIONS[0], SCREW_HOLE_D/2, hole_y_min, hole_y_max)
for px, pz in PILLAR_POSITIONS[1:]:
    screw_holes = screw_holes.union(make_y_pillar(px, pz, SCREW_HOLE_D/2, hole_y_min, hole_y_max))

case = case.cut(screw_holes)

# Y=Y_SPLIT 平面切两半
# Y_SPLIT = -10：分缝在屏幕仓之后、U 形框之前
# 这样 U 形框 + 按钮 holder + 支撑柱 + USB 都完整在后壳
# 前壳只含屏幕仓 + 案前部（含屏幕窗）
Y_SPLIT = -10
_big = max(W, D, H) + 20
front_half = case.intersect(
    cq.Workplane("XY").box(_big, _big, _big).translate((0, Y_SPLIT - _big/2, H/2))
)
rear_half = case.intersect(
    cq.Workplane("XY").box(_big, _big, _big).translate((0, Y_SPLIT + _big/2, H/2))
)

# 显示：前后壳沿 Y 方向各拉开 30mm，方便看接合面 + 内部结构
show(
    front_half.translate((0, -30, 0)),
    rear_half.translate((0, 30, 0)),
)

# ============ 导出 STL（要导出时把 EXPORT_STL 改为 True，重跑一次）============
EXPORT_STL = True

if EXPORT_STL:
    import os
    out_dir = os.path.dirname(os.path.abspath(__file__))
    front_path = os.path.join(out_dir, "case_front.stl")
    rear_path = os.path.join(out_dir, "case_rear.stl")
    cq.exporters.export(front_half, front_path, tolerance=0.05, angularTolerance=0.1)
    cq.exporters.export(rear_half, rear_path, tolerance=0.05, angularTolerance=0.1)
    print(f"前壳已导出: {front_path}")
    print(f"后壳已导出: {rear_path}")
