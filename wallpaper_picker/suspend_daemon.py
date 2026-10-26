#!/usr/bin/env python3
"""壁纸守护 —— 挂起/锁屏后的渲染器自愈。

三个职责:

1. systemd-logind 的 PrepareForSleep:挂起/唤醒会重新协商显示模式,渲染器
   窗口持有旧的全屏态与尺寸,唤醒后需要重启渲染器(实测重启即恢复)。

2. org.gnome.ScreenSaver 的 ActiveChanged:锁屏时 GNOME 把会话切到
   'unlock-dialog',旧版扩展会整个被停用;解锁后扩展重新启用时,渲染器
   窗口已经处于「最小化」状态,`global.get_window_actors()` 找不到它,
   于是画面永远黑着。重跑一次启动器(渲染器窗口重新 map)即恢复。

3. 消费扩展写下的重启标记(~/.cache/wallpaper-picker/lwpe-needs-restart):
   扩展连续 5 秒找不到可克隆的画面源就会刷新该标记,本守护轮询并重启。
   扩展画面正常时会写 lwpe-healthy 心跳,守护据此跳过不必要的重启——
   新版扩展在锁屏时不会被停用,解锁后根本不需要重启,心跳就是判据。

生命周期:由 start-wallpaper.sh 在启动壁纸时拉起(幂等,已有实例不重复),
--stop 时随壁纸一起停止。日志在 STATE_DIR/suspend-daemon.log。
"""

import os
import signal
import subprocess
import sys
import threading
import time

STATE_DIR = os.environ.get(
    "LWE_STATE_DIR",
    os.path.join(os.environ.get("XDG_CACHE_HOME", f"{os.path.expanduser('~')}/.cache"),
                 "wallpaper-picker"))
PIDFILE = os.path.join(STATE_DIR, "suspend-daemon.pid")
WALLPAPER_PIDFILE = os.path.join(STATE_DIR, "wallpaper.pid")
RESTART_FLAG = os.path.join(STATE_DIR, "lwpe-needs-restart")
HEALTH_FILE = os.path.join(STATE_DIR, "lwpe-healthy")
STOP_MARKER = os.path.join(STATE_DIR, "wallpaper-stop")
RECOVERY_ENV = "LWE_RECOVERY"
# 本文件在包内(wallpaper_picker/),启动器在上一级(仓库根/deb 安装根)
WAKER = os.path.join(
    os.path.dirname(os.path.dirname(os.path.realpath(__file__))),
    "start-wallpaper.sh")

os.makedirs(STATE_DIR, exist_ok=True)
log_file = open(os.path.join(STATE_DIR, "suspend-daemon.log"), "a", buffering=1)

# 重启节流:同一分钟内不重复重启;扩展反复报错时 10 分钟窗口内最多 3 次,
# 防止扩展与渲染器陷入互相触发的死循环。唤醒/解锁是人为事件,不受 3 次
# 上限约束,只需要一个最小间隔防止抖动。
_last_restart = 0.0
_restart_times = []
_restart_lock = threading.Lock()
_restarting = False


def log(msg):
    log_file.write(f"{time.strftime('%m-%d %H:%M:%S')} {msg}\n")


def wallpaper_stopped():
    """pidfile 不存在或停止标记存在 = 用户明确停掉了壁纸。

    渲染器进程消失但 pidfile 还在,说明它是崩溃/窗口被拆,仍然需要重启,
    所以这里只认 pidfile 与显式停止标记,不认进程是否还活着。
    """
    return (not os.path.exists(WALLPAPER_PIDFILE)
            or os.path.exists(STOP_MARKER))


def extension_healthy(max_age=20):
    """扩展每 5 秒刷新一次心跳;20 秒内的心跳说明它手上确实有画面源。"""
    try:
        return time.time() - os.path.getmtime(HEALTH_FILE) <= max_age
    except OSError:
        return False


