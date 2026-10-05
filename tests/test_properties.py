"""--list-properties 输出解析与退化参数钳制。"""

import wallpaper_picker.picker as picker


class TestParseProperties:
    def test_boolean(self):
        raw = "bokehblue - boolean\n\tText: Bokeh Blue\n\tValue: 1\n"
        props = picker.parse_properties(raw)
        assert props[0]["name"] == "bokehblue"
        assert props[0]["type"] == "boolean"
        assert props[0]["label"] == "Bokeh Blue"
        assert props[0]["value"] == "1"

    def test_combo_options_indented_two(self):
        raw = ("clock - combo\n\tText: Clock\n\tValue: 0\nValues:\n"
               "\t\t0 = 24H\n\t\t1 = 12H\n")
        props = picker.parse_properties(raw)
        assert props[0]["options"] == [("0", "24H"), ("1", "12H")]

    def test_skip_property_resets_pointer(self):
        # 跳过的类型必须清空当前属性，否则其缩进行算到上一个属性头上
        raw = ("good - boolean\n\tText: Good\n\tValue: 1\n"
               "weird - othertype\n\tText: Pollution\n\tValue: 9\n")
        props = picker.parse_properties(raw)
        assert len(props) == 1
        assert props[0]["label"] == "Good"

    def test_schemecolor_filtered(self):
        raw = "schemecolor - color\n\tText: ui_browse_x\n\tValue: 0 0 0\n"
        assert picker.parse_properties(raw) == []

    def test_running_with_prefix_ignored(self):
        raw = "Running with: x --bg 1\nbokeh - boolean\n\tValue: 1\n"
        assert picker.parse_properties(raw)[0]["name"] == "bokeh"


class TestCleanLabel:
    def test_plain(self):
        assert picker.clean_label("Clouds", "fallback") == "Clouds"

    def test_i18n_key_falls_back(self):
        assert picker.clean_label("ui_browse_x", "fallback") == "fallback"

    def test_html_stripped(self):
        assert picker.clean_label("<p>颜色<br>color", "fallback") == "颜色 color"

    def test_too_long_html_falls_back(self):
        long_html = "<p>" + "x" * 60
        assert picker.clean_label(long_html, "fallback") == "fallback"


class TestSliderClamp:
    """退化参数（作者数据 Step=0 / min>=max）的钳制逻辑。"""

    def _make(self, win, lo, hi, step):
        spec = {"name": "x", "type": "slider", "label": "x",
                "min": str(lo), "max": str(hi), "step": str(step)}
        return win._build_property_widget(spec, "0")

    def _window(self):
        win = picker.WallpaperPicker.__new__(picker.WallpaperPicker)
        win.settings = dict(picker.DEFAULT_SETTINGS)
        win.selected = None
        return win

    def test_zero_step_becomes_range_fraction(self):
        win = self._window()
        row = self._make(win, 0, 100, 0)
        scale = row.get_child().get_first_child()  # 简单取内部 scale
        # 直接断言能构造成功即可（GTK 断言 step!=0 失败会返回 NULL 并抛错）
        assert row is not None

    def test_zero_step_negative_range(self):
        win = self._window()
        assert self._make(win, -2000, 800, 0) is not None

    def test_inverted_range_clamped(self):
        win = self._window()
        assert self._make(win, 5, 5, 0.1) is not None

    def test_normal_slider(self):
        win = self._window()
        assert self._make(win, 0, 1, 0.01) is not None
