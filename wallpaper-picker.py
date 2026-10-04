#!/usr/bin/env python3
"""Wallpaper Engine 壁纸选择器 —— GNOME 原生 GTK4 / libadwaita 界面

左侧是壁纸网格，右侧是可折叠的属性面板（布局参考 Wallpaper Engine）。

设置分两层：
  播放设置  全局生效（静音、音量、帧率、缩放模式、特效开关）
  壁纸属性  逐壁纸生效，由渲染器的 --list-properties 动态读出，
            按 boolean/slider/combo 生成对应控件，经 --set-property 回传

设置写到 STATE_DIR/settings.json，start-wallpaper.sh 启动时会读取并翻译成
命令行参数，因此图形界面和命令行行为一致。
"""

import glob
import json
import os
import re
import subprocess
import sys
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Pango", "1.0")
gi.require_version("Graphene", "1.0")
from gi.repository import (  # noqa: E402
    Adw, Gdk, GdkPixbuf, Gio, GLib, Graphene, Gtk, Pango,
)

ROOT = os.path.dirname(os.path.realpath(__file__))
HOME = os.path.expanduser("~")
SCRIPT = os.path.join(ROOT, "start-wallpaper.sh")
AUTOSTART = f"{HOME}/.config/autostart/wallpaper-engine.desktop"
EXT_UUID = "linux-wallpaperengine@github.io"

STATE_DIR = os.environ.get(
    "LWE_STATE_DIR",
    os.path.join(os.environ.get("XDG_CACHE_HOME", f"{HOME}/.cache"),
                 "wallpaper-picker"))
PIDFILE = os.path.join(STATE_DIR, "wallpaper.pid")
SETTINGS_FILE = os.path.join(STATE_DIR, "settings.json")

# 路径探测与 start-wallpaper.sh 共用同一份实现，避免两边行为不一致
sys.path.insert(0, ROOT)
from lwe_paths import find_renderer, find_workshop  # noqa: E402

RENDERER = find_renderer()

TYPE_LABEL = {"scene": "场景", "video": "视频", "web": "网页"}
THUMB = 320

DEFAULT_SETTINGS = {
    "silent": False,
    "volume": 15,
    "fps": 30,
    "scaling": "default",
    "particles": True,
    "parallax": True,
    "mouse": True,
    "properties": {},
}

# 渲染器 --scaling 的合法取值（实测自 "allowed options" 报错信息）
SCALING_CHOICES = [
    ("default", "默认"),
    ("fill", "填充（裁切边缘）"),
    ("fit", "适应（保留黑边）"),
    ("stretch", "拉伸（可能变形）"),
]

CSS = """
.wallpaper-card {
    border-radius: 12px;
    background-color: alpha(currentColor, 0.06);
    padding: 6px;
}
.wallpaper-card:hover   { background-color: alpha(currentColor, 0.12); }
.wallpaper-card-current { background-color: alpha(@accent_bg_color, 0.28); }
.wallpaper-card-current:hover { background-color: alpha(@accent_bg_color, 0.38); }
.card-title { font-size: 0.86em; font-weight: 500; }
.card-badge { font-size: 0.72em; opacity: 0.6; }
.thumb {
    border-radius: 8px;
    /* 描边是必须的：不少壁纸本身就很暗，没有边界会和卡片糊成一片 */
    outline: 1px solid alpha(currentColor, 0.18);
    outline-offset: -1px;
}
.preview-frame {
    border-radius: 10px;
    outline: 1px solid alpha(currentColor, 0.15);
    outline-offset: -1px;
    background-color: alpha(currentColor, 0.05);
}
.sidebar-title { font-size: 1.15em; font-weight: 700; }
.section-title { font-weight: 700; opacity: 0.75; font-size: 0.82em; }
.empty-hint { opacity: 0.6; }
"""


# --------------------------------------------------------------- 配置读写

def load_settings():
    data = dict(DEFAULT_SETTINGS)
    data["properties"] = {}
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as fh:
            disk = json.load(fh)
        for key, value in disk.items():
            if key == "properties" and isinstance(value, dict):
                data["properties"] = value
            elif key in data:
                data[key] = value
    except Exception:
        pass
    return data


def save_settings(data):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = f"{SETTINGS_FILE}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, SETTINGS_FILE)


# --------------------------------------------------------------- 数据模型

