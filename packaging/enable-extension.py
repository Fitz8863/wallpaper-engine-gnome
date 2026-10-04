#!/usr/bin/env python3
"""把本包的 GNOME 扩展加入/移出当前用户的启用列表。

由 deb 的 postinst / prerm 以「发起安装的那个用户」身份调用，
也可以在用户自己的会话里手动跑：

    python3 ./enable-extension.py            # 启用
    python3 ./enable-extension.py --disable  # 禁用
"""

import ast
import os
import subprocess
import sys

UUID = "linux-wallpaperengine@github.io"

# 扩展可能存在的两个位置。卸载 deb 时如果另一处还有副本，
# 就不能把这个 uuid 从启用列表里摘掉，否则会把那份也一起禁用。
LOCATIONS = (
    os.path.expanduser(f"~/.local/share/gnome-shell/extensions/{UUID}"),
    f"/usr/share/gnome-shell/extensions/{UUID}",
)


def other_copy_exists(excluding_system):
    """除系统级那份以外，是否还有别的副本存在。"""
    for path in LOCATIONS:
        if excluding_system and path.startswith("/usr/share"):
            continue
        if os.path.exists(path):
            return True
    return False


def get_list():
    out = subprocess.run(
        ["gsettings", "get", "org.gnome.shell", "enabled-extensions"],
        capture_output=True, text=True)
    if out.returncode != 0:
        return None
    try:
        return list(ast.literal_eval(out.stdout.strip() or "[]"))
    except (ValueError, SyntaxError):
        return None


def set_list(items):
    return subprocess.run(
        ["gsettings", "set", "org.gnome.shell", "enabled-extensions",
         str(items)]).returncode == 0


def main():
    disable = "--disable" in sys.argv
    current = get_list()
    if current is None:
        print("读不到 GNOME 的扩展配置，跳过", file=sys.stderr)
        return 0

    if disable:
        if other_copy_exists(excluding_system=True):
            # 用户自己还装着一份（比如从源码目录软链过来的），保持启用
            return 0
        updated = [item for item in current if item != UUID]
        changed = len(updated) != len(current)
    else:
        if UUID in current:
            return 0
        updated = current + [UUID]
        changed = True

    if changed:
        set_list(updated)
    return 0


if __name__ == "__main__":
    sys.exit(main())
