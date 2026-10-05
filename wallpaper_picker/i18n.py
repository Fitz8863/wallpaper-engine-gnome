"""界面文案翻译层 —— 以中文原文为 key 的字典方案，无 gettext 工具链。

用法：tr("中文原文") 按当前语言返回文案；en 缺翻译时回退中文原文
（渐进可补，缺一条不影响运行）。语言来自 settings["language"]
（system/zh/en），应用启动时由 picker 调 set_language 注入；
切换语言重启应用后生效。
"""

import os

_LANG = "zh"

# "跟随系统"的实际解析结果
def resolve_system_lang():
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(var, "")
        if value:
            return "zh" if value.lower().startswith("zh") else "en"
    return "zh"


def set_language(lang):
    global _LANG
    if lang == "system":
        _LANG = resolve_system_lang()
    else:
        _LANG = "zh" if lang == "zh" else "en"


def tr(text):
    if _LANG == "zh":
        return text
    return _STRINGS.get(text, {}).get("en", text)


_STRINGS = {
    # ---- 主界面 ----
    "壁纸": {"en": "Wallpapers"},
    "搜索壁纸…": {"en": "Search wallpapers…"},
    "重新扫描壁纸": {"en": "Rescan wallpapers"},
    "停止": {"en": "Stop"},
    "显示/隐藏属性面板": {"en": "Show/hide the properties panel"},
    "正在切换壁纸…": {"en": "Switching wallpaper…"},
    "设置": {"en": "Settings"},
    "GNOME 扩展还没被加载：请注销后重新登录一次，动态壁纸才会显示到桌面上": {
        "en": "The GNOME extension is not loaded yet: log out and back in once, "
              "then the live wallpaper will appear on the desktop"},
    "知道了": {"en": "Got it"},
    "全部": {"en": "All"},
    "场景": {"en": "Scene"},
    "预设": {"en": "Preset"},
    "这是预设包壁纸（参数配置），需要它依赖的壁纸引擎，"
    "当前渲染器暂不支持。请直接使用它所依赖的那张壁纸。": {
        "en": "This is a preset package (a parameter configuration "
              "for its dependency wallpaper). The renderer does not "
              "support these yet — use the wallpaper it depends on "
              "instead."},
    "视频": {"en": "Video"},
    "网页": {"en": "Web"},
    "正在扫描壁纸…": {"en": "Scanning wallpapers…"},
    "壁纸选择器已最小化到托盘": {"en": "Wallpaper Picker minimized to tray"},
    "点击顶栏图标可以随时打开或停止动态壁纸": {
        "en": "Click the top-bar icon to open or stop the live wallpaper anytime"},
    "未选择壁纸": {"en": "No wallpaper selected"},
    "应用": {"en": "Apply"},
    "使用中": {"en": "In use"},
    "使用中 · ": {"en": "In use · "},
    "点击应用：{}": {"en": "Apply: {}"},
    "正在使用：{}　·　共 {} 张壁纸": {"en": "Using: {}　·　{} wallpapers total"},
    "当前没有动态壁纸在运行　·　共 {} 张壁纸": {
        "en": "No live wallpaper running　·　{} wallpapers total"},
    # ---- 侧栏 ----
    "壁纸属性": {"en": "Wallpaper properties"},
    "选中壁纸后这里会列出它自己的可调项": {
        "en": "Select a wallpaper to see its own adjustable options"},
    "本壁纸设置": {"en": "This wallpaper"},
    "音量": {"en": "Volume"},
    "播放设置": {"en": "Playback"},
    "全局生效，对所有壁纸都一样": {"en": "Global, applies to all wallpapers"},
    "静音": {"en": "Mute"},
    "关闭壁纸产生的所有声音": {"en": "Mute all sounds from wallpapers"},
    "帧率上限": {"en": "FPS limit"},
    "（默认）": {"en": " (default)"},
    "缩放模式": {"en": "Scaling"},
    "默认": {"en": "Default"},
    "填充（裁切边缘）": {"en": "Fill (crop edges)"},
    "适应（保留黑边）": {"en": "Fit (keep black bars)"},
    "拉伸（可能变形）": {"en": "Stretch (may distort)"},
    "其他程序出声时自动静音": {"en": "Auto-mute when other apps play audio"},
    "关掉后，你听音乐或看视频时壁纸不会自动静音": {
        "en": "Turn off to keep wallpaper audio while you listen to music "
              "or watch videos"},
    "粒子效果": {"en": "Particles"},
    "关闭可降低 GPU 占用": {"en": "Disable to reduce GPU usage"},
    "视差效果": {"en": "Parallax"},
    "跟随鼠标的景深位移": {"en": "Depth shift following the mouse"},
    "鼠标交互": {"en": "Mouse interaction"},
    "允许壁纸响应鼠标位置": {"en": "Let the wallpaper respond to the mouse"},
    "「静音」已开启，音量不生效": {"en": "Mute is on; volume has no effect"},
    "视频壁纸的帧率由视频文件本身决定，这一项对它无效": {
        "en": "A video's frame rate is decided by the file itself; "
              "this option has no effect on it"},
    "场景壁纸是实时渲染的，调低这一项可以省电": {
        "en": "Scene wallpapers are rendered in real time; "
              "lowering this saves power"},
    "网页壁纸由 CEF 渲染，这一项效果有限": {
        "en": "Web wallpapers are rendered by CEF; this option has limited effect"},
    "限制渲染帧率，可省电": {"en": "Limit the render frame rate to save power"},
    # ---- 属性面板 ----
    "正在读取该壁纸的可调项…": {"en": "Reading this wallpaper's options…"},
    "属性读取失败（可能超时），重新选中这张壁纸可重试": {
        "en": "Failed to read properties (possibly timed out); "
              "select this wallpaper again to retry"},
    "这张壁纸没有可调项": {"en": "This wallpaper has no adjustable options"},
    "壁纸属性（{} 项）": {"en": "Wallpaper properties ({} items)"},
    # ---- 操作反馈 ----
    "正在切换：{} …": {"en": "Switching: {} …"},
    "启动失败：{}": {"en": "Failed to start: {}"},
    "见日志": {"en": "see the log"},
    "已应用：{}": {"en": "Applied: {}"},
    "正在停止 …": {"en": "Stopping…"},
    "停止失败：{}": {"en": "Failed to stop: {}"},
    "已停止动态壁纸": {"en": "Live wallpaper stopped"},
    # ---- 托盘 ----
    "打开壁纸选择器": {"en": "Open Wallpaper Picker"},
    "停止动态壁纸": {"en": "Stop live wallpaper"},
    "退出": {"en": "Quit"},
    "壁纸选择器": {"en": "Wallpaper Picker"},
    "Wallpaper Engine 动态壁纸": {"en": "Wallpaper Engine live wallpapers"},
    # ---- 设置对话框 ----
    "应用行为": {"en": "App behavior"},
    "开机自动启动": {"en": "Start at login"},
    "登录后自动恢复上次的壁纸，并在顶栏常驻托盘图标": {
        "en": "Restore the last wallpaper at login and keep the tray icon running"},
    "启动时恢复壁纸": {"en": "Restore wallpaper on launch"},
    "手动打开软件时也自动应用上次的壁纸": {
        "en": "Apply the last wallpaper when you open the app manually"},
    "关闭窗口时": {"en": "When closing the window"},
    "托盘不可用时始终退出程序": {"en": "Always quit when the tray is unavailable"},
    "隐藏到托盘（推荐）": {"en": "Hide to tray (recommended)"},
    "直接退出": {"en": "Quit"},
    "外观": {"en": "Appearance"},
    "界面语言 / Language": {"en": "Language"},
    "切换后需要重启应用才能生效": {"en": "Restart the app to apply the change"},
    "跟随系统": {"en": "Follow system"},
    "简体中文": {"en": "Simplified Chinese"},
    "关于壁纸选择器": {"en": "About Wallpaper Picker"},
    "版本、许可证与项目链接": {"en": "Version, license and project link"},
    "壁纸来源": {"en": "Wallpaper source"},
    "壁纸目录": {"en": "Wallpaper folder"},
    "浏览…": {"en": "Browse…"},
    "重置": {"en": "Reset"},
    "选择壁纸目录": {"en": "Select wallpaper folder"},
    "自动探测 Steam 创意工坊；第三方下载的壁纸可在此指定目录": {
        "en": "Auto-detect the Steam workshop; point this to a folder "
              "for wallpapers downloaded elsewhere"},
    "环境变量 LWE_WORKSHOP 已设置，优先于此处（当前：{}）": {
        "en": "The LWE_WORKSHOP environment variable is set and takes "
              "priority (currently: {})"},
}
