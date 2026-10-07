"""多显示器探测 —— parse_logical_monitors 与 find_screens/find_screen。"""

import os
import subprocess
import sys

import wallpaper_picker.paths as paths


def _monitor(x, y, primary, connector):
    """构造一个 Mutter logical_monitor 条目（只含被解析的字段）。"""
    return (x, y, 1.0, 0, primary, [(connector, "vendor", "product", "sn")], {})


def test_parse_puts_primary_first_then_by_position():
    logical = [
        _monitor(1920, 0, False, "HDMI-1"),
        _monitor(0, 0, True, "eDP-1"),
        _monitor(1920, 1080, False, "DP-2"),
    ]
    assert paths.parse_logical_monitors(logical) == [
        ("eDP-1", True),      # 主屏永远最前
        ("HDMI-1", False),    # 同行按 x 排序
        ("DP-2", False),
    ]


def test_parse_sorts_by_y_before_x():
    logical = [
        _monitor(0, 1080, False, "DP-1"),   # 下排
        _monitor(0, 0, False, "HDMI-2"),    # 上排
    ]
    assert paths.parse_logical_monitors(logical) == [
        ("HDMI-2", False), ("DP-1", False)
    ]


def test_parse_skips_entries_without_monitors():
    logical = [
        (0, 0, 1.0, 0, True, [], {}),
        _monitor(0, 0, False, "HDMI-1"),
    ]
    assert paths.parse_logical_monitors(logical) == [("HDMI-1", False)]


def test_find_screens_falls_back_to_single_screen(monkeypatch):
    """Mutter 查询失败时回落单屏：LWE_SCREEN 优先，否则 eDP-1 视为主屏。"""
    monkeypatch.setattr(paths, "_query_mutter_screens", lambda: None)
    monkeypatch.delenv("LWE_SCREEN", raising=False)
    assert paths.find_screens() == [("eDP-1", True)]

    monkeypatch.setenv("LWE_SCREEN", "DP-3")
    assert paths.find_screens() == [("DP-3", True)]


def test_find_screen_picks_primary(monkeypatch):
    monkeypatch.setattr(
        paths, "_query_mutter_screens",
        lambda: [("HDMI-1", False), ("eDP-1", True)])
    monkeypatch.delenv("LWE_SCREEN", raising=False)
    assert paths.find_screen() == "eDP-1"


def test_find_screen_env_override_still_wins(monkeypatch):
    monkeypatch.setenv("LWE_SCREEN", "DP-9")
    assert paths.find_screen() == "DP-9"


def test_screens_cli_prints_connector_per_line():
    """真机/回落两种环境下 CLI 都应输出：每行一个 connector、恰好一个主屏。"""
    out = subprocess.run(
        [sys.executable, os.path.join(os.path.dirname(paths.__file__), "paths.py"),
         "screens"],
        capture_output=True, text=True, timeout=30).stdout.strip().splitlines()
    assert len(out) >= 1
    starred = [line for line in out if line.endswith(" *")]
    assert len(starred) == 1
    assert all(line.rsplit(" *", 1)[0] for line in out)
