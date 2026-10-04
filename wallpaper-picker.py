#!/usr/bin/env python3
"""Wallpaper Engine 壁纸选择器 —— GNOME 原生 GTK4 / libadwaita 界面

列出本地 Steam 创意工坊里已订阅的全部壁纸，点一下即切换。
底层调用同目录下的 start-wallpaper.sh（渲染器 + GNOME 扩展）。
"""

import glob
import json
import os
import subprocess
import sys

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

# 运行时状态放在缓存目录，不污染源码树（与 start-wallpaper.sh 保持一致）
STATE_DIR = os.environ.get(
    "LWE_STATE_DIR",
    os.path.join(os.environ.get("XDG_CACHE_HOME", f"{HOME}/.cache"),
                 "wallpaper-picker"))
PIDFILE = os.path.join(STATE_DIR, "wallpaper.pid")

# 常见的 Steam 安装布局，按优先级排列
WORKSHOP_CANDIDATES = [
    f"{HOME}/.local/share/Steam/steamapps/workshop/content/431960",
    f"{HOME}/.steam/steam/steamapps/workshop/content/431960",
    f"{HOME}/.steam/debian-installation/steamapps/workshop/content/431960",
    f"{HOME}/.var/app/com.valvesoftware.Steam/.local/share/Steam"
    "/steamapps/workshop/content/431960",
]


def find_workshop():
    """返回创意工坊内容目录；都找不到时返回 None。"""
    for path in WORKSHOP_CANDIDATES:
        if os.path.isdir(path):
            return path
    return None


TYPE_LABEL = {"scene": "场景", "video": "视频", "web": "网页"}
THUMB = 300  # 缩略图边长，42 张全载入约 15MB

CSS = """
.card {
    border-radius: 12px;
    background-color: alpha(currentColor, 0.06);
    padding: 6px;
}
.card:hover   { background-color: alpha(currentColor, 0.12); }
.card-current { background-color: alpha(@accent_bg_color, 0.30); }
.card-title   { font-size: 0.85em; }
.card-badge   { font-size: 0.72em; opacity: 0.65; }
.thumb {
    border-radius: 8px;
    /* 描边是必须的：不少壁纸本身就很暗，没有边界会和深色卡片糊成一片 */
    outline: 1px solid alpha(currentColor, 0.18);
    outline-offset: -1px;
}
"""