def restart_wallpaper(reason, min_interval=60, cap=True):
    global _last_restart, _restarting
    with _restart_lock:
        if _restarting:
            log(f"{reason}:上一次重启还没结束,跳过")
            return False
        if wallpaper_stopped():
            log(f"{reason}:用户已停止壁纸,跳过")
            return False
        now = time.monotonic()
        if now - _last_restart < min_interval:
            log(f"{reason}:{min_interval} 秒内刚重启过,跳过")
            return False
        if cap:
            _restart_times[:] = [t for t in _restart_times if now - t < 600]
            if len(_restart_times) >= 3:
                log(f"{reason}:10 分钟内已重启 3 次,暂时退避")
                return False
        _last_restart = now
        _restart_times.append(now)
        _restarting = True
    log(f"{reason}:重启壁纸")
    try:
        env = os.environ.copy()
        # 让启动器知道这是守护恢复，不能清掉用户刚写下的停止标记。
        env[RECOVERY_ENV] = "1"
        result = subprocess.run(
            ["bash", WAKER, "--apply-plan"],
            capture_output=True, text=True, timeout=60, env=env)
        if result.returncode != 0:
            detail = (result.stderr or "").strip().splitlines()
            suffix = f":{detail[-1][:160]}" if detail else ""
            log(f"重启失败(退出码 {result.returncode}){suffix}")
            return False
        log("重启完成")
        return True
    except Exception as exc:
        log(f"重启失败: {exc}")
        return False
    finally:
        with _restart_lock:
            _restarting = False


def recover(reason, delay):
    """唤醒/解锁后的恢复:等 Mutter 把布局和窗口重新摆好再决定是否重启。"""
    time.sleep(delay)
    if wallpaper_stopped():
        log(f"{reason}:壁纸已被停止,跳过恢复")
        return
    if extension_healthy():
        log(f"{reason}:扩展仍持有画面源,无需重启")
        return
    # 解锁是人为事件,间隔取小一点:连续锁屏/解锁测试时每次都要能自愈
    restart_wallpaper(reason, min_interval=5, cap=False)


def check_restart_flag():
    """轮询扩展写下的重启标记(每 3 秒)。"""
    token = _restart_flag_token()
    if token is None:
        return True
    try:
        age = time.time() - os.path.getmtime(RESTART_FLAG)
    except OSError:
        return True
    # 过期标记(例如扩展已被用户停用)、或用户已停掉壁纸:清掉别反复触发
    if age > 120 or wallpaper_stopped():
        try:
            os.remove(RESTART_FLAG)
        except OSError:
            pass
        return True
    if not restart_wallpaper("扩展报告无可克隆的画面源"):
        return True
    # 重启期间扩展可能又发现了坏源并写入新标记。只删除本轮消费的
    # 那一个版本，不能覆盖新的恢复请求。
    if _restart_flag_token() == token:
        try:
            os.remove(RESTART_FLAG)
        except OSError:
            pass
    return True


def _restart_flag_token():
    """返回重启标记当前版本，用于避免删除重启期间的新标记。"""
    try:
        stat = os.stat(RESTART_FLAG)
        with open(RESTART_FLAG, "rb") as fh:
            content = fh.read()
        return stat.st_ino, stat.st_mtime_ns, stat.st_size, content
    except OSError:
        return None


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

    def on_prepare(_bus, _sender, _path, _iface, _signal, params):
        sleeping = params.unpack()[0]
        if sleeping:
            log("进入挂起")
        else:
            log("唤醒:安排恢复")
            threading.Thread(target=recover, args=("系统唤醒", 3),
                             daemon=True).start()

    def on_screensaver(_bus, _sender, _path, _iface, _signal, params):
        active = params.unpack()[0]
        if active:
            log("屏幕已锁定")
        else:
            log("屏幕已解锁:安排恢复")
            threading.Thread(target=recover, args=("屏幕解锁", 4),
                             daemon=True).start()

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

        # 会话总线拿不到(例如在无桌面环境里跑)不应该拖垮挂起守护,
        # 所以单独 try 一次。
        try:
            session = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            session.signal_subscribe(
                "org.gnome.ScreenSaver", "org.gnome.ScreenSaver",
                "ActiveChanged", "/org/gnome/ScreenSaver", None,
                Gio.DBusSignalFlags.NONE, on_screensaver)
        except Exception as exc:
            log(f"订阅锁屏信号失败(解锁后不会自动恢复): {exc}")

        GLib.timeout_add_seconds(3, check_restart_flag)
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