class Wallpaper:
    __slots__ = ("wid", "wtype", "title", "preview", "dir")

    def __init__(self, wid, wtype, title, preview, directory):
        self.wid = wid
        self.wtype = wtype
        self.title = title
        self.preview = preview
        self.dir = directory


def load_thumbnail(path, size):
    """载入缩略图。

    两个坑：GdkPixbuf 的 new_from_file_at_scale 遇到 GIF 动图会抛
    "Not all frames of the GIF image were loaded"，动图得另走 PixbufAnimation
    取首帧再缩放；另外不少壁纸画面极暗，带 alpha 的先合成到深色底上。
    """
    try:
        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, size, size, True)
    except GLib.Error:
        frame = GdkPixbuf.PixbufAnimation.new_from_file(path) \
            .get_iter(None).get_pixbuf()
        w, h = frame.get_width(), frame.get_height()
        factor = min(size / w, size / h, 1.0)
        pixbuf = frame.scale_simple(
            max(1, round(w * factor)), max(1, round(h * factor)),
            GdkPixbuf.InterpType.BILINEAR)

    if not pixbuf.get_has_alpha():
        return pixbuf
    flat = GdkPixbuf.Pixbuf.new(
        GdkPixbuf.Colorspace.RGB, True, pixbuf.get_bits_per_sample(),
        pixbuf.get_width(), pixbuf.get_height())
    flat.fill(0x303030ff)
    pixbuf.composite(
        flat, 0, 0, pixbuf.get_width(), pixbuf.get_height(),
        0, 0, 1.0, 1.0, GdkPixbuf.InterpType.BILINEAR, 255)
    return flat


def scan_wallpapers():
    workshop = find_workshop()
    if workshop is None:
        return []
    found = []
    for path in sorted(glob.glob(os.path.join(workshop, "*/"))):
        pj = os.path.join(path, "project.json")
        if not os.path.exists(pj):
            continue
        wid = os.path.basename(path.rstrip("/"))
        try:
            meta = json.load(open(pj, encoding="utf-8-sig"))
            title = str(meta.get("title", wid)).strip()
            wtype = str(meta.get("type", "?")).lower()
        except Exception:
            title, wtype = f"{wid}（配置无法解析）", "?"
        preview = ""
        for name in ("preview.jpg", "preview.png", "preview.gif"):
            if os.path.exists(os.path.join(path, name)):
                preview = os.path.join(path, name)
                break
        found.append(Wallpaper(wid, wtype, title, preview, path))
    order = {"scene": 0, "video": 1, "web": 2}
    found.sort(key=lambda w: (order.get(w.wtype, 9), w.title))
    return found


def running_wallpaper_id():
    try:
        pid = open(PIDFILE).read().strip()
        args = [a.decode() for a in
                open(f"/proc/{pid}/cmdline", "rb").read().split(b"\0") if a]
        if "--bg" in args:
            return args[args.index("--bg") + 1]
    except Exception:
        pass
    return None


def extension_loaded():
    try:
        return subprocess.run(["gnome-extensions", "info", EXT_UUID],
                              capture_output=True, text=True,
                              timeout=10).returncode == 0
    except Exception:
        return False


# --------------------------------------------------- 渲染器属性列表解析

# 这些是 Wallpaper Engine 的界面元数据，不是绘制参数，改了对画面没有任何影响。
# 实测方法：同一张壁纸渲染两次、只改这一个属性，逐像素比对 ——
#   schemecolor 改亮红  → 差异 0.06%（仅动画时间差，等于无影响）
#   pbrcolor    改亮红  → 差异 3%
#   clouds 开关         → 差异 17%
# 所以只排除 schemecolor（它决定创意工坊详情页的强调色），颜色类控件本身保留。
SKIP_PROPERTIES = {"schemecolor"}


def clean_label(text, fallback):
    """把属性的 Text 字段变成能看的标题。

    作者写标签的方式五花八门，实测遇到过三种：
      "Clouds"                          直接用
      "ui_browse_properties_scheme_color"  i18n 键名，用属性名兜底
      "<p>颜色<br>color"                   HTML，剥掉标签后是有效标题
    """
    text = (text or "").strip()
    if not text or text.startswith("ui_"):
        return fallback
    if "<" in text:
        plain = re.sub(r"<[^>]+>", " ", text)
        plain = re.sub(r"\s+", " ", plain).strip()
        # 太长的多半是整段说明或广告，不适合当标题
        if not plain or len(plain) > 40:
            return fallback
        return plain
    return text


