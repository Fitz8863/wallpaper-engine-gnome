"""wallpaper_picker —— GNOME Wayland 下的 Wallpaper Engine 壁纸选择器。

包结构与依赖方向（全部相对 import，禁止绝对名，防模块对象双份）：

    picker ──▶ tray / settings_dialog / i18n / paths / scan
    tray  ──▶ i18n
    settings_dialog ──▶ i18n
    paths / scan / i18n ──▶ 纯 stdlib（供 start-wallpaper.sh 按路径直调）

本文件必须保持零 import：build-deb.sh 用正则从这里提取 APP_VERSION
（不能 import，否则打包机要背上 gi 依赖），子模块也从这里取版本。
"""

APP_VERSION = "1.3.0"

__all__ = ["APP_VERSION"]
