"""settings.json 旧版迁移与读写。"""

from tests.conftest import write_settings


def test_fresh_install_defaults(picker_mod):
    s = picker_mod.load_settings()
    assert s["autostart"] is True
    assert s["last"] is None
    assert s["volume_default"] == 15


def test_legacy_global_volume_migrates(picker_mod):
    write_settings(picker_mod, {"volume": 42, "fps": 60})
    s = picker_mod.load_settings()
    assert s["volume_default"] == 42
    assert s["fps"] == 60


def test_legacy_autostart_file_inference(picker_mod):
    # 旧版自启文件存在 + settings 无 autostart 键 → 开
    with open(picker_mod.AUTOSTART, "w", encoding="utf-8") as fh:
        fh.write('Exec=bash -c "sleep 5; /x/start-wallpaper.sh 1149860629"\n')
    write_settings(picker_mod, {})
    s = picker_mod.load_settings()
    assert s["autostart"] is True
    assert s["last"] == "1149860629"


def test_deleted_autostart_file_means_disabled(picker_mod):
    # 手动删过自启文件的用户视为关闭，不擅自恢复
    write_settings(picker_mod, {})
    s = picker_mod.load_settings()
    assert s["autostart"] is False


def test_per_wallpaper_volume_fallback(picker_mod):
    s = picker_mod.load_settings()
    s["volumes"]["123"] = 7
    assert picker_mod.wallpaper_volume(s, "123") == 7
    assert picker_mod.wallpaper_volume(s, "999") == s["volume_default"]


def test_settings_roundtrip(picker_mod, tmp_path):
    s = picker_mod.load_settings()
    s["fps"] = 25
    s["volumes"]["123"] = 7
    picker_mod.save_settings(s)
    disk = picker_mod.load_settings()
    assert disk["fps"] == 25
    assert disk["volumes"]["123"] == 7
