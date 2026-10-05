"""非 Steam 壁纸目录支持 —— find_workshop 优先级与自定义目录扫描。"""

import json
import os

import wallpaper_picker.paths as paths
from tests.conftest import write_settings
from wallpaper_picker.scan import scan_workshop


def _isolate_detection(monkeypatch):
    """清空 Steam 探测，让「自动探测」分支确定性地返回 None。"""
    monkeypatch.setattr(paths, "STEAM_ROOTS", [])
    monkeypatch.setattr(paths, "FALLBACK_WORKSHOP", [])


def make_workshop(tmp_path, name="my-wallpapers"):
    ws = tmp_path / name
    wall = ws / "my-cool-wall"
    wall.mkdir(parents=True)
    (wall / "project.json").write_text(
        '{"title": "Cool", "type": "scene"}', encoding="utf-8")
    return ws


def test_settings_choice_beats_auto_detect(picker_mod, tmp_path, monkeypatch):
    _isolate_detection(monkeypatch)
    ws = make_workshop(tmp_path)
    write_settings(picker_mod, {"workshop": str(ws)})
    assert paths.find_workshop() == str(ws)


def test_env_beats_settings_choice(picker_mod, tmp_path, monkeypatch):
    _isolate_detection(monkeypatch)
    env_ws = make_workshop(tmp_path, "env-ws")
    chosen_ws = make_workshop(tmp_path, "chosen-ws")
    write_settings(picker_mod, {"workshop": str(chosen_ws)})
    monkeypatch.setenv("LWE_WORKSHOP", str(env_ws))
    assert paths.find_workshop() == str(env_ws)


def test_missing_setting_falls_through_to_detect(picker_mod, tmp_path,
                                                 monkeypatch):
    _isolate_detection(monkeypatch)
    write_settings(picker_mod, {})
    assert paths.find_workshop() is None


def test_broken_settings_file_is_not_fatal(picker_mod, tmp_path, monkeypatch):
    _isolate_detection(monkeypatch)
    with open(picker_mod.SETTINGS_FILE, "w", encoding="utf-8") as fh:
        fh.write("{not json")
    assert paths.find_workshop() is None


def test_stale_setting_path_is_ignored(picker_mod, tmp_path, monkeypatch):
    """目录被删/移动后手动选择失效，回落自动探测而不是报错。"""
    _isolate_detection(monkeypatch)
    write_settings(picker_mod, {"workshop": str(tmp_path / "gone")})
    assert paths.find_workshop() is None


def test_steam_workshop_query_ignores_manual_choice(picker_mod, tmp_path,
                                                    monkeypatch):
    """启动器判定壁纸归属用：find_steam_workshop 不受手动选择影响。"""
    _isolate_detection(monkeypatch)
    ws = make_workshop(tmp_path)
    write_settings(picker_mod, {"workshop": str(ws)})
    assert paths.find_steam_workshop() is None


def test_scan_accepts_non_numeric_dirnames(tmp_path):
    """第三方整理的目录名不一定工坊 ID 的数字，扫描按目录名处理。"""
    ws = make_workshop(tmp_path)
    rows = scan_workshop(str(ws))
    assert rows == [("scene", "my-cool-wall", "Cool")]


def test_settings_default_has_workshop_key(picker_mod):
    s = picker_mod.load_settings()
    assert s["workshop"] is None
