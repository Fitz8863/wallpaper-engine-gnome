"""纯逻辑层测试的公共基建。

picker.py 顶部会 import GTK，测试机不需要显示环境也能拉起这些纯逻辑
模块——所以按需注入环境变量后单独 import picker 的逻辑部分。
"""

import json
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


@pytest.fixture()
def picker_mod(tmp_path, monkeypatch):
    """隔离 STATE_DIR 与自启文件后加载 picker 模块。

    不用 importlib.reload：GObject 类型（LWPEAspectPicture 等）不能
    重复注册，reload 第二次必炸。模块只加载一次，可变全局
    （AUTOSTART 等）改为逐测试指到沙箱。
    """
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("LWE_STATE_DIR", str(state))
    autostart = tmp_path / "autostart" / "wallpaper-engine.desktop"
    autostart.parent.mkdir()

    import wallpaper_picker.picker as picker
    monkeypatch.setattr(picker, "AUTOSTART", str(autostart))
    monkeypatch.setattr(picker, "STATE_DIR", str(state))
    monkeypatch.setattr(picker, "SETTINGS_FILE", str(state / "settings.json"))
    monkeypatch.setattr(picker, "PIDFILE", str(state / "wallpaper.pid"))
    monkeypatch.setattr(picker, "THUMB_DIR", str(state / "thumbs"))
    return picker


def write_settings(picker_mod, data):
    with open(picker_mod.SETTINGS_FILE, "w", encoding="utf-8") as fh:
        json.dump(data, fh)


def read_autostart(picker_mod):
    path = picker_mod.AUTOSTART
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return fh.read()
