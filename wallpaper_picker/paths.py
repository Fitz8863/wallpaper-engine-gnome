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
    python3 wallpaper_picker/paths.py workshop   # 打印创意工坊目录
    python3 wallpaper_picker/paths.py renderer   # 打印渲染器路径
    python3 wallpaper_picker/paths.py report     # 打印完整探测报告
"""

import json
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


def state_settings_path():
    """settings.json 的位置。

    规则与 picker.py 的 STATE_DIR、start-wallpaper.sh 的 STATE_DIR 一致
    （LWE_STATE_DIR > $XDG_CACHE_HOME/wallpaper-picker > ~/.cache/...），
    三处各写一份是既有布局，改动时要同步。
    """
    base = os.environ.get("LWE_STATE_DIR") or os.path.join(
        os.environ.get("XDG_CACHE_HOME", f"{HOME}/.cache"), "wallpaper-picker")
    return os.path.join(base, "settings.json")


def _chosen_workshop():
    """用户在设置里手动选择的壁纸目录（settings.json 的 workshop 键）。"""
    try:
        with open(state_settings_path(), encoding="utf-8") as fh:
            chosen = json.load(fh).get("workshop")
    except Exception:
        return None
    return chosen if chosen and os.path.isdir(chosen) else None


def _steam_workshop_detected():
    """自动探测：遍历 Steam 库，再走固定位置兜底。"""
    for library in steam_libraries():
        candidate = os.path.join(library, "steamapps", "workshop",
                                 "content", APP_ID)
        if os.path.isdir(candidate):
            return candidate

    for candidate in FALLBACK_WORKSHOP:
        if os.path.isdir(candidate):
            return candidate
    return None


def find_steam_workshop():
    """Steam 创意工坊目录，忽略环境变量与手动选择。

    启动器用来判定壁纸归属：渲染器对纯数字 --bg 固定去 Steam 工坊找，
    自定义目录里的壁纸必须传完整路径。这个查询必须与「用户当前生效
    目录」无关。
    """
    return _steam_workshop_detected()


def find_workshop():
    """返回当前生效的创意工坊壁纸目录，找不到返回 None。

    优先级：LWE_WORKSHOP 环境变量 > 设置里手动选择（settings.json 的
    workshop 键）> 自动探测。手动选择这一层让非 Steam 场景（第三方
    下载、手动整理的壁纸）也能用图形界面与命令行。
    """
    override = os.environ.get("LWE_WORKSHOP")
    if override:
        return override if os.path.isdir(override) else None

    chosen = _chosen_workshop()
    if chosen:
        return chosen

    return _steam_workshop_detected()


def wallpaper_bg_arg(wid):
    """渲染器 --bg 参数的实际传值。

    Steam 订阅壁纸传 ID（渲染器自己能按 ID 找到）；自定义目录里的壁纸
    传完整路径（渲染器 translateBackground 对含 / 的值按文件路径处理，
    纯数字才回 Steam 工坊找——自定义壁纸按 ID 传会失败，或读到 Steam 里
    同 ID 另一张壁纸的属性）。判定逻辑与 start-wallpaper.sh 保持一致，
    两处改动要同步。
    """
    steam_ws = find_steam_workshop()
    if steam_ws and os.path.isdir(os.path.join(steam_ws, wid)):
        return wid
    workshop = find_workshop()
    if workshop and workshop != steam_ws \
            and os.path.isdir(os.path.join(workshop, wid)):
        return os.path.join(workshop, wid)
    return wid


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


def parse_logical_monitors(logical):
    """把 Mutter 的 logical_monitors 列表解析成 [(connector, is_primary), ...]。

    输入每项形如 (x, y, scale, transform, primary, [monitors], props)，
    refs 里每项是 (connector, vendor, product, serial)。输出主屏排最前，
    其余按屏幕位置（先 y 后 x）排序——与用户「从左到右」的直觉一致，
    界面上按这个顺序展示。纯函数，喂构造数据即可测试。
    """
    parsed = []
    for entry in logical:
        refs = entry[5] if len(entry) > 5 else []
        if not refs:
            continue
        parsed.append({
            "connector": refs[0][0],
            "primary": bool(entry[4]),
            "x": entry[0],
            "y": entry[1],
        })
    parsed.sort(key=lambda s: (not s["primary"], s["y"], s["x"]))
    return [(s["connector"], s["primary"]) for s in parsed]


def _query_mutter_screens():
    """向 Mutter 查询全部逻辑显示器，失败返回 None。"""
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
        return parse_logical_monitors(reply.unpack()[2])
    except Exception:
        return None


def find_screens():
    """返回全部显示器 [(connector, is_primary), ...]，主屏排最前。

    多显示器支持的探测基础：渲染器可以逐屏指定 --screen-root，
    界面按这里给出的顺序展示与命名各块屏。Mutter 查询失败时回落
    到单屏（LWE_SCREEN 或 eDP-1，视为主屏）——单屏行为永远可用。
    """
    screens = _query_mutter_screens()
    if screens:
        return screens
    return [(os.environ.get("LWE_SCREEN") or "eDP-1", True)]


def find_screen():
    """返回主显示器的连接器名（如 eDP-1 / HDMI-1 / DP-2）。

    这个值没有通用常量——笔记本内置屏通常是 eDP-1，外接屏可能是别的名字。
    所以这里向 Mutter 查询当前的主显示器，而不是猜。查不到才回落到 eDP-1。
    """
    override = os.environ.get("LWE_SCREEN")
    if override:
        return override

    for connector, primary in find_screens():
        if primary:
            return connector
    return "eDP-1"


def report():
    lines = []
    lines.append("Steam 根目录 / 库：")
    libs = steam_libraries()
    lines.extend(f"  {path}" for path in libs) if libs else lines.append("  （未找到）")

    lines.append("")
    lines.append("创意工坊壁纸目录：")
    if os.environ.get("LWE_WORKSHOP"):
        source = "（来自环境变量 LWE_WORKSHOP）"
    elif _chosen_workshop():
        source = "（来自设置中的手动选择）"
    else:
        source = "（自动探测）"
    workshop = find_workshop()
    if workshop:
        try:
            count = len([d for d in os.listdir(workshop)
                         if os.path.isdir(os.path.join(workshop, d))])
        except OSError:
            count = "?"
        lines.append(f"  {workshop}{source}（{count} 张）")
    else:
        lines.append(f"  （未找到{source}——请确认 Steam 里已安装 "
                     "Wallpaper Engine 并订阅壁纸，或在设置里手动选择目录）")

    lines.append("")
    lines.append("Wallpaper Engine assets：")
    lines.append(f"  {find_assets() or '（未找到）'}")

    lines.append("")
    lines.append("渲染器：")
    renderer = find_renderer()
    exists = "存在" if os.access(renderer, os.X_OK) else "不存在或不可执行"
    lines.append(f"  {renderer}（{exists}）")

    lines.append("")
    lines.append("显示器（* 为主屏）：")
    for connector, primary in find_screens():
        lines.append(f"  {connector}{' *' if primary else ''}")
    return "\n".join(lines)


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "report"
    if action == "workshop":
        path = find_workshop()
        if path:
            print(path)
        else:
            sys.exit(1)
    elif action == "steam-workshop":
        # 与 find_workshop 的区别：忽略环境变量与手动选择，只认 Steam。
        # start-wallpaper.sh 用它判定壁纸该传 ID 还是完整路径。
        path = find_steam_workshop()
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
    elif action == "screens":
        # 多显示器支持：每行一个 connector，主屏带 * 标记
        for connector, primary in find_screens():
            print(f"{connector}{' *' if primary else ''}")
    else:
        print(report())
