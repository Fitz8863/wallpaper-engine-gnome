#!/usr/bin/env python3
"""挂起唤醒守护 —— 监听 login1 的 PrepareForSleep,唤醒后自动重启壁纸。

为什么要重启进程:挂起/唤醒会重新协商显示模式(实测内屏 2560x1600 →
2048x1280),渲染器窗口持有的全屏态与尺寸是唤醒前的,克隆到新布局后
顶栏/Dock 区域(工作区外的那一圈)铺不上——扩展只能重建克隆,救不了
窗口本身。重启渲染器是唯一确定的恢复手段(实测重启即全覆盖)。

生命周期:由选择器应用拉起(--stop 时杀掉),幂等(已有实例则退出)。
"""

import os
import signal
import subprocess
import sys
import time

STATE_DIR = os.environ.get(
    "LWE_STATE_DIR",
    os.path.join(os.environ.get("XDG_CACHE_HOME", f"{os.path.expanduser('~')}/.cache"),
                 "wallpaper-picker"))
PIDFILE = os.path.join(STATE_DIR, "suspend-daemon.pid")
WAKER = os.path.join(os.path.dirname(os.path.realpath(__file__)), "start-wallpaper.sh")

log_file = open(os.path.join(STATE_DIR, "suspend-daemon.log"), "a", buffering=1)


def log(msg):
    log_file.write(f"{time.strftime('%m-%d %H:%M:%S')} {msg}\n")


def wallpaper_running():
    try:
        pid = open(os.path.join(STATE_DIR, "wallpaper.pid")).read().strip()
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False


def on_wake():
    """唤醒后的恢复:等 Mutter 重新协商完布局再重启(通常 1-3 秒)。"""
    for attempt in range(3):
        time.sleep(3)
        try:
            if not wallpaper_running():
                log("唤醒后壁纸未在运行,跳过恢复")
                return
            r = subprocess.run(
                [sys.executable, os.path.join(os.path.dirname(WAKER),
                                              "wallpaper_picker", "paths.py"),
                 "screens"],
                capture_output=True, text=True, timeout=10)
            if r.returncode == 0 and r.stdout.strip():
                break
        except Exception:
            continue
    log("唤醒:重启壁纸以适配新的显示布局")
    try:
        subprocess.run(["bash", WAKER, "--apply-plan"],
                       capture_output=True, text=True, timeout=60)
        log("恢复完成")
    except Exception as exc:
        log(f"恢复失败: {exc}")


def main():
    os.makedirs(STATE_DIR, exist_ok=True)
    # 幂等:已有守护在跑就退出(启动器/应用每次启动都会拉一份)
    try:
        old = open(PIDFILE).read().strip()
        os.kill(int(old), 0)
        return 0
    except Exception:
        pass
    open(PIDFILE, "w").write(str(os.getpid()))

    import gi
    gi.require_version("GLib", "2.0")
    from gi.repository import GLib

    import threading

    def on_prepare(_bus, _sender, _path, _iface, _signal, params):
        sleeping = params.unpack()[0]
        if sleeping:
            log("进入挂起")
        else:
            log("唤醒:安排恢复")
            threading.Thread(target=on_wake, daemon=True).start()

    def shutdown(_signum, _frame):
        try:
            os.remove(PIDFILE)
        except OSError:
            pass
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    try:
        import gi
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio, GLib

        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        bus.signal_subscribe(
            "org.freedesktop.login1", "org.freedesktop.login1.Manager",
            "PrepareForSleep", "/org/freedesktop/login1", None,
            Gio.DBusSignalFlags.NONE, on_prepare)
        log(f"守护启动 (pid {os.getpid()})")
        GLib.MainLoop().run()
    except Exception as exc:
        log(f"守护退出: {exc}")
        try:
            os.remove(PIDFILE)
        except OSError:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
