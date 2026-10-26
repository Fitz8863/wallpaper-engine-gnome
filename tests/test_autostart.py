"""登录自启文件的生命周期：写/删/自愈/迁移。"""

from tests.conftest import read_autostart


def _fake_window(picker_mod):
    """不走 Gtk 构造，只挂上被测方法依赖的属性。"""
    win = picker_mod.WallpaperPicker.__new__(picker_mod.WallpaperPicker)
    win.settings = dict(picker_mod.DEFAULT_SETTINGS)
    return win


def test_write_autostart_uses_restore_entry(picker_mod):
    win = _fake_window(picker_mod)
    win.settings["last"] = "123456"
    win.write_autostart()
    content = read_autostart(picker_mod)
    assert content and "--restore" in content
    assert "wallpaper-picker.py" in content
    # Exec 指向根目录薄壳（包的上一级），不是包内文件
    assert "wallpaper_picker/picker.py" not in content


def test_remove_autostart(picker_mod):
    win = _fake_window(picker_mod)
    win.write_autostart()
    win.remove_autostart()
    assert read_autostart(picker_mod) is None


def test_ensure_autostart_selfheal(picker_mod):
    """开了自启但文件是旧格式（缺 --restore）时自动重写。"""
    win = _fake_window(picker_mod)
    win.settings["autostart"] = True
    win.settings["last"] = "42"
    with open(picker_mod.AUTOSTART, "w", encoding="utf-8") as fh:
        fh.write('Exec=bash -c "sleep 5; /x/start-wallpaper.sh 42"\n')
    win.ensure_autostart()
    content = read_autostart(picker_mod)
    assert content and "--restore" in content


def test_ensure_autostart_respects_disabled(picker_mod):
    win = _fake_window(picker_mod)
    win.settings["autostart"] = False
    win.ensure_autostart()
    assert read_autostart(picker_mod) is None


def test_ensure_autostart_noop_when_current(picker_mod):
    win = _fake_window(picker_mod)
    win.settings["last"] = "42"
    win.write_autostart()
    before = read_autostart(picker_mod)
    win.ensure_autostart()
    assert read_autostart(picker_mod) == before


def test_set_autostart_toggle_writes_and_removes(picker_mod):
    win = _fake_window(picker_mod)
    win.settings["last"] = "42"
    # set_autostart 会调 schedule_save（GLib 定时器），这里只需要文件副作用
    import gi
    gi.require_version("GLib", "2.0")
    from gi.repository import GLib
    win._save_source = None
    win.schedule_save = lambda: None
    win.set_autostart(False)
    assert read_autostart(picker_mod) is None
    win.set_autostart(True)
    assert read_autostart(picker_mod) is not None


def test_manual_restore_setting_requests_restore_on_first_activation(picker_mod):
    assert picker_mod.should_restore_on_activate(
        first=True,
        snapshot_path=None,
        restore_mode=False,
        settings={"restore_on_start": True},
    ) is True


def test_restore_is_not_requested_for_normal_start_when_disabled(picker_mod):
    assert picker_mod.should_restore_on_activate(
        first=True,
        snapshot_path=None,
        restore_mode=False,
        settings={"restore_on_start": False},
    ) is False


def test_extension_loaded_requires_enabled_active(picker_mod, monkeypatch):
    class Result:
        returncode = 0
        stdout = "Enabled: No\nState: ACTIVE\n"

    monkeypatch.setattr(picker_mod.subprocess, "run", lambda *args, **kwargs: Result())
    assert picker_mod.extension_loaded() is False


def test_extension_loaded_accepts_only_enabled_active(picker_mod, monkeypatch):
    class Result:
        returncode = 0
        stdout = "Enabled: Yes\nState: ACTIVE\n"

    monkeypatch.setattr(picker_mod.subprocess, "run", lambda *args, **kwargs: Result())
    assert picker_mod.extension_loaded() is True


def test_stop_clears_all_running_screen_state(picker_mod, monkeypatch):
    win = picker_mod.WallpaperPicker.__new__(picker_mod.WallpaperPicker)
    win.current_id = "123"
    win.current_screens = {"eDP-1": "123", "HDMI-1": "456"}
    win.set_busy = lambda *_args: None
    win.remove_autostart = lambda: None
    win.update_highlight = lambda: None
    win.update_status = lambda: None
    win.apply_btn = type("Button", (), {"set_sensitive": lambda *_: None,
                                        "set_label": lambda *_: None})()
    win.toast_overlay = type("Overlay", (), {"add_toast": lambda *_: None})()
    monkeypatch.setattr(picker_mod.Adw, "Toast", lambda **kwargs: kwargs)

    result = type("Result", (), {"returncode": 0, "stderr": ""})()
    assert win._after_stop(result) is False
    assert win.current_id is None
    assert win.current_screens == {}