def load_thumbnail(path, size):
    """载入缩略图。

    两个坑要注意：
    1. GdkPixbuf 的 new_from_file_at_scale 遇到 GIF 动图会直接抛
       "Not all frames of the GIF image were loaded"，所以动图得另走
       PixbufAnimation 取首帧再自己缩放。
    2. 不少壁纸本身画面极暗，纹理带 alpha 的先合成到深色底上，
       免得和卡片背景糊在一起。
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


class Wallpaper:
    __slots__ = ("wid", "wtype", "title", "preview")

    def __init__(self, wid, wtype, title, preview):
        self.wid = wid
        self.wtype = wtype
        self.title = title
        self.preview = preview


def scan_wallpapers():
    """扫描本地创意工坊目录，返回壁纸列表。"""
    workshop = find_workshop()
    if workshop is None:
        return []
    found = []
    for d in sorted(glob.glob(os.path.join(workshop, "*/"))):
        pj = os.path.join(d, "project.json")
        if not os.path.exists(pj):
            continue
        wid = os.path.basename(d.rstrip("/"))
        try:
            meta = json.load(open(pj, encoding="utf-8-sig"))
            title = str(meta.get("title", wid)).strip()
            wtype = str(meta.get("type", "?")).lower()
        except Exception:
            title, wtype = f"{wid}（配置无法解析）", "?"
        preview = ""
        for name in ("preview.jpg", "preview.png", "preview.gif"):
            if os.path.exists(os.path.join(d, name)):
                preview = os.path.join(d, name)
                break
        found.append(Wallpaper(wid, wtype, title, preview))
    order = {"scene": 0, "video": 1, "web": 2}
    found.sort(key=lambda w: (order.get(w.wtype, 9), w.title))
    return found


def running_wallpaper_id():
    """当前正在渲染的壁纸 ID；用 pidfile + 进程命令行双重确认。"""
    try:
        pid = open(PIDFILE).read().strip()
        cmdline = open(f"/proc/{pid}/cmdline", "rb").read().split(b"\0")
        args = [a.decode() for a in cmdline if a]
        if "--bg" in args:
            return args[args.index("--bg") + 1]
    except Exception:
        pass
    return None


def extension_loaded():
    """GNOME Shell 是否已经加载了扩展（注销重登后才为真）。"""
    try:
        out = subprocess.run(
            ["gnome-extensions", "info", EXT_UUID],
            capture_output=True, text=True, timeout=10,
        )
        return out.returncode == 0
    except Exception:
        return False


class PickerWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="壁纸", default_width=1100,
                         default_height=760)
        self.wallpapers = []
        self.current_id = None
        self.cards = {}

        provider = Gtk.CssProvider()
        provider.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self.toast_overlay = Adw.ToastOverlay()
        toolbar = Adw.ToolbarView()
        self.toast_overlay.set_child(toolbar)
        self.set_content(self.toast_overlay)

        header = Adw.HeaderBar()
        toolbar.add_top_bar(header)

        self.search = Gtk.SearchEntry(placeholder_text="搜索壁纸…", width_chars=22)
        self.search.connect("search-changed", lambda *_: self.populate())
        header.set_title_widget(self.search)

        refresh_btn = Gtk.Button(icon_name="view-refresh-symbolic",
                                 tooltip_text="重新扫描壁纸")
        refresh_btn.connect("clicked", lambda *_: self.reload())
        header.pack_start(refresh_btn)

        self.stop_btn = Gtk.Button(label="停止", tooltip_text="停止当前动态壁纸")
        self.stop_btn.add_css_class("destructive-action")
        self.stop_btn.connect("clicked", self.on_stop)
        header.pack_end(self.stop_btn)

        self.filter = Gtk.DropDown.new_from_strings(
            ["全部", "场景", "视频", "网页"])
        self.filter.connect("notify::selected", lambda *_: self.populate())
        header.pack_start(self.filter)

        # 扩展没生效时给个明确提示，而不是让用户对着没反应的桌面发呆
        self.warn = Adw.Banner(
            title="GNOME 扩展还没被加载：请注销后重新登录一次，动态壁纸才会显示到桌面上",
            revealed=not extension_loaded(),
        )
        self.warn.set_button_label("知道了")
        toolbar.add_top_bar(self.warn)

        self.flow = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.NONE, homogeneous=True,
            min_children_per_line=2, max_children_per_line=6,
            row_spacing=10, column_spacing=10,
            margin_top=14, margin_bottom=14, margin_start=14, margin_end=14,
        )
        scroller = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        scroller.set_child(self.flow)
        toolbar.set_content(scroller)

        self.status = Gtk.Label(xalign=0, margin_start=14, margin_bottom=10,
                                css_classes=["dim-label"])
        toolbar.add_bottom_bar(self.status)

        self.reload()

    # ---------- 数据加载 ----------

    def reload(self):
        self.wallpapers = scan_wallpapers()
        self.current_id = running_wallpaper_id()
        self.populate()
        self.update_status()

    def populate(self):
        while (child := self.flow.get_first_child()) is not None:
            self.flow.remove(child)
        self.cards.clear()

        keyword = self.search.get_text().strip().lower()
        want = ["", "scene", "video", "web"][self.filter.get_selected()]

        for w in self.wallpapers:
            if want and w.wtype != want:
                continue
            if keyword and keyword not in w.title.lower() and keyword not in w.wid:
                continue
            card = self.make_card(w)
            self.cards[w.wid] = card
            self.flow.append(card)

    def make_card(self, w):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                      css_classes=["card"])
        if w.wid == self.current_id:
            box.add_css_class("card-current")

        picture = None
        if w.preview:
            try:
                texture = Gdk.Texture.new_for_pixbuf(
                    load_thumbnail(w.preview, THUMB))
                picture = Gtk.Picture.new_for_paintable(texture)
            except Exception:
                picture = None
        if picture is None:
            picture = Gtk.Picture()
        picture.set_can_shrink(True)
        picture.set_size_request(-1, int(THUMB * 0.62))
        picture.add_css_class("thumb")
        picture.set_content_fit(Gtk.ContentFit.COVER)
        box.append(picture)

        title = Gtk.Label(label=w.title, lines=2,
                          ellipsize=Pango.EllipsizeMode.END,
                          wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
                          justify=Gtk.Justification.CENTER,
                          css_classes=["card-title"])
        box.append(title)

        badge = Gtk.Label(
            label=f"{TYPE_LABEL.get(w.wtype, w.wtype)} · {w.wid}",
            css_classes=["card-badge"])
        box.append(badge)

        button = Gtk.Button(child=box, has_frame=False)
        button.connect("clicked", lambda *_: self.apply(w))
        button.set_tooltip_text(f"点击应用：{w.title}")

        child = Gtk.FlowBoxChild()
        child.set_child(button)
        return child

    # ---------- 操作 ----------

    def apply(self, w):
        result = subprocess.run([SCRIPT, w.wid], capture_output=True,
                                text=True, timeout=60)
        if result.returncode != 0:
            self.toast_overlay.add_toast(Adw.Toast(
                title=f"启动失败：{result.stderr.strip()[:120] or '见 wallpaper.log'}"))
            return
        self.current_id = w.wid
        self.write_autostart(w.wid)
        self.populate()
        self.update_status()
        self.toast_overlay.add_toast(Adw.Toast(title=f"已切换：{w.title}"))

    def on_stop(self, _btn):
        subprocess.run([SCRIPT, "--stop"], capture_output=True, timeout=30)
        self.current_id = None
        self.populate()
        self.update_status()
        self.toast_overlay.add_toast(Adw.Toast(title="已停止动态壁纸"))

    def write_autostart(self, wid):
        """把当前壁纸写成登录默认，下次进来还是它。"""
        try:
            os.makedirs(os.path.dirname(AUTOSTART), exist_ok=True)
            with open(AUTOSTART, "w", encoding="utf-8") as f:
                f.write(
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
            self.status.set_text(f"正在渲染：{title}　·　共 {total} 张壁纸")
            self.stop_btn.set_sensitive(True)
        else:
            self.status.set_text(f"当前没有运行中的动态壁纸　·　共 {total} 张壁纸")
            self.stop_btn.set_sensitive(False)


class PickerApp(Adw.Application):
    def __init__(self, snapshot_path=None):
        super().__init__(application_id="io.github.fitz.WallpaperPicker",
                         flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.snapshot_path = snapshot_path

    def do_activate(self):
        window = self.props.active_window or PickerWindow(self)
        window.present()
        if self.snapshot_path:
            GLib.timeout_add(2000, self._snapshot, window)

    def _snapshot(self, window):
        """把窗口内容自己渲染成 PNG（用于开发自查，避免受系统截图权限限制）。"""
        try:
            paintable = Gtk.WidgetPaintable.new(window.get_content())
            w = paintable.get_intrinsic_width()
            h = paintable.get_intrinsic_height()
            snapshot = Gtk.Snapshot.new()
            # 先铺一层底色：get_content() 不含窗口自身背景，不铺的话
            # 深色界面的浅色文字会落在透明底上，看着像一片空白
            bg = Gdk.RGBA()
            bg.red = bg.green = bg.blue = 0.14
            bg.alpha = 1.0
            snapshot.append_color(bg, Graphene.Rect().init(0, 0, w, h))
            paintable.snapshot(snapshot, w, h)
            renderer = window.get_native().get_renderer()
            texture = renderer.render_texture(snapshot.to_node(), None)
            texture.save_to_png(self.snapshot_path)
            print(f"已保存界面截图: {self.snapshot_path} ({w}x{h})")
        except Exception as exc:  # noqa: BLE001
            print(f"界面截图失败: {exc}")
        self.quit()
        return False


if __name__ == "__main__":
    shot = None
    if "--snapshot" in sys.argv:
        i = sys.argv.index("--snapshot")
        shot = sys.argv[i + 1]
        del sys.argv[i:i + 2]
    sys.exit(PickerApp(shot).run(sys.argv))
