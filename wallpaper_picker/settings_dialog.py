"""设置对话框 —— Adw.PreferencesWindow，主界面顶栏齿轮按钮打开。

分组：应用行为（自启/启动恢复/关闭窗口行为）、壁纸来源（手动指定
非 Steam 场景的壁纸目录）、语言、关于。
所有改动即时持久化（复用 picker.settings + schedule_save 基建）；
界面语言的切换在重启应用后生效。

窗口形态：独立非模态窗口（不锁主窗口输入，关闭只是隐藏、实例复用）。
"""

import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from .i18n import tr
from .paths import find_screens

LANG_KEYS = ["system", "zh", "en"]
LANG_LABELS = ["跟随系统", "简体中文", "English"]
CLOSE_LABELS = ["隐藏到托盘（推荐）", "直接退出"]


class SettingsDialog(Adw.PreferencesWindow):
    def __init__(self, picker, app_version, repo_url, **kwargs):
        super().__init__(**kwargs)
        self._picker = picker
        self._app_version = app_version
        s = picker.settings
        self.set_transient_for(picker)
        # 不用模态：模态会锁住主窗口输入，一旦交互异常整个应用就像卡死。
        # 非模态下它是一个独立窗口，主界面随时可以继续操作。
        self.set_modal(False)
        self.set_title(tr("设置"))
        self.set_default_size(560, 480)
        # 点 X 是隐藏而不是销毁：实例被主窗口缓存复用，关闭再开不重建
        self.connect("close-request", self._on_close)

        # ---- 单页布局：三个分组竖排，滚动查看（用户明确要求不分页）----
        page = Adw.PreferencesPage()

        behavior = Adw.PreferencesGroup(title=tr("应用行为"))

        row_autostart = Adw.SwitchRow(
            title=tr("开机自动启动"),
            subtitle=tr("登录后自动恢复上次的壁纸，并在顶栏常驻托盘图标"),
            active=bool(s.get("autostart", True)))
        row_autostart.connect(
            "notify::active",
            lambda r, _p: picker.set_autostart(r.get_active()))
        behavior.add(row_autostart)

        row_restore = Adw.SwitchRow(
            title=tr("启动时恢复壁纸"),
            subtitle=tr("手动打开软件时也自动应用上次的壁纸"),
            active=bool(s.get("restore_on_start", False)))
        row_restore.connect(
            "notify::active",
            lambda r, _p: picker.set_restore_on_start(r.get_active()))
        behavior.add(row_restore)

        close_model = Gtk.StringList.new([tr(l) for l in CLOSE_LABELS])
        row_close = Adw.ComboRow(
            title=tr("关闭窗口时"),
            subtitle=tr("托盘不可用时始终退出程序"),
            model=close_model)
        row_close.set_selected(
            0 if s.get("close_action", "tray") == "tray" else 1)
        row_close.connect(
            "notify::selected",
            lambda r, _p: picker.set_close_action(
                "tray" if r.get_selected() == 0 else "quit"))
        behavior.add(row_close)
        page.add(behavior)

        # ---- 壁纸来源 ----
        # 非 Steam 场景（第三方下载、手动整理）在这里指定壁纸目录。
        # 渲染器侧不需要任何配置：启动器会把不在 Steam 工坊下的壁纸
        # 以完整路径传给它（--bg 含 / 时渲染器直接按路径处理）。
        ws_group = Adw.PreferencesGroup(title=tr("壁纸来源"))
        row_ws = Adw.ActionRow(title=tr("壁纸目录"))
        self._ws_row = row_ws
        btn_browse = Gtk.Button(label=tr("浏览…"), valign=Gtk.Align.CENTER)
        btn_browse.connect("clicked", self._choose_workshop)
        btn_reset = Gtk.Button(label=tr("重置"), valign=Gtk.Align.CENTER)
        btn_reset.connect("clicked", self._reset_workshop)
        row_ws.add_suffix(btn_browse)
        row_ws.add_suffix(btn_reset)
        self._ws_browse_btn = btn_browse
        self._ws_reset_btn = btn_reset
        self._update_workshop_row()
        ws_group.add(row_ws)
        page.add(ws_group)

        # ---- 显示器（多屏概览；逐屏更改在主窗口的「应用到显示器」）----
        mm_group = Adw.PreferencesGroup(title=tr("显示器"))
        row_clone = Adw.SwitchRow(
            title=tr("所有显示器使用同一壁纸"),
            subtitle=tr("关闭后可在主窗口逐屏选择不同的壁纸"),
            active=bool(s.get("clone", True)))
        row_clone.connect(
            "notify::active",
            lambda r, _p: picker.set_clone(r.get_active()))
        mm_group.add(row_clone)
        screens_map = s.get("screens") or {}
        for conn, primary in find_screens():
            wid = screens_map.get(conn) or s.get("last")
            title = next((w.title for w in picker.wallpapers if w.wid == wid),
                         wid or "—")
            mm_group.add(Adw.ActionRow(
                title=tr("{}（主）").format(conn) if primary else conn,
                subtitle=title))
        page.add(mm_group)

        # ---- 语言 ----
        lang_group = Adw.PreferencesGroup(title=tr("外观"))
        row_lang = Adw.ComboRow(
            title=tr("界面语言 / Language"),
            subtitle=tr("切换后需要重启应用才能生效"),
            model=Gtk.StringList.new([tr(l) for l in LANG_LABELS]))
        current = s.get("language", "system")
        row_lang.set_selected(
            LANG_KEYS.index(current) if current in LANG_KEYS else 0)
        row_lang.connect(
            "notify::selected",
            lambda r, _p: picker.set_language(LANG_KEYS[r.get_selected()]))
        lang_group.add(row_lang)
        page.add(lang_group)

        # ---- 关于 ----
        about_group = Adw.PreferencesGroup()
        about_row = Adw.ActionRow(
            title=tr("关于壁纸选择器"),
            subtitle=tr("版本、许可证与项目链接"),
            activatable=True)
        about_row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        about_row.connect("activated", lambda _r: self._show_about(repo_url))
        about_group.add(about_row)
        page.add(about_group)

        self.add(page)

    def _on_close(self, *_args):
        self.hide()
        return True

    # ---- 壁纸目录选择 ----

    def _update_workshop_row(self):
        """副标题跟当前生效状态走，包括环境变量优先时的说明。"""
        env = os.environ.get("LWE_WORKSHOP")
        if env:
            self._ws_row.set_subtitle(
                tr("环境变量 LWE_WORKSHOP 已设置，优先于此处（当前：{}）").format(env))
            self._ws_browse_btn.set_sensitive(False)
            self._ws_reset_btn.set_sensitive(False)
            return
        chosen = self._picker.settings.get("workshop")
        if chosen:
            self._ws_row.set_subtitle(chosen)
        else:
            self._ws_row.set_subtitle(
                tr("自动探测 Steam 创意工坊；第三方下载的壁纸可在此指定目录"))
        self._ws_browse_btn.set_sensitive(True)
        self._ws_reset_btn.set_sensitive(bool(chosen))

    def _choose_workshop(self, _btn):
        dialog = Gtk.FileDialog(title=tr("选择壁纸目录"))
        dialog.select_folder(self, None, self._on_workshop_selected)

    def _on_workshop_selected(self, dialog, result):
        try:
            folder = dialog.select_folder_finish(result)
        except Exception:
            return          # 用户取消或关闭对话框，不是错误
        path = folder.get_path() if folder is not None else None
        if not path:
            return
        self._picker.set_workshop(path)
        self._update_workshop_row()

    def _reset_workshop(self, _btn):
        self._picker.set_workshop(None)
        self._update_workshop_row()

    def _show_about(self, repo_url):
        about = Adw.AboutWindow(transient_for=self)
        about.set_application_name(tr("壁纸选择器"))
        about.set_application_icon("io.github.fitz.WallpaperPicker")
        about.set_version(self._app_version)
        about.set_developer_name("Fitz")
        about.set_website(repo_url)
        about.set_issue_url(repo_url + "/issues")
        about.set_license_type(Gtk.License.GPL_3_0)
        about.set_developers(["Fitz"])
        about.present()
