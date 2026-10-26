"""挂起守护的失败处理与停止竞态回归测试。"""

import importlib
import io
import os
import sys
from types import SimpleNamespace

import pytest


@pytest.fixture()
def daemon(tmp_path, monkeypatch):
    """以隔离状态目录加载模块，避免触碰真实用户缓存。"""
    monkeypatch.setenv("LWE_STATE_DIR", str(tmp_path))
    sys.modules.pop("wallpaper_picker.suspend_daemon", None)
    module = importlib.import_module("wallpaper_picker.suspend_daemon")
    module.log_file = io.StringIO()
    module._last_restart = 0.0
    module._restart_times.clear()
    module._restarting = False
    yield module
    module.log_file = io.StringIO()


def _wallpaper_running(daemon):
    daemon.WALLPAPER_PIDFILE = os.path.join(
        os.path.dirname(daemon.RESTART_FLAG), "wallpaper.pid")
    open(daemon.WALLPAPER_PIDFILE, "w", encoding="ascii").write("123")


def test_restart_wallpaper_reports_nonzero_exit_as_failure(daemon, monkeypatch):
    _wallpaper_running(daemon)
    calls = []
    monkeypatch.setattr(
        daemon.subprocess,
        "run",
        lambda *args, **kwargs: (
            calls.append((args, kwargs))
            or SimpleNamespace(returncode=1, stdout="", stderr="boom")),
    )
    monkeypatch.setattr(daemon, "log", lambda msg: calls.append(msg))

    assert daemon.restart_wallpaper("测试失败") is False
    assert any("重启失败" in msg for msg in calls if isinstance(msg, str))
    assert not any(msg == "重启完成" for msg in calls if isinstance(msg, str))
    run_call = next(item for item in calls if isinstance(item, tuple))
    assert run_call[1]["env"]["LWE_RECOVERY"] == "1"


def test_restart_flag_is_kept_when_restart_fails(daemon, monkeypatch):
    _wallpaper_running(daemon)
    open(daemon.RESTART_FLAG, "w", encoding="ascii").write("old")
    monkeypatch.setattr(daemon, "restart_wallpaper", lambda *_args: False)

    assert daemon.check_restart_flag() is True
    assert os.path.exists(daemon.RESTART_FLAG)


def test_success_does_not_remove_a_new_restart_flag(daemon, monkeypatch):
    _wallpaper_running(daemon)
    open(daemon.RESTART_FLAG, "w", encoding="ascii").write("old")

    def restart_and_replace_flag(_reason):
        with open(daemon.RESTART_FLAG, "w", encoding="ascii") as fh:
            fh.write("new")
        return True

    monkeypatch.setattr(daemon, "restart_wallpaper", restart_and_replace_flag)

    assert daemon.check_restart_flag() is True
    assert open(daemon.RESTART_FLAG, encoding="ascii").read() == "new"


def test_success_removes_the_consumed_restart_flag(daemon, monkeypatch):
    _wallpaper_running(daemon)
    open(daemon.RESTART_FLAG, "w", encoding="ascii").write("old")
    monkeypatch.setattr(daemon, "restart_wallpaper", lambda *_args: True)

    assert daemon.check_restart_flag() is True
    assert not os.path.exists(daemon.RESTART_FLAG)


def test_recovery_is_blocked_after_stop_marker(daemon, monkeypatch):
    _wallpaper_running(daemon)
    open(daemon.STOP_MARKER, "w", encoding="ascii").write("stopped")
    calls = []
    monkeypatch.setattr(daemon.time, "sleep", lambda _delay: None)
    monkeypatch.setattr(daemon.subprocess, "run", lambda *args, **kwargs: calls.append(args))

    daemon.recover("停止后恢复", 0)
    assert calls == []