def parse_properties(raw):
    """解析 `--list-properties` 的输出。

    格式形如::

        bokehblue - boolean
            Text: Bokeh Blue
            Value: 1

        clock - combo
            Text: Clock
            Value: 0
        Values:
            0 = 24H
            1 = 12H
    """
    props = []
    cur = None
    in_values = False

    for line in raw.splitlines():
        if not line.strip() or line.startswith("Running with:"):
            continue

        if line.startswith("Values:"):
            in_values = True
            continue

        if line.startswith("\t\t") and cur is not None and in_values:
            value, _, label = line.strip().partition("=")
            cur["options"].append((value.strip(), label.strip()))
            continue

        if line.startswith("\t"):
            key, _, value = line.strip().partition(":")
            key, value = key.strip(), value.strip()
            if cur is None:
                continue
            if key == "Text":
                cur["label"] = clean_label(value, cur["name"])
            elif key == "Value":
                cur["value"] = value
            elif key in ("Min", "Max", "Step"):
                cur[key.lower()] = value
            continue

        in_values = False
        name, sep, ptype = line.partition(" - ")
        if not sep:
            continue
        name = name.strip()
        if name in SKIP_PROPERTIES:
            # 同样要清空 cur，理由见上面那条注释
            cur = None
            continue
        ptype = ptype.strip()
        if ptype not in ("boolean", "slider", "combo", "color"):
            # 跳过的类型必须清空 cur，否则它后续缩进的 Text/Value 行
            # 会被错误地算到上一个属性头上
            cur = None
            continue
        cur = {"name": name, "type": ptype, "label": name,
               "value": "", "min": None, "max": None, "step": None,
               "options": []}
        props.append(cur)

    return props


def fetch_properties(wid):
    """跑一次渲染器读出该壁纸的可调属性（耗时，需放到后台线程）。"""
    if not os.access(RENDERER, os.X_OK):
        return []
    try:
        out = subprocess.run(
            [RENDERER, "--bg", wid, "--list-properties"],
            capture_output=True, text=True, timeout=60)
    except Exception:
        return []
    return parse_properties(out.stdout)


# ------------------------------------------------------------------ 界面

