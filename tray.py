"""顶栏托盘图标（StatusNotifierItem）—— 纯 Gio 实现，无额外系统依赖。

为什么不用 Ayatana.AppIndicator3 的绑定：它依赖 Gtk-3.0，与本应用加载的
Gtk-4.0 同进程冲突。所以按 StatusNotifierItem 规范直接导出 D-Bus 对象：

  /StatusNotifierItem   org.kde.StatusNotifierItem（图标/标题/tooltip/激活）
  /StatusNotifierMenu   com.canonical.dbusmenu（右键菜单，精简静态实现）

GNOME 侧需要 AppIndicator 支持扩展，Ubuntu 24.04 预装（微信、输入法等
托盘图标走同一机制）。没有该扩展的桌面注册会失败，此时托盘不可用，
窗口关闭恢复为退出行为。
"""

import os

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gio, GLib, GdkPixbuf

SNI_PATH = "/StatusNotifierItem"
MENU_PATH = "/StatusNotifierMenu"
# 托盘宿主的名字/路径组合，按序尝试。GNOME 的 AppIndicator 扩展持有
# org.kde.* 名字且对象在 /StatusNotifierWatcher（不是规范里的 /org/kde/...）
WATCHERS = (
    ("org.kde.StatusNotifierWatcher", "/StatusNotifierWatcher"),
    ("org.kde.StatusNotifierWatcher", "/org/kde/StatusNotifierWatcher"),
    ("org.freedesktop.StatusNotifierWatcher", "/StatusNotifierWatcher"),
    ("org.freedesktop.StatusNotifierWatcher", "/org/freedesktop/StatusNotifierWatcher"),
)
ICON_NAME = "io.github.fitz.WallpaperPicker"

SNI_XML = """<node>
  <interface name="org.kde.StatusNotifierItem">
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="WindowId" type="i" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="IconPixmap" type="a(iiay)" access="read"/>
    <property name="OverlayIconName" type="s" access="read"/>
    <property name="OverlayIconPixmap" type="a(iiay)" access="read"/>
    <property name="AttentionIconName" type="s" access="read"/>
    <property name="AttentionIconPixmap" type="a(iiay)" access="read"/>
    <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
    <property name="Menu" type="o" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <method name="Activate">
      <arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/>
    </method>
    <method name="SecondaryActivate">
      <arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/>
    </method>
    <method name="Scroll">
      <arg name="delta" type="i" direction="in"/>
      <arg name="orientation" type="s" direction="in"/>
    </method>
  </interface>
</node>"""

MENU_XML = """<node>
  <interface name="com.canonical.dbusmenu">
    <property name="Version" type="u" access="read"/>
    <property name="TextDirection" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconThemePath" type="as" access="read"/>
    <method name="GetLayout">
      <arg name="parentId" type="i" direction="in"/>
      <arg name="recursionDepth" type="i" direction="in"/>
      <arg name="propertyNames" type="as" direction="in"/>
      <arg name="revision" type="u" direction="out"/>
      <arg name="layout" type="(ia{sv}av)" direction="out"/>
    </method>
    <method name="GetGroupProperties">
      <arg name="ids" type="ai" direction="in"/>
      <arg name="propertyNames" type="as" direction="in"/>
      <arg name="properties" type="a(ia{sv})" direction="out"/>
    </method>
    <method name="GetProperty">
      <arg name="id" type="i" direction="in"/>
      <arg name="name" type="s" direction="in"/>
      <arg name="value" type="v" direction="out"/>
    </method>
    <method name="Event">
      <arg name="id" type="i" direction="in"/>
      <arg name="eventId" type="s" direction="in"/>
      <arg name="data" type="v" direction="in"/>
      <arg name="timestamp" type="u" direction="in"/>
    </method>
    <method name="EventGroup">
      <arg name="events" type="a(isvu)" direction="in"/>
      <arg name="idErrors" type="ai" direction="out"/>
    </method>
    <method name="AboutToShow">
      <arg name="id" type="i" direction="in"/>
      <arg name="needUpdate" type="b" direction="out"/>
    </method>
    <signal name="ItemsPropertiesUpdated">
      <arg name="updatedProps" type="a(ia{sv})"/>
      <arg name="removedProps" type="a(ias)"/>
    </signal>
    <signal name="LayoutUpdated">
      <arg name="revision" type="u"/>
      <arg name="parent" type="i"/>
    </signal>
    <signal name="ItemActivationRequested">
      <arg name="id" type="i"/>
      <arg name="timestamp" type="u"/>
    </signal>
  </interface>
</node>"""

