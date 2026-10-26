"""GNOME Shell 扩展的启动前检查。"""

import os
import shutil
import subprocess
import time


UUID = "linux-wallpaperengine@github.io"


def _run(command, *args):
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    return subprocess.run(
        [command, *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
        env=env,
    )


def ensure_enabled():
    """确保桌面背景克隆扩展已启用，成功返回 True。"""
    command = shutil.which("gnome-extensions")
    if not command:
        return False
    try:
        result = _run(command, "enable", UUID)
    except (OSError, subprocess.SubprocessError):
        return False
    if result.returncode != 0:
        return False

    # gnome-extensions can return success after dconf warns that no session
    # bus is available.  Do not start a full-screen renderer until Shell has
    # actually activated the extension that moves it to the background layer.
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            status = _run(command, "info", UUID)
        except (OSError, subprocess.SubprocessError):
            return False
        if status.returncode == 0:
            output = status.stdout
            if "Enabled: Yes" in output and "State: ACTIVE" in output:
                return True
        time.sleep(0.1)
    return False


if __name__ == "__main__":
    raise SystemExit(0 if ensure_enabled() else 1)
