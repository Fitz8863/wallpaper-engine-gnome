"""启动器的进程生命周期回归测试。"""

import os
import stat
import subprocess
import time


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAUNCHER = os.path.join(PROJECT_ROOT, "start-wallpaper.sh")


def _command(path, body):
    path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _base_env(tmp_path, bin_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _command(
        fake_bin / "gnome-extensions",
        "if [ \"$1\" = info ]; then printf '%s\\n' 'Enabled: Yes' 'State: ACTIVE'; fi",
    )
    env = os.environ.copy()
    env.update({
        "HOME": str(tmp_path / "home"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "LWE_STATE_DIR": str(tmp_path / "state"),
        "LWE_BIN": str(bin_path),
        "PATH": f"{fake_bin}:/usr/bin:/bin",
    })
    return env


def _wait_gone(pid, timeout=3):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


def test_stop_kills_custom_renderer_from_pidfile_and_marks_stop(tmp_path):
    renderer = tmp_path / "custom-renderer"
    _command(renderer, "trap 'exit 0' TERM INT; while :; do sleep 1; done")
    env = _base_env(tmp_path, renderer)

    started = subprocess.run(
        [LAUNCHER, "123"], env=env, capture_output=True, text=True, timeout=10)
    assert started.returncode == 0, started.stderr
    pidfile = tmp_path / "state" / "wallpaper.pid"
    pid = int(pidfile.read_text(encoding="ascii"))

    stopped = subprocess.run(
        [LAUNCHER, "--stop"], env=env, capture_output=True, text=True, timeout=10)
    assert stopped.returncode == 0, stopped.stderr
    assert not pidfile.exists()
    assert (tmp_path / "state" / "wallpaper-stop").exists()
    assert _wait_gone(pid)


def test_failed_renderer_start_cleans_pid_and_daemon_state(tmp_path):
    renderer = tmp_path / "custom-renderer"
    _command(renderer, "exit 7")
    env = _base_env(tmp_path, renderer)

    result = subprocess.run(
        [LAUNCHER, "123"], env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert not (tmp_path / "state" / "wallpaper.pid").exists()
    assert not (tmp_path / "state" / "suspend-daemon.pid").exists()


def test_stop_marker_is_written_even_when_nothing_is_running(tmp_path):
    renderer = tmp_path / "custom-renderer"
    _command(renderer, "exit 0")
    env = _base_env(tmp_path, renderer)

    result = subprocess.run(
        [LAUNCHER, "--stop"], env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0
    assert (tmp_path / "state" / "wallpaper-stop").exists()


def test_recovery_refuses_to_start_after_user_stop(tmp_path):
    renderer = tmp_path / "custom-renderer"
    _command(renderer, "trap 'exit 0' TERM INT; while :; do sleep 1; done")
    env = _base_env(tmp_path, renderer)
    env["LWE_RECOVERY"] = "1"
    state = tmp_path / "state"
    state.mkdir()
    (state / "wallpaper-stop").write_text("stopped", encoding="ascii")

    result = subprocess.run(
        [LAUNCHER, "123"], env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 75
    assert not (state / "wallpaper.pid").exists()