# 菜单：id -> (标签, 动作名)。动作由调用方注入。
MENU_ITEMS = [
    (1, "打开壁纸选择器", "open"),
    (2, "停止动态壁纸", "stop"),
    (3, "退出", "quit"),
]


class TrayIcon:
    """StatusNotifierItem 托盘图标。动作通过 on_action 回调注入：

        TrayIcon(on_open=..., on_stop=..., on_quit=...)
    """

    def __init__(self, on_open, on_stop, on_quit):
        self.available = False
        self._registered = False
        self._revision = 0
        self._reg_ids = []
        self._watch_ids = []
        self._actions = {"open": on_open, "stop": on_stop, "quit": on_quit}
        self._pixmap = self._icon_pixmap()
        self._sni_info = Gio.DBusNodeInfo.new_for_xml(SNI_XML)
        self._menu_info = Gio.DBusNodeInfo.new_for_xml(MENU_XML)

    def start(self):
        """监视托盘宿主（扩展/面板），出现即注册。挂掉重开会自动重新注册。"""
        for name in dict.fromkeys(name for name, _path in WATCHERS):
            self._watch_ids.append(Gio.bus_watch_name(
                Gio.BusType.SESSION, name, Gio.BusNameWatcherFlags.NONE,
                self._on_watcher_appeared, None))

    # ------------------------------------------------------------ 注册

    def _on_watcher_appeared(self, bus, name, *_args):
        if self._registered:
            return
        if not self._reg_ids:
            try:
                self._reg_ids.append(bus.register_object(
                    SNI_PATH, self._sni_info.interfaces[0],
                    self._sni_call, self._sni_get_prop, None))
                self._reg_ids.append(bus.register_object(
                    MENU_PATH, self._menu_info.interfaces[0],
                    self._menu_call, self._menu_get_prop, None))
            except Exception as exc:
                print(f"托盘对象导出失败: {exc}")
                return
        for wname, wpath in WATCHERS:
            if wname != name:
                continue
            try:
                bus.call_sync(
                    name, wpath, "org.kde.StatusNotifierWatcher",
                    "RegisterStatusNotifierItem",
                    GLib.Variant("(s)", (bus.get_unique_name(),)),
                    None, Gio.DBusCallFlags.NONE, 3000, None)
            except Exception as exc:
                print(f"托盘注册失败 {name}{wpath}: {exc}")
                continue
            self._registered = True
            self.available = True
            self._revision += 1
            bus.emit_signal(None, MENU_PATH, "com.canonical.dbusmenu",
                            "LayoutUpdated",
                            GLib.Variant("(ui)", (self._revision, 0)))
            print("托盘图标已注册")
            return
        print("托盘注册失败（桌面可能不支持 AppIndicator）")

    # ------------------------------------------------------- SNI 接口

    def _icon_pixmap(self):
        """SNI 的 IconPixmap 要 ARGB32 大端字节序；先找主题图标，
        找不到再退仓库里的 PNG（源码直接运行且没跑过 install.sh 的场景）。"""
        candidates = []
        try:
            from gi.repository import Gdk, Gtk
            theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
            paintable = theme.lookup_icon(
                ICON_NAME, None, 64, 1, Gtk.TextDirection.NONE, 0)
            f = paintable.get_file()
            if f is not None:
                candidates.append(f.get_path())
        except Exception:
            pass
        repo_png = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                                "icons", "hicolor", "64x64", "apps",
                                f"{ICON_NAME}.png")
        candidates.append(repo_png)
        for path in candidates:
            try:
                pb = GdkPixbuf.Pixbuf.new_from_file(path)
                w, h = pb.get_width(), pb.get_height()
                rgba = pb.get_pixels()
                argb = bytearray()
                for i in range(0, len(rgba), 4):
                    argb += bytes((rgba[i + 3], rgba[i], rgba[i + 1], rgba[i + 2]))
                return [(w, h, bytes(argb))]
            except Exception:
                continue
        return []

    def _sni_props(self):
        empty_pixmap = GLib.Variant("a(iiay)", [])
        return {
            "Category": GLib.Variant("s", "ApplicationStatus"),
            "Id": GLib.Variant("s", "wallpaper-picker"),
            "Title": GLib.Variant("s", "壁纸选择器"),
            "Status": GLib.Variant("s", "Active"),
            "WindowId": GLib.Variant("i", 0),
            "IconName": GLib.Variant("s", ICON_NAME),
            "IconPixmap": GLib.Variant("a(iiay)", self._pixmap),
            "OverlayIconName": GLib.Variant("s", ""),
            "OverlayIconPixmap": empty_pixmap,
            "AttentionIconName": GLib.Variant("s", ""),
            "AttentionIconPixmap": empty_pixmap,
            "ToolTip": GLib.Variant("(sa(iiay)ss)",
                                    ("壁纸选择器", [], "",
                                     "Wallpaper Engine 动态壁纸")),
            "Menu": GLib.Variant("o", MENU_PATH),
            "ItemIsMenu": GLib.Variant("b", True),
        }

    def _sni_get_prop(self, _conn, _sender, _path, _iface, name):
        try:
            return self._sni_props()[name]
        except KeyError:
            raise GLib.Error(Gio.DBusError.UNKNOWN_PROPERTY, None,
                             f"no such property: {name}")

    def _sni_call(self, conn, sender, path, iface, name, params, invocation):
        if name in ("Activate", "SecondaryActivate"):
            self._dispatch("open")
        invocation.return_value(None)

    # ---------------------------------------------------- DBusMenu 接口

    @staticmethod
    def _item_props(label):
        return {
            "label": GLib.Variant("s", label),
            "enabled": GLib.Variant("b", True),
            "visible": GLib.Variant("b", True),
        }

    def _layout_node(self, depth):
        """返回 (id, props, children) 的裸元组；children 里每个子项是
        已包装好的 Variant。注意这里不能返回已构建的 GLib.Variant——
        Variant.__new__ 会先 unpack 再重组，av 里的子项会退化成裸值。"""
        root_props = {"children-display": GLib.Variant("s", "submenu")}
        children = []
        for item_id, label, _action in MENU_ITEMS:
            node = (item_id, self._item_props(label), [])
            children.append(GLib.Variant("(ia{sv}av)", node))
        return (0, root_props, children)

    def _menu_get_prop(self, _conn, _sender, _path, _iface, name):
        props = {
            "Version": GLib.Variant("u", 3),
            "TextDirection": GLib.Variant("s", "ltr"),
            "Status": GLib.Variant("s", "normal"),
            "IconThemePath": GLib.Variant("as", []),
        }
        try:
            return props[name]
        except KeyError:
            raise GLib.Error(Gio.DBusError.UNKNOWN_PROPERTY, None,
                             f"no such property: {name}")

    def _menu_call(self, conn, sender, path, iface, name, params, invocation):
        if name == "GetLayout":
            _parent, depth, _names = params.unpack()
            invocation.return_value(
                GLib.Variant("(u(ia{sv}av))",
                             (self._revision, self._layout_node(depth))))
        elif name == "GetGroupProperties":
            invocation.return_value(GLib.Variant("(a(ia{sv}))", ([],)))
        elif name == "GetProperty":
            item_id, prop = params.unpack()
            if prop == "label":
                label = next((l for i, l, _a in MENU_ITEMS if i == item_id), "")
                invocation.return_value(GLib.Variant("v",
                                                     GLib.Variant("s", label)))
            else:
                invocation.return_value(GLib.Variant("v", GLib.Variant("s", "")))
        elif name == "Event":
            item_id, event, _data, _ts = params.unpack()
            if event == "clicked":
                action = next((a for i, _l, a in MENU_ITEMS if i == item_id), None)
                self._dispatch(action)
            invocation.return_value(None)
        elif name == "EventGroup":
            invocation.return_value(GLib.Variant("(ai)", ([],)))
        elif name == "AboutToShow":
            invocation.return_value(GLib.Variant("(b)", (False,)))
        else:
            invocation.return_value(None)

    # ------------------------------------------------------------- 动作

    def _dispatch(self, action):
        if action and action in self._actions:
            self._actions[action]()
