"""扫描排序与 i18n 回退。"""

import wallpaper_picker.scan as scan
import wallpaper_picker.i18n as i18n


class TestScanOrder:
    def test_sorted_by_type_then_title(self, tmp_path):
        (tmp_path / "1").mkdir()
        (tmp_path / "1" / "project.json").write_text(
            '{"title": "B", "type": "video"}', encoding="utf-8")
        (tmp_path / "2").mkdir()
        (tmp_path / "2" / "project.json").write_text(
            '{"title": "A", "type": "scene"}', encoding="utf-8")
        rows = scan.scan_workshop(str(tmp_path))
        assert [r[1] for r in rows] == ["2", "1"]

    def test_broken_project_json_still_listed(self, tmp_path):
        (tmp_path / "9").mkdir()
        (tmp_path / "9" / "project.json").write_text("{broken", encoding="utf-8")
        rows = scan.scan_workshop(str(tmp_path))
        assert len(rows) == 1
        assert rows[0][0] == "?"
        assert "无法解析" in rows[0][2]

    def test_utf8_bom_project_json(self, tmp_path):
        (tmp_path / "3").mkdir()
        (tmp_path / "3" / "project.json").write_bytes(
            b'\xef\xbb\xbf{"title": "ok", "type": "scene"}')
        rows = scan.scan_workshop(str(tmp_path))
        assert rows[0][2] == "ok"


class TestI18n:
    def test_zh_passthrough(self):
        i18n.set_language("zh")
        assert i18n.tr("停止") == "停止"

    def test_en_known(self):
        i18n.set_language("en")
        assert i18n.tr("停止") == "Stop"
        assert i18n.tr("壁纸属性（{} 项）").format(3) == "Wallpaper properties (3 items)"

    def test_en_missing_falls_back(self):
        i18n.set_language("en")
        assert i18n.tr("不存在的文案") == "不存在的文案"

    def test_system_resolution(self, monkeypatch):
        monkeypatch.setenv("LANG", "en_US.UTF-8")
        monkeypatch.delenv("LC_ALL", raising=False)
        monkeypatch.delenv("LC_MESSAGES", raising=False)
        i18n.set_language("system")
        assert i18n.tr("停止") == "Stop"
        monkeypatch.setenv("LANG", "zh_CN.UTF-8")
        i18n.set_language("system")
        assert i18n.tr("停止") == "停止"
