#!/usr/bin/env python3
"""生成 wallpaper-picker 的应用图标：同一份 draw() 同时输出 SVG 和各尺寸 PNG。

为什么不用 SVG 文件直接分发：hicolor 的 scalable SVG 依赖目标机器的
gdk-pixbuf SVG 加载器（librsvg），实测存在注册了加载器却加载失败的环境；
PNG 没有任何运行时依赖，所以发布以 PNG 为主，SVG 只是配套的矢量源。

用法：
    python3 icons/generate.py      # 重新生成 icons/ 下所有产物
"""

import math
import os

import cairo

SIZE = 256          # 设计基准坐标，所有尺寸都从这里等比缩放
ICON_NAME = "io.github.fitz.WallpaperPicker"
HERE = os.path.dirname(os.path.realpath(__file__))


def hex_rgb(s):
    return tuple(int(s[i:i + 2], 16) / 255 for i in (1, 3, 5))


SKY_TOP = hex_rgb("#191c38")
SKY_MID = hex_rgb("#32305c")
SKY_BOT = hex_rgb("#5a4a7c")
MOON = hex_rgb("#f6e7b4")
RIDGE_TOP = hex_rgb("#262445")
RIDGE_BOT = hex_rgb("#141327")


def rounded_rect(ctx, x, y, w, h, r):
    # 四个圆心都内缩 r，按右上→右下→左下→左上的顺序连弧
    ctx.new_sub_path()
    ctx.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    ctx.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    ctx.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    ctx.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    ctx.close_path()


def sky_paint(ctx):
    """夜空渐变。月牙的「缺口」也要用同一份渐变重涂一次才能融进背景，
    所以单独提成函数（userSpaceOnUse 坐标系）。"""
    sky = cairo.LinearGradient(0, 8, 0, 248)
    sky.add_color_stop_rgb(0.0, *SKY_TOP)
    sky.add_color_stop_rgb(0.55, *SKY_MID)
    sky.add_color_stop_rgb(1.0, *SKY_BOT)
    ctx.set_source(sky)
    ctx.paint()


def draw(ctx):
    ctx.save()
    rounded_rect(ctx, 8, 8, 240, 240, 52)
    ctx.clip()

    sky_paint(ctx)   # 夜空

    # 星
    for cx, cy, r, a in ((54, 60, 2.6, 0.8), (98, 42, 1.8, 0.5),
                         (224, 96, 2.2, 0.6), (66, 112, 1.8, 0.4),
                         (140, 30, 1.6, 0.45)):
        ctx.set_source_rgba(1, 1, 1, a)
        ctx.arc(cx, cy, r, 0, 2 * math.pi)
        ctx.fill()

    # 四角星
    ctx.set_source_rgba(1, 1, 1, 0.7)
    cx, cy, outer, inner = 120, 78, 8, 3
    pts = []
    for i in range(4):
        ang = -math.pi / 2 + i * math.pi / 2
        pts.append((cx + outer * math.cos(ang), cy + outer * math.sin(ang)))
        ang2 = ang + math.pi / 4
        pts.append((cx + inner * math.cos(ang2), cy + inner * math.sin(ang2)))
    ctx.move_to(*pts[0])
    for p in pts[1:]:
        ctx.line_to(*p)
    ctx.close_path()
    ctx.fill()

    # 新月：亮圆被「背景色圆」咬掉一口。咬痕要在裁剪到该圆的范围内
    # 重涂天空渐变，坐标相同所以和背景严丝合缝。月晕用径向渐变，
    # 边缘自然衰减，不会有实心圆的硬边。
    glow = cairo.RadialGradient(172, 74, 20, 172, 74, 46)
    glow.add_color_stop_rgba(0.0, *MOON, 0.28)
    glow.add_color_stop_rgba(1.0, *MOON, 0.0)
    ctx.set_source(glow)
    ctx.arc(172, 74, 46, 0, 2 * math.pi)
    ctx.fill()
    ctx.set_source_rgb(*MOON)
    ctx.arc(172, 74, 28, 0, 2 * math.pi)
    ctx.fill()
    ctx.save()
    ctx.arc(184, 64, 24, 0, 2 * math.pi)
    ctx.clip()
    sky_paint(ctx)
    ctx.restore()

    # 远山
    ctx.set_source_rgba(*hex_rgb("#3b3562"), 0.92)
    ctx.move_to(8, 214)
    ctx.line_to(72, 118)
    ctx.line_to(128, 202)
    ctx.line_to(128, 248)
    ctx.line_to(8, 248)
    ctx.close_path()
    ctx.fill()

    ctx.set_source_rgba(*hex_rgb("#2d2950"), 0.95)
    ctx.move_to(72, 248)
    ctx.line_to(152, 126)
    ctx.line_to(248, 232)
    ctx.line_to(248, 248)
    ctx.close_path()
    ctx.fill()

    # 近景山脊（带纵向渐变）
    ridge = cairo.LinearGradient(0, 150, 0, 248)
    ridge.add_color_stop_rgb(0.0, *RIDGE_TOP)
    ridge.add_color_stop_rgb(1.0, *RIDGE_BOT)
    ctx.set_source(ridge)
    ctx.move_to(8, 248)
    ctx.line_to(62, 186)
    ctx.line_to(116, 248)
    ctx.close_path()
    ctx.fill()
    ctx.move_to(92, 248)
    ctx.line_to(170, 172)
    ctx.line_to(248, 248)
    ctx.close_path()
    ctx.fill()

    ctx.restore()


def render(target, kind, size=None):
    if kind == "svg":
        surface = cairo.SVGSurface(target, SIZE, SIZE)
        draw(cairo.Context(surface))
        surface.finish()
    else:
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
        ctx = cairo.Context(surface)
        ctx.scale(size / SIZE, size / SIZE)
        draw(ctx)
        surface.write_to_png(target)


if __name__ == "__main__":
    render(os.path.join(HERE, f"{ICON_NAME}.svg"), "svg")
    for s in (48, 64, 128, 256):
        d = os.path.join(HERE, "hicolor", f"{s}x{s}", "apps")
        os.makedirs(d, exist_ok=True)
        render(os.path.join(d, f"{ICON_NAME}.png"), "png", s)
    print("图标已生成：SVG + " + "/".join(str(s) for s in (48, 64, 128, 256)))
