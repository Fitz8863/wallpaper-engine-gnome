"""设置对话框 —— Adw.PreferencesWindow，主界面顶栏齿轮按钮打开。

三页：应用行为（自启/启动恢复/关闭窗口行为）、语言、关于。
所有改动即时持久化（复用 picker.settings + schedule_save 基建）；
界面语言的切换在重启应用后生效。
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

LANG_KEYS = ["system", "zh", "en"]
LANG_LABELS = ["跟随系统", "简体中文", "English"]
CLOSE_LABELS = ["隐藏到托盘（推荐）", "直接退出"]


class SettingsDialog(Adw.PreferencesWindow):
    def __init__(self, picker, app_version, repo_url, **kwargs):
        super().__init__(**kwargs)
        self._picker = picker
        s = picker.settings
        self.set_transient_for(picker)
        self.set_title("设置")
        self.set_default_size(560, 480)
        self.set_modal(True)

        # ---- 应用行为 ----
        app_page = Adw.PreferencesPage()
        behavior = Adw.PreferencesGroup(title="应用行为")

        row_autostart = Adw.SwitchRow(
            title="开机自动启动",
            subtitle="登录后自动恢复上次的壁纸，并在顶栏常驻托盘图标",
            active=bool(s.get("autostart", True)))
        row_autostart.connect(
            "notify::active",
            lambda r, _p: picker.set_autostart(r.get_active()))
        behavior.add(row_autostart)

        row_restore = Adw.SwitchRow(
            title="启动时恢复壁纸",
            subtitle="手动打开软件时也自动应用上次的壁纸",
            active=bool(s.get("restore_on_start", False)))
        row_restore.connect(
            "notify::active",
            lambda r, _p: picker.set_restore_on_start(r.get_active()))
        behavior.add(row_restore)

        close_model = Gtk.StringList.new(CLOSE_LABELS)
        row_close = Adw.ComboRow(
            title="关闭窗口时",
            subtitle="托盘不可用时始终退出程序",
            model=close_model)
        row_close.set_selected(
            0 if s.get("close_action", "tray") == "tray" else 1)
        row_close.connect(
            "notify::selected",
            lambda r, _p: picker.set_close_action(
                "tray" if r.get_selected() == 0 else "quit"))
        behavior.add(row_close)
        app_page.add(behavior)

        # ---- 语言 ----
        lang_page = Adw.PreferencesPage()
        lang_group = Adw.PreferencesGroup(title="外观")
        row_lang = Adw.ComboRow(
            title="界面语言 / Language",
            subtitle="切换后需要重启应用才能生效",
            model=Gtk.StringList.new(LANG_LABELS))
        current = s.get("language", "system")
        row_lang.set_selected(
            LANG_KEYS.index(current) if current in LANG_KEYS else 0)
        row_lang.connect(
            "notify::selected",
            lambda r, _p: picker.set_language(LANG_KEYS[r.get_selected()]))
        lang_group.add(row_lang)
        lang_page.add(lang_group)

        # ---- 关于 ----
        about_page = Adw.PreferencesPage()
        about_group = Adw.PreferencesGroup()
        about_row = Adw.ActionRow(
            title="关于壁纸选择器",
            subtitle="版本、许可证与项目链接",
            activatable=True)
        about_row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        about_row.connect("activated", lambda _r: self._show_about(repo_url))
        about_group.add(about_row)
        about_page.add(about_group)

        self.add(app_page)
        self.add(lang_page)
        self.add(about_page)

    def _show_about(self, repo_url):
        about = Adw.AboutWindow(transient_for=self)
        about.set_application_name("壁纸选择器")
        about.set_application_icon("io.github.fitz.WallpaperPicker")
        about.set_version(self._picker.APP_VERSION)
        about.set_developer_name("Fitz")
        about.set_website(repo_url)
        about.set_issue_url(repo_url + "/issues")
        about.set_license_type(Gtk.License.GPL_3_0)
        about.set_developers(["Fitz"])
        about.present()
