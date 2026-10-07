"""多屏启动计划 —— plan_launch 的意图展开与回写。"""

import json

import wallpaper_picker.paths as paths


def setup_dual_screen(monkeypatch, tmp_path, settings=None):
    """双屏（HDMI-1 主 + eDP-1 副）+ 隔离的 settings.json。"""
    monkeypatch.setattr(
        paths, "find_screens",
        lambda: [("HDMI-1", True), ("eDP-1", False)])
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    monkeypatch.setenv("LWE_STATE_DIR", str(state))
    if settings is not None:
        with open(state / "settings.json", "w", encoding="utf-8") as fh:
            json.dump(settings, fh)
    return state / "settings.json"


def read_settings(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def test_default_clones_to_all_screens(monkeypatch, tmp_path):
    path = setup_dual_screen(monkeypatch, tmp_path)
    plan = paths.plan_launch("3018048025", write_back=False)
    assert plan == [("HDMI-1", "3018048025"), ("eDP-1", "3018048025")]
    assert not path.exists()                  # write_back=False 不动盘


def test_explicit_screens_switch_to_per_monitor(monkeypatch, tmp_path):
    path = setup_dual_screen(monkeypatch, tmp_path)
    plan = paths.plan_launch(
        None, [("HDMI-1", "3018048025"), ("eDP-1", "2317494988")])
    assert plan == [("HDMI-1", "3018048025"), ("eDP-1", "2317494988")]
    data = read_settings(path)
    assert data["clone"] is False
    assert data["screens"] == {"HDMI-1": "3018048025", "eDP-1": "2317494988"}


def test_plain_id_overrides_primary_keeps_others(monkeypatch, tmp_path):
    """逐屏之后应用普通 ID：主屏被本次壁纸覆盖，副屏保持映射。"""
    path = setup_dual_screen(monkeypatch, tmp_path, {
        "clone": False,
        "screens": {"HDMI-1": "3018048025", "eDP-1": "2317494988"},
    })
    plan = paths.plan_launch("3050160027")    # 默认回写：last 记录本次壁纸
    assert plan == [("HDMI-1", "3050160027"), ("eDP-1", "2317494988")]
    assert read_settings(path)["last"] == "3050160027"


def test_all_screens_forces_clone_and_clears_map(monkeypatch, tmp_path):
    path = setup_dual_screen(monkeypatch, tmp_path, {
        "clone": False, "screens": {"HDMI-1": "1", "eDP-1": "2"}})
    plan = paths.plan_launch("3050160027", all_screens=True)
    assert plan == [("HDMI-1", "3050160027"), ("eDP-1", "3050160027")]
    data = read_settings(path)
    assert data["clone"] is True
    assert data["screens"] == {}


def test_falls_back_to_last_when_no_explicit_bg(monkeypatch, tmp_path):
    """纯恢复场景（无位置参数、无 --screen）：按 screens，缺失回落 last。"""
    path = setup_dual_screen(monkeypatch, tmp_path, {
        "clone": False, "screens": {"eDP-1": "2317494988"}, "last": "3050160027"})
    plan = paths.plan_launch(None, write_back=False)
    assert plan == [("HDMI-1", "3050160027"), ("eDP-1", "2317494988")]


def test_single_screen_degenerates_cleanly(monkeypatch, tmp_path):
    """单屏机器：一切意图都退化为「主屏 = 本次壁纸」。"""
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("LWE_STATE_DIR", str(state))
    monkeypatch.setattr(paths, "find_screens", lambda: [("eDP-1", True)])
    plan = paths.plan_launch("3018048025", write_back=False)
    assert plan == [("eDP-1", "3018048025")]