class WallpaperPicker(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="壁纸", default_width=1280,
                         default_height=820)
        self.settings = load_settings()
        self.wallpapers = []
        self.current_id = None
        self.selected = None
        self.cards = {}
        self.prop_rows = []
        self.switching = False
        self._reapply_source = None
        self._props_token = 0

        provider = Gtk.CssProvider()
        provider.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self.toast_overlay = Adw.ToastOverlay()
        self.set_content(self.toast_overlay)

        self.split = Adw.OverlaySplitView(
            sidebar_position=Gtk.PackType.END,
            min_sidebar_width=330, max_sidebar_width=400,
            collapsed=False)
        self.toast_overlay.set_child(self.split)

        self.split.set_content(self.build_content())
        self.split.set_sidebar(self.build_sidebar())

        self.reload()

    # ---------------------------------------------------------- 主区域

    def build_content(self):
        view = Adw.ToolbarView()
        header = Adw.HeaderBar()

        self.search = Gtk.SearchEntry(placeholder_text="搜索壁纸…",
                                      width_chars=24)
        self.search.connect("search-changed", lambda *_: self.populate())
        header.set_title_widget(self.search)

        refresh = Gtk.Button(icon_name="view-refresh-symbolic",
                             tooltip_text="重新扫描壁纸")
        refresh.connect("clicked", lambda *_: self.reload())
        header.pack_start(refresh)

        self.stop_btn = Gtk.Button(label="停止", css_classes=["destructive-action"])
        self.stop_btn.connect("clicked", self.on_stop)
        header.pack_end(self.stop_btn)

        self.panel_btn = Gtk.ToggleButton(icon_name="sidebar-show-symbolic",
                                          tooltip_text="显示/隐藏属性面板")
        self.panel_btn.connect("toggled",
                               lambda b: self.split.set_show_sidebar(b.get_active()))
        header.pack_end(self.panel_btn)

        # 切换壁纸需要一两秒（要停掉旧渲染器再起新的），期间转个圈给个交代
        self.spinner = Gtk.Spinner(tooltip_text="正在切换壁纸…")
        self.spinner.set_visible(False)
        header.pack_end(self.spinner)
        view.add_top_bar(header)

        self.warn = Adw.Banner(
            title="GNOME 扩展还没被加载：请注销后重新登录一次，动态壁纸才会显示到桌面上",
            revealed=not extension_loaded())
        self.warn.set_button_label("知道了")
        view.add_top_bar(self.warn)

        self.chips = Gtk.Box(spacing=6, margin_top=10, margin_bottom=4,
                             margin_start=14, margin_end=14)
        group = None
        for label, key in (("全部", ""), ("场景", "scene"),
                           ("视频", "video"), ("网页", "web")):
            btn = Gtk.ToggleButton(label=label,
                                   active=(key == ""), css_classes=["flat"])
            btn.connect("toggled", self.on_filter_toggled, key)
            self.chips.append(btn)
            group = btn if group is None else group
            if btn is not self.chips.get_first_child():
                btn.set_group(group)
        view.add_top_bar(self.chips)

        self.flow = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.NONE, homogeneous=True,
            min_children_per_line=2, max_children_per_line=6,
            row_spacing=12, column_spacing=12,
            margin_top=10, margin_bottom=14, margin_start=14, margin_end=14)
        scroller = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        scroller.set_child(self.flow)
        view.set_content(scroller)

        self.status = Gtk.Label(xalign=0, margin_start=14, margin_bottom=10,
                                css_classes=["dim-label"])
        view.add_bottom_bar(self.status)
        return view

    def on_filter_toggled(self, btn, key):
        if btn.get_active():
            self.active_filter = key
            self.populate()

    # ---------------------------------------------------------- 属性面板

    def build_sidebar(self):
        view = Adw.ToolbarView()
        header = Adw.HeaderBar()

        self.sidebar_title = Adw.WindowTitle(title="未选择壁纸")
        header.set_title_widget(self.sidebar_title)

        apply_btn = Gtk.Button(label="应用", css_classes=["suggested-action"])
        apply_btn.connect("clicked", lambda *_: self.apply(self.selected))
        self.apply_btn = apply_btn
        header.pack_end(apply_btn)
        view.add_top_bar(header)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14,
                      margin_top=14, margin_bottom=18,
                      margin_start=14, margin_end=14)

        self.preview = Gtk.Picture(can_shrink=True, height_request=170,
                                   css_classes=["preview-frame"])
        self.preview.set_content_fit(Gtk.ContentFit.COVER)
        box.append(self.preview)

        self.subtitle = Gtk.Label(xalign=0, wrap=True, css_classes=["card-badge"])
        box.append(self.subtitle)

        box.append(Gtk.Separator())

        # ---- 壁纸属性（逐壁纸，放最前面：这才是属性面板的主角）----
        self.props_header = self.section_title("壁纸属性")
        box.append(self.props_header)
        self.props_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.append(self.props_box)
        self.props_hint = Gtk.Label(
            label="选中壁纸后这里会列出它自己的可调项",
            xalign=0, wrap=True, css_classes=["empty-hint", "card-badge"])
        self.props_box.append(self.props_hint)

        # ---- 播放设置（全局，收进折叠分组省空间）----
        group = Adw.PreferencesGroup(margin_top=4)
        self.settings_group = group
        box.append(group)

        self.settings_expander = Adw.ExpanderRow(
            title="播放设置", subtitle="全局生效，对所有壁纸都一样")
        group.add(self.settings_expander)

        self.row_silent = self.switch_row("静音", "关闭壁纸产生的所有声音",
                                          self.settings["silent"],
                                          lambda v: self.set_global("silent", v))
        self.settings_expander.add_row(self.row_silent)

        self.row_volume = self.slider_row("音量", 0, 100, 1,
                                          self.settings["volume"],
                                          lambda v: self.set_global("volume", int(v)),
                                          suffix="%")
        self.settings_expander.add_row(self.row_volume)

        self.row_fps = self.slider_row("帧率上限", 10, 144, 1,
                                       self.settings["fps"],
                                       lambda v: self.set_global("fps", int(v)),
                                       suffix=" fps")
        self.settings_expander.add_row(self.row_fps)

        self.row_scaling = self.combo_row("缩放模式", SCALING_CHOICES,
                                          self.settings["scaling"],
                                          lambda v: self.set_global("scaling", v))
        self.settings_expander.add_row(self.row_scaling)

        self.row_particles = self.switch_row(
            "粒子效果", "关闭可降低 GPU 占用",
            self.settings["particles"],
            lambda v: self.set_global("particles", v))
        self.settings_expander.add_row(self.row_particles)

        self.row_parallax = self.switch_row(
            "视差效果", "跟随鼠标的景深位移",
            self.settings["parallax"],
            lambda v: self.set_global("parallax", v))
        self.settings_expander.add_row(self.row_parallax)

        self.row_mouse = self.switch_row(
            "鼠标交互", "允许壁纸响应鼠标位置",
            self.settings["mouse"],
            lambda v: self.set_global("mouse", v))
        self.settings_expander.add_row(self.row_mouse)

        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_child(box)
        view.set_content(scroller)
        return view

    def section_title(self, text):
        return Gtk.Label(label=text, xalign=0, css_classes=["section-title"])

    def switch_row(self, title, subtitle, value, on_change):
        row = Adw.SwitchRow(title=title, subtitle=subtitle, active=bool(value))
        row.connect("notify::active", lambda r, _p: on_change(r.get_active()))
        return row

    def slider_row(self, title, lo, hi, step, value, on_change, suffix=""):
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL,
                                         lo, hi, step)
        scale.set_value(value)
        scale.set_hexpand(True)
        scale.set_draw_value(False)
        label = Gtk.Label(label=f"{int(value)}{suffix}",
                          width_chars=6, xalign=1)

        def on_change_inner(sc):
            val = sc.get_value()
            label.set_text(f"{int(val)}{suffix}")
            on_change(val)

        scale.connect("value-changed", on_change_inner)
        row = Adw.ActionRow(title=title)
        row.add_suffix(scale)
        row.add_suffix(label)
        row._scale = scale
        row._label = label
        row._suffix = suffix
        return row

    def combo_row(self, title, choices, value, on_change):
        labels = [label for _key, label in choices]
        keys = [key for key, _label in choices]
        dropdown = Gtk.DropDown.new_from_strings(labels)
        if value in keys:
            dropdown.set_selected(keys.index(value))
        dropdown.set_valign(Gtk.Align.CENTER)
        dropdown.connect(
            "notify::selected",
            lambda dd, _p: on_change(keys[dd.get_selected()]))
        row = Adw.ActionRow(title=title)
        row.add_suffix(dropdown)
        return row

    # ---------------------------------------------------------- 数据刷新

    def reload(self):
        self.wallpapers = scan_wallpapers()
        self.current_id = running_wallpaper_id()
        self.populate()
        self.update_status()
        if self.selected is None and self.current_id:
            match = next((w for w in self.wallpapers
                          if w.wid == self.current_id), None)
            if match:
                self.select(match, apply_now=False)

    def populate(self):
        while (child := self.flow.get_first_child()) is not None:
            self.flow.remove(child)
        self.cards.clear()

        keyword = self.search.get_text().strip().lower()
        want = getattr(self, "active_filter", "")

        for wall in self.wallpapers:
            if want and wall.wtype != want:
                continue
            if keyword and keyword not in wall.title.lower() \
                    and keyword not in wall.wid:
                continue
            self.flow.append(self.make_card(wall))

    def update_highlight(self):
        """只更新「哪张在用」的高亮，不重建网格。

        切换壁纸后如果重建整个网格，滚动条会跳回顶部——用户想再换一张
        就得重新往下翻。所以这里原地改样式。
        """
        for wid, widgets in self.cards.items():
            is_current = wid == self.current_id
            box, badge = widgets["box"], widgets["badge"]
            has_class = box.has_css_class("wallpaper-card-current")
            if is_current and not has_class:
                box.add_css_class("wallpaper-card-current")
            elif not is_current and has_class:
                box.remove_css_class("wallpaper-card-current")
            badge.set_text(self.badge_text(wid))

    def badge_text(self, wid):
        wall = next((w for w in self.wallpapers if w.wid == wid), None)
        if wall is None:
            return ""
        prefix = "使用中 · " if wid == self.current_id else ""
        return f"{prefix}{TYPE_LABEL.get(wall.wtype, wall.wtype)}"

    def make_card(self, wall):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                      css_classes=["wallpaper-card"])
        current = wall.wid == self.current_id
        if current:
            box.add_css_class("wallpaper-card-current")

        picture = Gtk.Picture(can_shrink=True, css_classes=["thumb"])
        picture.set_size_request(-1, int(THUMB * 0.60))
        if wall.preview:
            try:
                texture = Gdk.Texture.new_for_pixbuf(
                    load_thumbnail(wall.preview, THUMB))
                picture.set_paintable(texture)
            except Exception:
                pass
        picture.set_content_fit(Gtk.ContentFit.COVER)
        box.append(picture)

        title = Gtk.Label(label=wall.title, lines=2,
                          ellipsize=Pango.EllipsizeMode.END, wrap=True,
                          wrap_mode=Pango.WrapMode.WORD_CHAR,
                          justify=Gtk.Justification.CENTER,
                          css_classes=["card-title"])
        box.append(title)

        # 固定字符宽度：角标在「场景」和「使用中 · 场景」之间切换时长度会变，
        # 而网格是等宽的，一旦撑动布局就会把滚动位置挤走
        badge = Gtk.Label(css_classes=["card-badge"], width_chars=12,
                          ellipsize=Pango.EllipsizeMode.END, xalign=0.5)
        box.append(badge)

        button = Gtk.Button(child=box, has_frame=False)
        button.set_tooltip_text(f"点击应用：{wall.title}")
        button.connect("clicked", lambda *_: self.select(wall, apply_now=True))

        child = Gtk.FlowBoxChild()
        child.set_child(button)

        # 记下需要在高亮更新时改动的控件，避免重建整个网格
        self.cards[wall.wid] = {"child": child, "box": box, "badge": badge}
        badge.set_text(self.badge_text(wall.wid))
        return child

    def select(self, wall, apply_now):
        if wall is None:
            return
        self.selected = wall
        self.split.set_show_sidebar(True)
        self.panel_btn.set_active(True)
        self.sidebar_title.set_title(wall.title)
        self.subtitle.set_text(
            f"{TYPE_LABEL.get(wall.wtype, wall.wtype)} · {wall.wid}")
        self.preview.set_paintable(None)
        if wall.preview:
            try:
                self.preview.set_paintable(Gdk.Texture.new_for_pixbuf(
                    load_thumbnail(wall.preview, 480)))
            except Exception:
                pass
        self.apply_btn.set_label("使用中" if wall.wid == self.current_id else "应用")
        if not self.switching:
            self.apply_btn.set_sensitive(wall.wid != self.current_id)
        self.load_properties(wall)
        if apply_now:
            self.apply(wall)

    # ---------------------------------------------------------- 壁纸属性

    def load_properties(self, wall):
        """后台线程读属性，避免卡住界面。"""
        self._props_token += 1
        token = self._props_token

        while (child := self.props_box.get_first_child()) is not None:
            self.props_box.remove(child)
        self.props_box.append(Gtk.Label(
            label="正在读取该壁纸的可调项…", xalign=0,
            css_classes=["empty-hint", "card-badge"]))
        self.prop_rows = []

        def worker():
            props = fetch_properties(wall.wid)
            GLib.idle_add(self.render_properties, wall.wid, props, token)

        threading.Thread(target=worker, daemon=True).start()

    def render_properties(self, wid, props, token):
        if token != self._props_token:
            return False
        while (child := self.props_box.get_first_child()) is not None:
            self.props_box.remove(child)

        if not props:
            self.props_box.append(Gtk.Label(
                label="这张壁纸没有可调项", xalign=0,
                css_classes=["empty-hint", "card-badge"]))
            self.props_header.set_text("壁纸属性")
            return False

        saved = self.settings["properties"].get(wid, {})

        for spec in props:
            name = spec["name"]
            value = saved.get(name, spec["value"])
            widget = None
            if spec["type"] == "boolean":
                widget = Adw.SwitchRow(
                    title=spec["label"],
                    active=str(value).strip() not in ("0", "false", "False", ""))
                widget.connect(
                    "notify::active",
                    lambda r, _p, n=name: self.set_property(
                        n, "1" if r.get_active() else "0"))
            elif spec["type"] == "slider":
                try:
                    lo = float(spec["min"] or 0)
                    hi = float(spec["max"] or 1)
                    step = float(spec["step"] or 0.01)
                except ValueError:
                    lo, hi, step = 0.0, 1.0, 0.01
                try:
                    cur = float(value)
                except ValueError:
                    cur = lo
                scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL,
                                                 lo, hi, step)
                scale.set_value(cur)
                scale.set_hexpand(True)
                scale.set_draw_value(False)
                readout = Gtk.Label(label=f"{cur:g}", width_chars=6, xalign=1)
                scale.connect(
                    "value-changed",
                    lambda sc, n=name, lab=readout: (
                        lab.set_text(f"{sc.get_value():g}"),
                        self.set_property(n, f"{sc.get_value():g}")))
                widget = Adw.ActionRow(title=spec["label"])
                widget.add_suffix(scale)
                widget.add_suffix(readout)
            elif spec["type"] == "combo" and spec["options"]:
                values = [opt[0] for opt in spec["options"]]
                labels = [opt[1] for opt in spec["options"]]
                dropdown = Gtk.DropDown.new_from_strings(labels)
                if str(value) in values:
                    dropdown.set_selected(values.index(str(value)))
                dropdown.set_valign(Gtk.Align.CENTER)
                dropdown.connect(
                    "notify::selected",
                    lambda dd, _p, n=name, vals=values:
                        self.set_property(n, vals[dd.get_selected()]))
                widget = Adw.ActionRow(title=spec["label"])
                widget.add_suffix(dropdown)
            elif spec["type"] == "color":
                # 渲染器读写的格式是 "r, g, b, a" 四个 0-1 浮点数
                rgba = Gdk.RGBA()
                try:
                    parts = [float(p) for p in str(value).split(",")[:4]]
                    rgba.red, rgba.green, rgba.blue, rgba.alpha = parts
                except ValueError:
                    rgba.parse("#000000")
                button = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog())
                button.set_rgba(rgba)
                button.set_valign(Gtk.Align.CENTER)
                button.connect(
                    "notify::rgba",
                    lambda btn, _p, n=name: self.set_property(
                        n, "%.6f, %.6f, %.6f, %.6f" % (
                            btn.get_rgba().red, btn.get_rgba().green,
                            btn.get_rgba().blue, btn.get_rgba().alpha)))
                widget = Adw.ActionRow(title=spec["label"])
                widget.add_suffix(button)

            if widget is not None:
                self.props_box.append(widget)
                self.prop_rows.append(widget)

        self.props_header.set_text(f"壁纸属性（{len(self.prop_rows)} 项）")
        return False

    # ---------------------------------------------------------- 设置变更

    def set_global(self, key, value):
        if self.settings.get(key) == value:
            return
        self.settings[key] = value
        save_settings(self.settings)
        self.schedule_reapply()

    def set_property(self, name, value):
        if self.selected is None:
            return
        store = self.settings["properties"].setdefault(self.selected.wid, {})
        if store.get(name) == value:
            return
        store[name] = value
        save_settings(self.settings)
        self.schedule_reapply()

    def schedule_reapply(self):
        """设置改动后重新应用壁纸。

        连续拖动滑块会触发很多次，所以做个防抖，避免把渲染器反复重启。
        """
        if self.current_id is None:
            return
        if self._reapply_source is not None:
            GLib.source_remove(self._reapply_source)
        self._reapply_source = GLib.timeout_add(700, self._do_reapply)

    def _do_reapply(self):
        self._reapply_source = None
        target = next((w for w in self.wallpapers if w.wid == self.current_id),
                      None)
        if target is not None:
            self.apply(target, quiet=True)
        return False

    # ---------------------------------------------------------- 操作

    def run_script_async(self, args, callback):
        """后台跑 start-wallpaper.sh，别阻塞界面。

        以前是同步 subprocess.run，切换壁纸时整个窗口会卡住好几秒。
        """
        def worker():
            try:
                result = subprocess.run([SCRIPT] + args, capture_output=True,
                                        text=True, timeout=120)
            except Exception as exc:  # noqa: BLE001
                result = subprocess.CompletedProcess(args, 1, "", str(exc))
            GLib.idle_add(callback, result)
        threading.Thread(target=worker, daemon=True).start()

    def set_busy(self, busy, text=""):
        self.switching = busy
        for widget in (self.stop_btn, self.panel_btn):
            widget.set_sensitive(not busy)
        if busy:
            self.spinner.start()
            self.spinner.set_visible(True)
            if text:
                self.status.set_text(text)
            self.apply_btn.set_sensitive(False)
        else:
            self.spinner.stop()
            self.spinner.set_visible(False)

    def apply(self, wall, quiet=False):
        if wall is None or getattr(self, "switching", False):
            return
        self.set_busy(True, f"正在切换：{wall.title} …")

        def done(result):
            self.set_busy(False)
            if result.returncode != 0:
                detail = (result.stderr or "").strip().splitlines()
                self.toast_overlay.add_toast(Adw.Toast(
                    title=f"启动失败：{detail[-1][:100] if detail else '见日志'}"))
                self.update_status()
                return False
            self.current_id = wall.wid
            self.write_autostart(wall.wid)
            self.update_highlight()      # 原地更新，不重建网格（否则滚动条跳顶）
            self.update_status()
            self.apply_btn.set_sensitive(False)
            self.apply_btn.set_label("使用中")
            if not quiet:
                self.toast_overlay.add_toast(Adw.Toast(title=f"已应用：{wall.title}"))
            return False

        self.run_script_async([wall.wid], done)

    def on_stop(self, _btn):
        self.set_busy(True, "正在停止 …")

        def done(_result):
            self.set_busy(False)
            self.current_id = None
            self.update_highlight()
            self.update_status()
            self.apply_btn.set_sensitive(True)
            self.apply_btn.set_label("应用")
            self.toast_overlay.add_toast(Adw.Toast(title="已停止动态壁纸"))
            return False

        self.run_script_async(["--stop"], done)

    def write_autostart(self, wid):
        try:
            os.makedirs(os.path.dirname(AUTOSTART), exist_ok=True)
            with open(AUTOSTART, "w", encoding="utf-8") as fh:
                fh.write(
                    "[Desktop Entry]\n"
                    "Type=Application\n"
                    "Name=Wallpaper Engine 动态壁纸\n"
                    "Comment=linux-wallpaperengine 渲染器\n"
                    f'Exec=bash -c "sleep 5; {SCRIPT} {wid}"\n'
                    "X-GNOME-Autostart-enabled=true\n"
                    "NoDisplay=false\n"
                    "Terminal=false\n")
        except Exception:
            pass

    def update_status(self):
        total = len(self.wallpapers)
        if self.current_id:
            title = next((w.title for w in self.wallpapers
                          if w.wid == self.current_id), self.current_id)
            self.status.set_text(f"正在使用：{title}　·　共 {total} 张壁纸")
            self.stop_btn.set_sensitive(True)
        else:
            self.status.set_text(f"当前没有动态壁纸在运行　·　共 {total} 张壁纸")
            # 正在切换时别把「停止」重新启用，否则状态文案会被覆盖
            self.stop_btn.set_sensitive(not self.switching)


