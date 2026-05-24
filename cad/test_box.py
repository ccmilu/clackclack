"""
CadQuery + OCP CAD Viewer 验证用 hello world。
跑通这个文件就说明环境 OK，可以开始正经设计外壳。

运行方式：
  在 VS Code 中打开本文件，把 Python 解释器选为 cadquery-viewer 环境
  按 Ctrl+F5（macOS: Ctrl+F5 也行）
  右侧 OCP CAD Viewer 面板会出现一个倒了圆角的盒子
"""

import cadquery as cq
from ocp_vscode import show

# 简单参数化盒子：80×50×25mm，所有竖边倒 3mm 圆角
box = (
    cq.Workplane("XY")
    .box(80, 50, 25)
    .edges("|Z")
    .fillet(3)
)

show(box)
