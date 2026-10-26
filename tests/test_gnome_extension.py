"""启动动态壁纸前必须启用负责桌面层级的 GNOME 扩展。"""

import os
from pathlib import Path

import wallpaper_picker.gnome_extension as extension


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _fake_gnome_extensions(tmp_path, log_path, returncode=0):
    command = tmp_path / "gnome-extensions"
    command.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$*\" >> \"$LWE_EXTENSION_TEST_LOG\"\n"
        "if [ \"$1\" = info ]; then\n"
        "  printf '%s\\n' 'Enabled: Yes' 'State: ACTIVE'\n"
        "fi\n"
        f"exit {returncode}\n",
        encoding="utf-8",
    )
    command.chmod(command.stat().st_mode | 0o111)
    return command


def test_enable_extension_calls_gnome_extensions(tmp_path, monkeypatch):
    log = tmp_path / "calls.log"
    _fake_gnome_extensions(tmp_path, log)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("LWE_EXTENSION_TEST_LOG", os.fspath(log))

    assert extension.ensure_enabled() is True
    assert log.read_text(encoding="utf-8") == (
        "enable linux-wallpaperengine@github.io\n"
        "info linux-wallpaperengine@github.io\n"
    )


def test_enable_extension_fails_when_command_fails(tmp_path, monkeypatch):
    log = tmp_path / "calls.log"
    _fake_gnome_extensions(tmp_path, log, returncode=1)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("LWE_EXTENSION_TEST_LOG", os.fspath(log))

    assert extension.ensure_enabled() is False


def test_enable_extension_fails_when_command_is_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))

    assert extension.ensure_enabled() is False


def test_launcher_guards_renderer_start_with_extension_check():
    script = (PROJECT_ROOT / "start-wallpaper.sh").read_text(encoding="utf-8")
    guard = "python3 -m wallpaper_picker.gnome_extension"
    assert guard in script
    assert script.index(guard) < script.index("# 前导多屏参数")


def test_extension_uses_runtime_state_directory():
    source = (PROJECT_ROOT / "gnome-extension" / "wallpaperManager.js").read_text(
        encoding="utf-8")
    assert "GLib.getenv('LWE_STATE_DIR')" in source
    assert "LWE_STATE_DIR" in source


def test_core_background_hook_failure_is_fatal():
    source = (PROJECT_ROOT / "gnome-extension" / "wallpaperManager.js").read_text(
        encoding="utf-8")
    start = source.index("const backgroundPrototype")
    end = source.index("global.lwpeWallpaperManager = this;", start)
    hook = source[start:end]
    assert "typeof originalBackgroundHook !== 'function'" in hook
    assert "InjectionManager did not replace _createBackgroundActor" in hook
    assert "throw new Error" in hook
