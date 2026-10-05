#!/usr/bin/env python3
"""路径探测 —— 找出 Steam 库、创意工坊壁纸和渲染器在哪里。

被 wallpaper_picker/picker.py 和 start-wallpaper.sh 共用，保证两边行为一致。

查找顺序（先看环境变量，再自动探测）：

  创意工坊壁纸目录：
    1. $LWE_WORKSHOP
    2. 遍历所有 Steam 库（会读 libraryfolders.vdf，所以装在别的硬盘上也能找到）
    3. 几个常见的固定位置兜底

  渲染器二进制：
    1. $LWE_BIN
    2. ~/linux-wallpaperengine/build/output/linux-wallpaperengine

命令行自测：
    python3 lwe_paths.py workshop   # 打印创意工坊目录
    python3 lwe_paths.py renderer   # 打印渲染器路径
    python3 lwe_paths.py report     # 打印完整探测报告
"""

import os
import re
import sys

APP_ID = "431960"  # Wallpaper Engine 在 Steam 上的 appid

HOME = os.path.expanduser("~")

# Steam 客户端可能的根目录
STEAM_ROOTS = [
    f"{HOME}/.steam/steam",
    f"{HOME}/.steam/debian-installation",       # Ubuntu 官方 deb 包
    f"{HOME}/.local/share/Steam",
    f"{HOME}/.var/app/com.valvesoftware.Steam/.local/share/Steam",  # Flatpak
    f"{HOME}/snap/steam/common/.local/share/Steam",                 # Snap
]

# 找不到 vdf 时的固定位置兜底
FALLBACK_WORKSHOP = [
    f"{HOME}/.local/share/Steam/steamapps/workshop/content/{APP_ID}",
    f"{HOME}/.steam/steam/steamapps/workshop/content/{APP_ID}",
    f"{HOME}/.steam/debian-installation/steamapps/workshop/content/{APP_ID}",
    f"{HOME}/.var/app/com.valvesoftware.Steam/.local/share/Steam"
    f"/steamapps/workshop/content/{APP_ID}",
]

DEFAULT_RENDERER = f"{HOME}/linux-wallpaperengine/build/output/linux-wallpaperengine"


def steam_libraries():
    """列出这个系统上所有 Steam 库目录。

    Steam 把库路径记在 <root>/steamapps/libraryfolders.vdf 里，形如::

        "libraryfolders"
        {
            "0"
            {
                "path"      "/home/user/.steam/debian-installation"
                ...
            }
        }

    直接解析它，用户把库放在别的硬盘上也能找到。
    """
    libraries = []
    for root in STEAM_ROOTS:
        # 根目录本身也是一个库
        if os.path.isdir(os.path.join(root, "steamapps")):
            libraries.append(root)

        vdf = os.path.join(root, "steamapps", "libraryfolders.vdf")
        if not os.path.isfile(vdf):
            continue
        try:
            with open(vdf, encoding="utf-8", errors="replace") as fh:
                content = fh.read()
        except OSError:
            continue
        # 匹配 "path" 后面跟着的路径，转义反斜杠（Windows 风格存档也可能出现）
        for raw in re.findall(r'"path"\s+"([^"]+)"', content):
            path = raw.replace("\\\\", "/")
            if path not in libraries and os.path.isdir(path):
                libraries.append(path)

    # 去重且保持顺序
    seen, unique = set(), []
    for path in libraries:
        real = os.path.realpath(path)
        if real not in seen:
            seen.add(real)
            unique.append(path)
    return unique


def find_workshop():
    """返回创意工坊壁纸目录，找不到返回 None。"""
    override = os.environ.get("LWE_WORKSHOP")
    if override:
        return override if os.path.isdir(override) else None

    for library in steam_libraries():
        candidate = os.path.join(library, "steamapps", "workshop",
                                 "content", APP_ID)
        if os.path.isdir(candidate):
            return candidate

    for candidate in FALLBACK_WORKSHOP:
        if os.path.isdir(candidate):
            return candidate
    return None


def find_assets():
    """返回 Wallpaper Engine 本体的 assets 目录（渲染器需要，用于场景壁纸）。"""
    override = os.environ.get("LWE_ASSETS")
    if override:
        return override if os.path.isdir(override) else None

    for library in steam_libraries():
        candidate = os.path.join(library, "steamapps", "common",
                                 "wallpaper_engine", "assets")
        if os.path.isdir(candidate):
            return candidate
    return None


def find_renderer():
    """返回渲染器二进制路径（不校验是否存在，交给调用方判断）。"""
    return os.environ.get("LWE_BIN") or DEFAULT_RENDERER


def find_screen():
    """返回主显示器的连接器名（如 eDP-1 / HDMI-1 / DP-2）。

    这个值没有通用常量——笔记本内置屏通常是 eDP-1，外接屏可能是别的名字。
    所以这里向 Mutter 查询当前的主显示器，而不是猜。查不到才回落到 eDP-1。
    """
    override = os.environ.get("LWE_SCREEN")
    if override:
        return override

    try:
        # 只在需要时导入 gi，避免拖慢 workshop/renderer 这类查询
        from gi.repository import Gio

        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        reply = bus.call_sync(
            "org.gnome.Mutter.DisplayConfig",
            "/org/gnome/Mutter/DisplayConfig",
            "org.gnome.Mutter.DisplayConfig",
            "GetCurrentState",
            None, None, Gio.DBusCallFlags.NONE, 5000, None)

        # 返回签名大致是 (serial, monitors, logical_monitors, properties)，
        # logical_monitors 每项形如 (x, y, scale, transform, primary, [monitors], props)
        _serial, _monitors, logical = reply.unpack()[:3]
        for entry in logical:
            primary, refs = entry[4], entry[5]
            if primary and refs:
                # refs 里每项形如 (connector, vendor, product, serial)
                return refs[0][0]
        if logical and logical[0][5]:
            return logical[0][5][0][0]
    except Exception:
        pass

    return "eDP-1"


def report():
    lines = []
    lines.append("Steam 根目录 / 库：")
    libs = steam_libraries()
    lines.extend(f"  {path}" for path in libs) if libs else lines.append("  （未找到）")

    lines.append("")
    lines.append("创意工坊壁纸目录：")
    workshop = find_workshop()
    if workshop:
        try:
            count = len([d for d in os.listdir(workshop)
                         if os.path.isdir(os.path.join(workshop, d))])
        except OSError:
            count = "?"
        lines.append(f"  {workshop}（{count} 张）")
    else:
        lines.append("  （未找到——请确认 Steam 里已安装 Wallpaper Engine 并订阅壁纸）")

    lines.append("")
    lines.append("Wallpaper Engine assets：")
    lines.append(f"  {find_assets() or '（未找到）'}")

    lines.append("")
    lines.append("渲染器：")
    renderer = find_renderer()
    exists = "存在" if os.access(renderer, os.X_OK) else "不存在或不可执行"
    lines.append(f"  {renderer}（{exists}）")

    lines.append("")
    lines.append("主显示器：")
    lines.append(f"  {find_screen()}")
    return "\n".join(lines)


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "report"
    if action == "workshop":
        path = find_workshop()
        if path:
            print(path)
        else:
            sys.exit(1)
    elif action == "assets":
        path = find_assets()
        if path:
            print(path)
        else:
            sys.exit(1)
    elif action == "renderer":
        print(find_renderer())
    elif action == "screen":
        print(find_screen())
    else:
        print(report())