class PickerApp(Adw.Application):
    def __init__(self, snapshot_path=None, preselect=None):
        super().__init__(application_id="io.github.fitz.WallpaperPicker",
                         flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.snapshot_path = snapshot_path
        self.preselect = preselect

    def do_activate(self):
        window = self.props.active_window or WallpaperPicker(self)
        if self.snapshot_path:
            # 开发截图时用更高的画布，方便一次看全整个面板
            window.set_default_size(1280, 1400)
        window.present()
        if self.preselect:
            match = next((w for w in window.wallpapers
                          if w.wid == self.preselect), None)
            if match is not None:
                window.select(match, apply_now=False)
        if self.snapshot_path:
            GLib.timeout_add(2500, self._snapshot, window)

    def _snapshot(self, window):
        """把窗口内容自己渲染成 PNG（开发自查用，绕开系统截图权限限制）。"""
        try:
            paintable = Gtk.WidgetPaintable.new(window.get_content())
            wide = paintable.get_intrinsic_width()
            high = paintable.get_intrinsic_height()
            snapshot = Gtk.Snapshot.new()
            bg = Gdk.RGBA()
            bg.red = bg.green = bg.blue = 0.14
            bg.alpha = 1.0
            snapshot.append_color(bg, Graphene.Rect().init(0, 0, wide, high))
            paintable.snapshot(snapshot, wide, high)
            renderer = window.get_native().get_renderer()
            texture = renderer.render_texture(snapshot.to_node(), None)
            texture.save_to_png(self.snapshot_path)
            print(f"已保存界面截图: {self.snapshot_path} ({wide}x{high})")
        except Exception as exc:  # noqa: BLE001
            print(f"界面截图失败: {exc}")
        self.quit()
        return False


if __name__ == "__main__":
    shot = None
    if "--snapshot" in sys.argv:
        index = sys.argv.index("--snapshot")
        shot = sys.argv[index + 1]
        del sys.argv[index:index + 2]
    select = None
    if "--select" in sys.argv:
        index = sys.argv.index("--select")
        select = sys.argv[index + 1]
        del sys.argv[index:index + 2]
    sys.exit(PickerApp(shot, select).run(sys.argv))
