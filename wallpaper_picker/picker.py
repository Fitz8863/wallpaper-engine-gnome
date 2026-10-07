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
import hashlib
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

# 本文件在包内（wallpaper_picker/），仓库根/安装根是上一级。
# SCRIPT、PICKER、自启 Exec 都指向根目录的文件——源码布局和 deb 布局
# （/usr/lib/wallpaper-engine-gnome/wallpaper_picker/）用 parents[1] 同时成立
ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
HOME = os.path.expanduser("~")
SCRIPT = os.path.join(ROOT, "start-wallpaper.sh")
PICKER = os.path.join(ROOT, "wallpaper-picker.py")
AUTOSTART = f"{HOME}/.config/autostart/wallpaper-engine.desktop"
REPO_URL = "https://github.com/Fitz8863/wallpaper-engine-gnome"
EXT_UUID = "linux-wallpaperengine@github.io"

STATE_DIR = os.environ.get(
    "LWE_STATE_DIR",
    os.path.join(os.environ.get("XDG_CACHE_HOME", f"{HOME}/.cache"),
                 "wallpaper-picker"))
PIDFILE = os.path.join(STATE_DIR, "wallpaper.pid")
SETTINGS_FILE = os.path.join(STATE_DIR, "settings.json")
THUMB_DIR = os.path.join(STATE_DIR, "thumbs")

# 包内模块一律相对 import：绝对名会经由 sys.path 命中另一份模块对象，
# i18n._LANG 等模块级状态就会分裂（表现为托盘/对话框翻译静默失效）
from . import i18n  # noqa: E402
from .i18n import tr  # noqa: E402
from .paths import find_renderer, find_screens, find_workshop, wallpaper_bg_arg  # noqa: E402
from .scan import scan_workshop  # noqa: E402
from .settings_dialog import SettingsDialog  # noqa: E402
from .tray import ICON_NAME, TrayIcon  # noqa: E402
from . import APP_VERSION  # noqa: E402

RENDERER = find_renderer()

TYPE_LABEL = {"scene": "场景", "video": "视频", "web": "网页", "preset": "预设"}
THUMB = 320
PREVIEW_SIZE = 480

# 「帧率上限」对不同类型壁纸的效果完全不同，所以要跟着选中的壁纸说明。
# 依据是官方文档 help.wallpaperengine.io/en/performance/gpu.html：
# 视频有固定帧率，调上限不影响它；场景是实时渲染，上限才真正起作用。
FPS_HINTS = {
    "video": "视频壁纸的帧率由视频文件本身决定，这一项对它无效",
    "scene": "场景壁纸是实时渲染的，调低这一项可以省电",
    "web": "网页壁纸由 CEF 渲染，这一项效果有限",
}
FPS_HINT_DEFAULT = "限制渲染帧率，可省电"

# 渲染器 --volume 的默认值。设成这个值就不必往下传参数。
RENDERER_DEFAULT_VOLUME = 15

DEFAULT_SETTINGS = {
    "silent": False,
    # 音量是逐壁纸的（与官方 Wallpaper Engine 一致）：每张壁纸记住自己的音量，
    # volume_default 只作为没有单独设定时的兜底值。
    "volume_default": RENDERER_DEFAULT_VOLUME,
    "volumes": {},
    "fps": 30,
    "scaling": "default",
    "particles": True,
    "parallax": True,
    "mouse": True,
    "automute": True,
    "properties": {},
    # 应用行为
    "autostart": True,       # 开机自动启动（登录恢复上次的壁纸并常驻托盘）
    "last": None,            # 上次应用的壁纸 ID，登录自启恢复用
    "restore_on_start": False,   # 手动启动时也恢复上次的壁纸
    "close_action": "tray",  # 关闭窗口：tray=隐藏到托盘 / quit=退出
    "language": "system",    # 界面语言：system/zh/en
    "workshop": None,        # 手动选择的壁纸目录；None = 自动探测
    # 多显示器：clone=true 时所有屏用同一张（官方 Clone 模式，接新屏的
    # 默认行为）；clone=false 时按 screens{connector: 壁纸ID} 逐屏，缺失
    # 的屏回落主屏壁纸。音量/属性仍按壁纸 ID（两屏同壁纸共享设置）。
    "clone": True,
    "screens": {},
}

# 值是字典的键，读盘时要单独处理，不能直接覆盖
NESTED_SETTINGS = ("volumes", "properties", "screens")

# 渲染器 --scaling 的合法取值（实测自 "allowed options" 报错信息）
SCALING_CHOICES = [
    ("default", "默认"),
    ("fill", "填充（裁切边缘）"),
    ("fit", "适应（保留黑边）"),
    ("stretch", "拉伸（可能变形）"),
]

# 帧率上限用预设下拉（对齐 Wallpaper Engine 的 Performance 设置），
# 不给自由滑块——桌面背景跑上百帧纯属浪费电。30 是 WE 的默认推荐值；
# 旧版本存过预设之外的自定义值时，会作为"自定义"项出现在列表里。
FPS_PRESETS = [240, 165, 144, 120, 90, 60, 45, 30, 25, 20, 15, 10, 8, 5, 1]

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
    background-color: alpha(currentColor, 0.05);
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
    for key in NESTED_SETTINGS:
        data[key] = {}          # 别和 DEFAULT_SETTINGS 共享同一个字典对象
    if not os.path.exists(SETTINGS_FILE):
        return data             # 全新安装：没有任何历史偏好可迁移
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as fh:
            disk = json.load(fh)
    except Exception:
        return data

    # 旧版本迁移：autostart 键按自启文件是否存在推断——老版本没有这个开关，
    # 手动删过自启文件的用户视为关闭，不能擅自恢复；last 从旧版自启文件的
    # Exec（start-wallpaper.sh <id>）里提取。
    if "autostart" not in disk:
        data["autostart"] = os.path.exists(AUTOSTART)
    if "last" not in disk and os.path.exists(AUTOSTART):
        try:
            with open(AUTOSTART, encoding="utf-8") as fh:
                m = re.search(r"start-wallpaper\.sh\s+(\d+)", fh.read())
            if m:
                data["last"] = m.group(1)
        except OSError:
            pass

    # 旧版本只有一个全局 volume，迁移成逐壁纸的兜底值
    if "volume" in disk and "volume_default" not in disk:
        try:
            data["volume_default"] = int(disk["volume"])
        except (TypeError, ValueError):
            pass

    for key, value in disk.items():
        if key in NESTED_SETTINGS:
            if isinstance(value, dict):
                data[key] = value
        elif key in data:
            data[key] = value
    return data


def wallpaper_volume(settings, wid):
    """取某张壁纸的音量：单独设过就用它，没设过用兜底值。"""
    volumes = settings.get("volumes") or {}
    if wid in volumes:
        return volumes[wid]
    return settings.get("volume_default", RENDERER_DEFAULT_VOLUME)


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


# ------------------------------------------------------------- 缩略图缓存
#
# 三层结构，逐层兜底：
#   THUMB_CACHE   内存里的 Gdk.Texture，网格重建时零开销复用
#   thumbs/ 目录  解码好的 JPEG，每天第一次启动不用重新解码全部预览图
#   现场解码      两层都没有才走 load_thumbnail
# 解码只在工作线程做，Texture 必须回主线程创建（GDK 对象不跨线程）。

_THUMB_CACHE = {}      # (preview路径, 尺寸) -> Gdk.Texture
_THUMB_LOCK = threading.Lock()
_THUMB_PENDING = {}    # 同一张图的并发请求合并成一次解码


def _thumb_cache_file(path, size):
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = 0
    # mtime 进 key：Steam 更新了壁纸预览图，旧缓存自动失效
    key = hashlib.sha1(
        f"{path}|{mtime}|{size}".encode("utf-8", "replace")).hexdigest()[:16]
    return os.path.join(THUMB_DIR, f"{key}.jpg")


def load_thumbnail_cached(path, size):
    """带磁盘缓存的解码，慢，只该在工作线程里调。"""
    cache_file = _thumb_cache_file(path, size)
    try:
        return GdkPixbuf.Pixbuf.new_from_file(cache_file)
    except Exception:
        pass
    pixbuf = load_thumbnail(path, size)
    try:
        os.makedirs(THUMB_DIR, exist_ok=True)
        # load_thumbnail 已把 alpha 合成到深色底上，存 JPEG 没问题
        pixbuf.savev(cache_file, "jpeg", ["quality"], ["85"])
    except Exception:
        pass    # 写不进去就退化为每次现场解码，不影响功能
    return pixbuf


def get_texture_async(path, size, callback):
    """异步取缩略图，完成后在主线程回调 callback(texture)。

    解码失败时回调 callback(None)，调用方自己兜底。
    """
    if not path:
        return
    key = (path, size)
    texture = _THUMB_CACHE.get(key)
    if texture is not None:
        callback(texture)
        return
    with _THUMB_LOCK:
        _THUMB_PENDING.setdefault(key, []).append(callback)
        first = len(_THUMB_PENDING[key]) == 1
    if not first:
        return      # 已经有同一个 key 的解码在跑了，等它完成统一回调

    def worker():
        try:
            pixbuf = load_thumbnail_cached(path, size)
        except Exception:
            pixbuf = None
        GLib.idle_add(_finish_thumbnail, key, pixbuf)

    threading.Thread(target=worker, daemon=True).start()


def _finish_thumbnail(key, pixbuf):
    with _THUMB_LOCK:
        callbacks = _THUMB_PENDING.pop(key, [])
    if pixbuf is not None:
        try:
            _THUMB_CACHE[key] = Gdk.Texture.new_for_pixbuf(pixbuf)
        except Exception:
            pass
    texture = _THUMB_CACHE.get(key)
    for callback in callbacks:
        callback(texture)
    return False


def scan_wallpapers():
    workshop = find_workshop()
    if workshop is None:
        return []
    found = []
    for wtype, wid, title in scan_workshop(workshop):
        path = os.path.join(workshop, wid)
        preview = ""
        for name in ("preview.jpg", "preview.png", "preview.gif"):
            if os.path.exists(os.path.join(path, name)):
                preview = os.path.join(path, name)
                break
        found.append(Wallpaper(wid, wtype, title, preview, path))
    return found


# ------------------------------------------------------------- 分辨率探测
#
# 显示壁纸素材的原始分辨率（对齐 Wallpaper Engine 的详情参数）：
#   视频 = 视频文件本身（GStreamer Discoverer 探测，失败退 ffprobe）
#   场景 = 包内最大 .tex 纹理头部声明的原始尺寸（PKGV 格式不用解码像素）
#   网页 = 自适应，无固定分辨率

_RES_CACHE = {}


def _pkg_largest_tex_dims(pkg_path):
    """从 WE 的 PKGV 包里找出最大 .tex 纹理的原始尺寸。

    包结构（与渲染器 PackageParser 一致）：sizedString "PKGV..." 头 +
    u32 文件数 + 每文件 (sizedString 名字, u32 偏移, u32 长度)。
    .tex 头部（TextureParser）：9B "TEXV0005\\0" + 9B "TEXI0001\\0" +
    format/flags/textureWidth/textureHeight/width/height 六个 u32，
    其中 width/height 是真实图像尺寸（texture 系列是对齐后的尺寸）。
    """
    with open(pkg_path, "rb") as fh:
        def sized():
            n = int.from_bytes(fh.read(4), "little")
            return fh.read(n)
        if not sized().startswith(b"PKGV"):
            return None
        count = int.from_bytes(fh.read(4), "little")
        if count > 200000:
            return None
        best = None
        for _ in range(count):
            name = sized().decode("utf-8", "replace")
            off = int.from_bytes(fh.read(4), "little")
            ln = int.from_bytes(fh.read(4), "little")
            if name.lower().endswith(".tex") and (best is None or ln > best[1]):
                best = (off, ln)
        base = fh.tell()

    if best is None:
        return None
    with open(pkg_path, "rb") as fh:
        fh.seek(base + best[0])
        head = fh.read(42)
    if len(head) < 42 or not head.startswith(b"TEXV0005"):
        return None
    width = int.from_bytes(head[34:38], "little")
    height = int.from_bytes(head[38:42], "little")
    if 0 < width < 100000 and 0 < height < 100000:
        return width, height
    return None


def _video_dims(path):
    try:
        gi.require_version("Gst", "1.0")
        gi.require_version("GstPbutils", "1.0")
        from gi.repository import Gst, GstPbutils
        if not Gst.is_initialized():
            Gst.init(None)
        discoverer = GstPbutils.Discoverer.new(10 * Gst.SECOND)
        info = discoverer.discover_uri(Gst.filename_to_uri(path))
        for stream in info.get_video_streams():
            return stream.get_width(), stream.get_height()
    except Exception:
        pass
    # GStreamer 绑定缺失时退回 ffprobe（装了 ffmpeg 才有）
    try:
        import shutil
        if not shutil.which("ffprobe"):
            return None
        out = subprocess.run(
            ["ffprobe", "-v", "quiet", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=10).stdout.strip()
        width, height = out.split(",")
        return int(width), int(height)
    except Exception:
        return None


def wallpaper_resolution(wall):
    """返回 (宽, 高) 或 None。按 wid 缓存，reload 时清空。"""
    if wall.wid in _RES_CACHE:
        return _RES_CACHE[wall.wid]
    dims = None
    if wall.wtype == "video":
        vids = [f for f in glob.glob(os.path.join(wall.dir, "*"))
                if f.lower().endswith((".mp4", ".webm", ".mkv", ".avi", ".mov"))]
        if vids:
            dims = _video_dims(max(vids, key=os.path.getsize))
    elif wall.wtype == "scene":
        pkg = os.path.join(wall.dir, "scene.pkg")
        if os.path.exists(pkg):
            dims = _pkg_largest_tex_dims(pkg)
        else:
            # 散装场景：目录里最大的非 preview 图片就是主素材
            imgs = [f for f in glob.glob(os.path.join(wall.dir, "*"))
                    if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp",
                                           ".bmp", ".gif"))
                    and not os.path.basename(f).lower().startswith("preview")]
            if imgs:
                info = GdkPixbuf.Pixbuf.get_file_info(max(imgs, key=os.path.getsize))
                if info and info[1] and info[2]:
                    dims = (info[1], info[2])
    _RES_CACHE[wall.wid] = dims
    return dims


def running_wallpaper_ids():
    """解析运行中渲染器的多屏映射 {connector: wid}（wid 归一为目录名）。

    cmdline 里 --screen-root 与 --bg 成对出现，顺序即 launch 计划顺序
    （主屏在最前）。进程不在跑或参数异常时返回 {}，调用方按无壁纸处理。
    """
    result = {}
    try:
        pid = open(PIDFILE).read().strip()
        args = [a.decode() for a in
                open(f"/proc/{pid}/cmdline", "rb").read().split(b"\0") if a]
        i = 0
        while i < len(args) - 2:
            if args[i] == "--screen-root":
                conn = args[i + 1]
                if args[i + 2] == "--bg" and i + 3 < len(args):
                    result[conn] = os.path.basename(args[i + 3])
                    i += 4
                    continue
            i += 1
    except Exception:
        pass
    return result


def running_wallpaper_id():
    """主屏（第一块屏）当前运行的壁纸；无渲染器时返回 None。"""
    return next(iter(running_wallpaper_ids().values()), None)


def extension_loaded():
    try:
        return subprocess.run(["gnome-extensions", "info", EXT_UUID],
                              capture_output=True, text=True,
                              timeout=3).returncode == 0
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
    """跑一次渲染器读出该壁纸的可调属性（耗时，需放到后台线程）。

    正常时返回属性列表（可能为空）；超时或进程异常返回 None，
    None 不进缓存，下次选中还会重试。
    """
    if not os.access(RENDERER, os.X_OK):
        return []
    try:
        out = subprocess.run(
            [RENDERER, "--bg", wallpaper_bg_arg(wid), "--list-properties"],
            capture_output=True, text=True, timeout=60)
    except Exception:
        return None
    return parse_properties(out.stdout)


# ------------------------------------------------------------------ 界面

class AspectPicture(Gtk.Picture):
    """固定宽高比的 Picture（16:9 网格格子）。

    CSS 的 aspect-ratio 对 Gtk.Picture 不生效——它覆写了测量函数，只按
    paintable 自己的尺寸算。这里覆写 do_measure 让高度跟随分配到的宽度
    按比例伸缩，缩略图才能以矩形铺满格子。
    """
    __gtype_name__ = "LWPEAspectPicture"
    RATIO = 16 / 9

    def __init__(self, **kwargs):
        super().__init__(can_shrink=True, **kwargs)

    def do_measure(self, orientation, for_size):
        if orientation == Gtk.Orientation.VERTICAL:
            # 高度是硬约束（最小值=自然值）：FlowBox 按最小尺寸定行高，
            # 返回 0 的话图片会被压成细条
            natural = int(for_size / self.RATIO) if for_size > 0 \
                else int(THUMB * 9 / 16)
            return natural, natural, -1, -1
        natural = int(for_size * self.RATIO) if for_size > 0 else THUMB
        return 0, natural, -1, -1


class WallpaperPicker(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title=tr("壁纸"), default_width=1280,
                         default_height=820)
        self.settings = load_settings()
        self.wallpapers = []
        self.current_screens = {}   # {connector: wid} 运行中的多屏映射
        self.current_id = None      # 主屏那张（兼容单屏逻辑）
        self.selected = None
        self.cards = {}
        self.prop_rows = []
        self.switching = False
        self._reapply_source = None
        self._props_token = 0
        self._preview_token = 0
        self._props_cache = {}     # wid -> 属性列表（含空列表），重扫时清空
        self._save_source = None
        self.preselect = None      # --select 参数，reload 扫描完成后消化
        self._res_token = 0
        self._loading_volume = False   # 回填音量滑块时抑制回调，免得误保存

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

        # 窗口先显示再填数据：扫描、建卡片都挪到 idle 里，
        # present() 之前的阻塞从几百毫秒降到几乎为零
        self.status.set_text(tr("正在扫描壁纸…"))
        GLib.idle_add(self.reload)

        self.tray = None
        self._tray_hint_shown = False
        self._settings_dialog = None
        self.connect("close-request", self._on_close_request)

    def init_tray(self):
        """顶栏托盘图标（StatusNotifierItem）。创建即注册，不阻塞。"""
        self.tray = TrayIcon(
            on_open=lambda: self._tray_open(),
            on_stop=self.stop_wallpaper,
            on_quit=lambda: self.get_application().quit())
        self.tray.start()

    def _tray_open(self):
        self.present()

    def _on_close_request(self, *args):
        # 托盘可用且设置了"隐藏到托盘"时，点 ✕ 是隐藏而不是退出
        if (self.settings.get("close_action", "tray") == "tray"
                and self.tray is not None and self.tray.available):
            self.hide()
            if not self._tray_hint_shown:
                self._tray_hint_shown = True
                try:
                    app = self.get_application()
                    note = Gio.Notification.new(tr("壁纸选择器已最小化到托盘"))
                    note.set_body(tr("点击顶栏图标可以随时打开或停止动态壁纸"))
                    note.set_icon(Gio.ThemedIcon.new(ICON_NAME))
                    app.send_notification("tray-hint", note)
                except Exception:
                    pass
            return True
        return False

    # ---------------------------------------------------------- 主区域

    def build_content(self):
        view = Adw.ToolbarView()
        header = Adw.HeaderBar()

        self.search = Gtk.SearchEntry(placeholder_text=tr("搜索壁纸…"),
                                      width_chars=24)
        self.search.connect("search-changed", lambda *_: self.populate())
        header.set_title_widget(self.search)

        refresh = Gtk.Button(icon_name="view-refresh-symbolic",
                             tooltip_text=tr("重新扫描壁纸"))
        refresh.connect("clicked", lambda *_: self.reload())
        header.pack_start(refresh)

        self.stop_btn = Gtk.Button(label=tr("停止"), css_classes=["destructive-action"])
        self.stop_btn.connect("clicked", self.on_stop)
        header.pack_end(self.stop_btn)

        self.panel_btn = Gtk.ToggleButton(icon_name="sidebar-show-symbolic",
                                          tooltip_text=tr("显示/隐藏属性面板"))
        self.panel_btn.connect("toggled",
                               lambda b: self.split.set_show_sidebar(b.get_active()))
        header.pack_end(self.panel_btn)

        # 切换壁纸需要一两秒（要停掉旧渲染器再起新的），期间转个圈给个交代
        self.spinner = Gtk.Spinner(tooltip_text=tr("正在切换壁纸…"))
        self.spinner.set_visible(False)
        header.pack_end(self.spinner)

        self.settings_btn = Gtk.Button(icon_name="emblem-system-symbolic",
                                       tooltip_text=tr("设置"))
        self.settings_btn.connect("clicked", self.on_settings_clicked)
        header.pack_end(self.settings_btn)
        view.add_top_bar(header)

        # 扩展检查是个子进程调用，放后台线程，别拖慢窗口出现；
        # 确认缺了才亮出提示，避免所有用户都看到横幅闪一下
        self.warn = Adw.Banner(
            title=tr("GNOME 扩展还没被加载：请注销后重新登录一次，动态壁纸才会显示到桌面上"),
            revealed=False)
        self.warn.set_button_label(tr("知道了"))
        view.add_top_bar(self.warn)
        threading.Thread(target=self._check_extension, daemon=True).start()

        self.chips = Gtk.Box(spacing=6, margin_top=10, margin_bottom=4,
                             margin_start=14, margin_end=14)
        group = None
        for label, key in ((tr("全部"), ""), (tr("场景"), "scene"),
                           (tr("视频"), "video"), (tr("网页"), "web")):
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

    def on_settings_clicked(self, _btn):
        if self._settings_dialog is None:
            self._settings_dialog = SettingsDialog(self, APP_VERSION, REPO_URL)
        self._settings_dialog.present()

    def _check_extension(self):
        """后台线程里查扩展状态，回主线程再动横幅。"""
        ok = extension_loaded()
        GLib.idle_add(self.warn.set_revealed, not ok)

    # ---------------------------------------------------------- 属性面板

    def build_sidebar(self):
        view = Adw.ToolbarView()
        header = Adw.HeaderBar()

        self.sidebar_title = Adw.WindowTitle(title=tr("未选择壁纸"))
        header.set_title_widget(self.sidebar_title)

        apply_btn = Gtk.Button(label=tr("应用"), css_classes=["suggested-action"])
        apply_btn.connect("clicked", lambda *_: self.apply(self.selected))
        self.apply_btn = apply_btn
        header.pack_end(apply_btn)
        view.add_top_bar(header)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14,
                      margin_top=14, margin_bottom=18,
                      margin_start=14, margin_end=14)

        self.preview = Gtk.Picture(can_shrink=True, height_request=170,
                                   css_classes=["preview-frame"])
        self.preview.set_content_fit(Gtk.ContentFit.CONTAIN)
        box.append(self.preview)

        self.subtitle = Gtk.Label(xalign=0, wrap=True, css_classes=["card-badge"])
        box.append(self.subtitle)

        box.append(Gtk.Separator())

        # ---- 壁纸属性（逐壁纸，放最前面：这才是属性面板的主角）----
        self.props_header = self.section_title(tr("壁纸属性"))
        box.append(self.props_header)
        self.props_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.append(self.props_box)
        self.props_hint = Gtk.Label(
            label=tr("选中壁纸后这里会列出它自己的可调项"),
            xalign=0, wrap=True, css_classes=["empty-hint", "card-badge"])
        self.props_box.append(self.props_hint)

        box.append(Gtk.Separator())

        # ---- 本壁纸设置（我们自己加的逐壁纸项，目前只有音量）----
        # 音量放在这里而不是「播放设置」里，是因为它逐壁纸生效——
        # 位置要跟语义一致，否则用户会以为调一次就全局生效了。
        own_group = Adw.PreferencesGroup(title=tr("本壁纸设置"))
        box.append(own_group)
        self.row_volume = self.slider_row(
            tr("音量"), 0, 100, 1, RENDERER_DEFAULT_VOLUME,
            self.on_volume_changed, suffix="%")
        self.row_volume.set_sensitive(False)   # 没选中壁纸时不知道音量存给谁
        own_group.add(self.row_volume)

        # ---- 应用到显示器（多屏；单屏时只有一项，无感）----
        # 选择在点「应用」时生效：克隆=全部屏同一张；具体 connector=只换
        # 该屏（其余屏按设置保留；带（主）标记的就是主屏）。
        self._scope_keys, scope_choices = ["clone"], [
            ("clone", tr("所有屏（克隆）"))]
        for conn, primary in find_screens():
            self._scope_keys.append(conn)
            scope_choices.append(
                (conn, tr("{}（主）").format(conn) if primary else conn))
        self.screen_scope = self.combo_row(
            tr("应用到显示器"), scope_choices, self._scope_keys[0],
            lambda _v: None)
        if not self.settings.get("clone", True):
            # 逐屏模式初始停在主屏的 connector（列表主屏在最前），
            # 暗示这次应用只会覆盖主屏
            self.screen_scope._dropdown.set_selected(1)
        own_group.add(self.screen_scope)
        # ---- 播放设置（全局，收进折叠分组省空间）----
        group = Adw.PreferencesGroup(margin_top=4)
        self.settings_group = group
        box.append(group)

        self.settings_expander = Adw.ExpanderRow(
            title=tr("播放设置"), subtitle=tr("全局生效，对所有壁纸都一样"))
        group.add(self.settings_expander)

        self.row_silent = self.switch_row(tr("静音"), tr("关闭壁纸产生的所有声音"),
                                          self.settings["silent"],
                                          lambda v: self.set_global("silent", v))
        self.settings_expander.add_row(self.row_silent)

        self.row_fps = self.combo_row(
            tr("帧率上限"), self._fps_choices(), self.settings["fps"],
            lambda v: self.set_global("fps", int(v)))
        self.settings_expander.add_row(self.row_fps)
        self.update_fps_hint()   # 先给个通用说明，选中壁纸后会换成针对性的

        self.row_scaling = self.combo_row(
            tr("缩放模式"),
            [(key, tr(label)) for key, label in SCALING_CHOICES],
            self.settings["scaling"],
            lambda v: self.set_global("scaling", v))
        self.settings_expander.add_row(self.row_scaling)

        # 对应官方 Performance > Playback 的
        # 「Other application playing audio」从 Mute 改成 Keep running
        self.row_automute = self.switch_row(
            tr("其他程序出声时自动静音"),
            tr("关掉后，你听音乐或看视频时壁纸不会自动静音"),
            self.settings.get("automute", True),
            lambda v: self.set_global("automute", v))
        self.settings_expander.add_row(self.row_automute)

        self.row_particles = self.switch_row(
            tr("粒子效果"), tr("关闭可降低 GPU 占用"),
            self.settings["particles"],
            lambda v: self.set_global("particles", v))
        self.settings_expander.add_row(self.row_particles)

        self.row_parallax = self.switch_row(
            tr("视差效果"), tr("跟随鼠标的景深位移"),
            self.settings["parallax"],
            lambda v: self.set_global("parallax", v))
        self.settings_expander.add_row(self.row_parallax)

        self.row_mouse = self.switch_row(
            tr("鼠标交互"), tr("允许壁纸响应鼠标位置"),
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
        row._dropdown = dropdown      # 供回填/读取（模式同 slider_row）
        row._keys = keys
        return row

    def _fps_choices(self):
        """帧率下拉选项：预设值 + 旧版可能存过的自定义值。"""
        values = list(FPS_PRESETS)
        if self.settings["fps"] not in values:
            values.append(self.settings["fps"])
        values.sort(reverse=True)
        return [(v, f"{v} fps" + (tr("（默认）") if v == 30 else "")) for v in values]

    # ---------------------------------------------------------- 数据刷新

    def reload(self):
        # 重扫 = 工坊内容可能变了，两层解码结果与分辨率缓存都不再可信
        _THUMB_CACHE.clear()
        self._props_cache.clear()
        _RES_CACHE.clear()
        self.wallpapers = scan_wallpapers()
        self.current_screens = running_wallpaper_ids()
        self.current_id = running_wallpaper_id()   # 主屏那张（兼容旧逻辑）
        self.populate()
        self.update_status()
        # 启动时自动选中：优先命令行的 --select，其次正在运行的壁纸
        if self.selected is None:
            want = self.preselect or self.current_id
            match = next((w for w in self.wallpapers
                          if w.wid == want), None) if want else None
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
        就得重新往下翻。所以这里原地改样式。多屏时任一屏在用的壁纸都算
        「使用中」。
        """
        in_use = set(self.current_screens.values())
        for wid, widgets in self.cards.items():
            is_current = wid in in_use
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
        used_on = [c for c, w in self.current_screens.items() if w == wid]
        prefix = tr("使用中 · ") if used_on else ""
        if len(used_on) > 1:
            prefix = tr("使用中 · {} 块屏 · ").format(len(used_on))
        return f"{prefix}{tr(TYPE_LABEL.get(wall.wtype, wall.wtype))}"

    def make_card(self, wall):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                      css_classes=["wallpaper-card"])
        current = wall.wid == self.current_id
        if current:
            box.add_css_class("wallpaper-card-current")

        picture = AspectPicture(css_classes=["thumb"])
        # 解码在后台线程做；回填时卡片可能已被搜索/筛选重建掉，回调里要确认
        if wall.preview:
            get_texture_async(wall.preview, THUMB,
                              lambda tex, w=wall: self._set_card_thumb(w, tex))
        # 铺满 16:9 格子（比例不同的壁纸裁边，如 16:10 裁上下）
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
        button.set_tooltip_text(tr("点击应用：{}").format(wall.title))
        button.connect("clicked", lambda *_: self.select(wall, apply_now=True))

        child = Gtk.FlowBoxChild()
        child.set_child(button)

        # 记下需要在高亮更新时改动的控件，避免重建整个网格
        self.cards[wall.wid] = {"child": child, "box": box, "badge": badge,
                                "picture": picture}
        badge.set_text(self.badge_text(wall.wid))
        return child

    def _set_card_thumb(self, wall, texture):
        """异步解码完成后回填卡片缩略图。"""
        card = self.cards.get(wall.wid)
        if card is None or texture is None:
            return
        card["picture"].set_paintable(texture)

    def select(self, wall, apply_now):
        if wall is None:
            return
        self.selected = wall
        self.split.set_show_sidebar(True)
        self.panel_btn.set_active(True)
        self.sidebar_title.set_title(wall.title)
        self._show_resolution(wall)
        self.preview.set_paintable(None)
        self._preview_token += 1
        token = self._preview_token
        if wall.preview:
            get_texture_async(wall.preview, PREVIEW_SIZE,
                              lambda tex, t=token: self._set_preview(tex, t))
        if wall.wtype == "preset":
            # 预设包：对依赖壁纸的参数配置，渲染器不支持，别让它走到
            # apply（只会得到含混的"启动失败：见日志"）
            self.apply_btn.set_sensitive(False)
            self.apply_btn.set_label(tr("应用"))
            self.subtitle.set_text(
                " · ".join([tr("预设"), wall.wid]))
            self.props_box_children_reset(
                tr("这是预设包壁纸（参数配置），需要它依赖的壁纸引擎，"
                   "当前渲染器暂不支持。请直接使用它所依赖的那张壁纸。"))
            self.update_volume_sensitivity_preset()
            return
        self.apply_btn.set_label(
            tr("使用中") if wall.wid in self.current_screens.values()
            else tr("应用"))
        if not self.switching:
            self.apply_btn.set_sensitive(
                wall.wid not in self.current_screens.values())
        self.load_volume(wall.wid)
        self.update_volume_sensitivity()
        self.update_fps_hint(wall.wtype)
        self.load_properties(wall)
        if apply_now:
            self.apply(wall)

    def props_box_children_reset(self, text):
        while (child := self.props_box.get_first_child()) is not None:
            self.props_box.remove(child)
        self.props_box.append(Gtk.Label(
            label=text, xalign=0, wrap=True,
            css_classes=["empty-hint", "card-badge"]))
        self.prop_rows = []
        self.props_header.set_text(tr("壁纸属性"))

    def update_volume_sensitivity_preset(self):
        """预设包不能应用，音量行同步禁用。"""
        self.row_volume.set_sensitive(False)
        self.row_volume.set_subtitle("")
        self._loading_volume = True
        try:
            self.row_volume._scale.set_value(0)
        finally:
            self._loading_volume = False

    def load_volume(self, wid):
        """把该壁纸的音量回填到滑块上。

        回填会触发 value-changed，必须先用标志位挡住回调，
        否则会把兜底值当成"用户设定"写进这张壁纸的记录里。
        """
        self._loading_volume = True
        try:
            self.row_volume._scale.set_value(wallpaper_volume(self.settings, wid))
        finally:
            self._loading_volume = False

    def _set_preview(self, texture, token):
        """异步解码完成后回填侧栏大预览；快速连点时旧请求作废。"""
        if token != self._preview_token or texture is None:
            return
        self.preview.set_paintable(texture)

    def _show_resolution(self, wall):
        """侧栏副标题带上素材原始分辨率。

        首次探测视频要走 GStreamer 初始化（约几百毫秒），放后台线程，
        完成后回主线程补上；快速连点用 token 防串台。探测期间先显示
        不带分辨率的版本，免得副标题停留在上一张壁纸的文字上。
        """
        def text(res):
            parts = [tr(TYPE_LABEL.get(wall.wtype, wall.wtype)), wall.wid]
            if res:
                parts.append(f"{res[0]}×{res[1]}")
            self.subtitle.set_text(" · ".join(parts))

        if wall.wid in _RES_CACHE:
            text(_RES_CACHE[wall.wid])
            return
        text(None)
        self._res_token += 1
        token = self._res_token

        def worker():
            res = wallpaper_resolution(wall)
            GLib.idle_add(self._set_resolution_text, wall, res, token)

        threading.Thread(target=worker, daemon=True).start()

    def _set_resolution_text(self, wall, res, token):
        if token != self._res_token or self.selected is not wall:
            return False
        parts = [tr(TYPE_LABEL.get(wall.wtype, wall.wtype)), wall.wid]
        if res:
            parts.append(f"{res[0]}×{res[1]}")
        self.subtitle.set_text(" · ".join(parts))
        return False

    # ---------------------------------------------------------- 壁纸属性

    def load_properties(self, wall):
        """读属性列表并渲染面板。缓存命中直接渲染，否则后台线程读。

        网页类壁纸的 --list-properties 要拉起 CEF，耗时数秒——不缓存的话
        每次点同一张卡片都得等一轮。
        """
        self._props_token += 1
        token = self._props_token

        cached = self._props_cache.get(wall.wid)
        if cached is not None:
            self.render_properties(wall.wid, cached, token)
            return

        while (child := self.props_box.get_first_child()) is not None:
            self.props_box.remove(child)
        self.props_box.append(Gtk.Label(
            label=tr("正在读取该壁纸的可调项…"), xalign=0,
            css_classes=["empty-hint", "card-badge"]))
        self.prop_rows = []

        def worker():
            props = fetch_properties(wall.wid)
            if props is not None:      # None = 读取失败，不缓存，可重试
                self._props_cache[wall.wid] = props
            GLib.idle_add(self.render_properties, wall.wid, props, token)

        threading.Thread(target=worker, daemon=True).start()

    def render_properties(self, wid, props, token):
        if token != self._props_token:
            return False
        while (child := self.props_box.get_first_child()) is not None:
            self.props_box.remove(child)

        if props is None:
            self.props_box.append(Gtk.Label(
                label=tr("属性读取失败（可能超时），重新选中这张壁纸可重试"),
                xalign=0, wrap=True, css_classes=["empty-hint", "card-badge"]))
            self.props_header.set_text(tr("壁纸属性"))
            return False
        if not props:
            self.props_box.append(Gtk.Label(
                label=tr("这张壁纸没有可调项"), xalign=0,
                css_classes=["empty-hint", "card-badge"]))
            self.props_header.set_text(tr("壁纸属性"))
            return False

        saved = self.settings["properties"].get(wid, {})

        for spec in props:
            widget = None
            try:
                widget = self._build_property_widget(
                    spec, saved.get(spec["name"], spec["value"]))
            except Exception as exc:
                # 作者侧数据可能退化（实测有 Step=0 的滑块），单个属性
                # 生成失败只跳过它自己，别拖垮整个面板
                print(f"属性控件生成失败({spec['name']}): {exc}")

            if widget is not None:
                self.props_box.append(widget)
                self.prop_rows.append(widget)

        self.props_header.set_text(
            tr("壁纸属性（{} 项）").format(len(self.prop_rows)))
        return False

    def _build_property_widget(self, spec, value):
        """按属性类型生成控件。参数异常时抛错，由调用方按属性隔离。"""
        name = spec["name"]
        if spec["type"] == "boolean":
            widget = Adw.SwitchRow(
                title=spec["label"],
                active=str(value).strip() not in ("0", "false", "False", ""))
            widget.connect(
                "notify::active",
                lambda r, _p, n=name: self.set_property(
                    n, "1" if r.get_active() else "0"))
            return widget
        if spec["type"] == "slider":
            try:
                lo = float(spec["min"] or 0)
                hi = float(spec["max"] or 1)
                step = float(spec["step"] or 0.01)
            except ValueError:
                lo, hi, step = 0.0, 1.0, 0.01
            # "0" 是非空字符串，上面的 or 兜底拦不住；Step=0 会让 GTK
            # 断言失败、控件构造返回 NULL，min>=max 同样非法
            if hi <= lo:
                lo, hi = 0.0, 1.0
            if step <= 0:
                step = (hi - lo) / 100.0
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
            row = Adw.ActionRow(title=spec["label"])
            row.add_suffix(scale)
            row.add_suffix(readout)
            return row
        if spec["type"] == "combo" and spec["options"]:
            values = [opt[0] for opt in spec["options"]]
            # 选项文本同样可能是作者写的 HTML（"<p>椎名真白<br>…"），剥成
            # 纯文本；剥不出结果的用原文兜底
            labels = [clean_label(opt[1], opt[1]) for opt in spec["options"]]
            dropdown = Gtk.DropDown.new_from_strings(labels)
            if str(value) in values:
                dropdown.set_selected(values.index(str(value)))
            dropdown.set_valign(Gtk.Align.CENTER)
            dropdown.connect(
                "notify::selected",
                lambda dd, _p, n=name, vals=values:
                    self.set_property(n, vals[dd.get_selected()]))
            row = Adw.ActionRow(title=spec["label"])
            row.add_suffix(dropdown)
            return row
        if spec["type"] == "color":
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
            row = Adw.ActionRow(title=spec["label"])
            row.add_suffix(button)
            return row
        return None

    # ---------------------------------------------------------- 设置变更

    def set_global(self, key, value):
        if self.settings.get(key) == value:
            return
        self.settings[key] = value
        self.schedule_save()
        if key == "silent":
            self.update_volume_sensitivity()
        self.schedule_reapply()

    def on_volume_changed(self, value):
        """音量逐壁纸保存（与官方 Wallpaper Engine 一致）。"""
        if self._loading_volume or self.selected is None:
            return
        wid = self.selected.wid
        value = int(value)
        volumes = self.settings.setdefault("volumes", {})
        if volumes.get(wid) == value:
            return
        volumes[wid] = value
        self.schedule_save()
        self.schedule_reapply(wid)

    def update_volume_sensitivity(self):
        """静音开着时，音量滑块拖了也没用，直接禁掉并说明原因。

        官方 Wallpaper Engine 也是这个逻辑——显示器被静音时逐壁纸的
        Volume 会显示为不可用。
        """
        silent = bool(self.settings.get("silent"))
        self.row_volume.set_sensitive(self.selected is not None and not silent)
        self.row_volume.set_subtitle(
            tr("「静音」已开启，音量不生效") if silent else "")

    def update_fps_hint(self, wtype=None):
        """帧率上限对不同类型壁纸的效果差别很大，副标题跟着选中的壁纸变。

        直接在这一行说明，比在别处写一段文档有效——用户正好在看这一行。
        """
        if wtype is None and self.selected is not None:
            wtype = self.selected.wtype
        self.row_fps.set_subtitle(
            tr(FPS_HINTS.get(wtype or "", FPS_HINT_DEFAULT)))

    def set_property(self, name, value):
        if self.selected is None:
            return
        store = self.settings["properties"].setdefault(self.selected.wid, {})
        if store.get(name) == value:
            return
        store[name] = value
        self.schedule_save()
        self.schedule_reapply(self.selected.wid)

    def schedule_save(self):
        """设置改动合并写盘。

        拖滑块时 value-changed 每个刻度都触发，逐次写盘毫无意义——
        一次拖动就是上百次。400ms 内的改动合并成一次写入。
        定得比 reapply 的 700ms 短，保证渲染器重启前新值已经落盘。
        """
        if self._save_source is not None:
            GLib.source_remove(self._save_source)
        self._save_source = GLib.timeout_add(400, self._flush_save)

    def _flush_save(self, *_args):
        # 直接调用时也可能有挂着的防抖定时器（apply 落盘路径），一并撤掉
        if self._save_source is not None:
            GLib.source_remove(self._save_source)
            self._save_source = None
        try:
            save_settings(self.settings)
        except Exception:
            pass
        return False

    def schedule_reapply(self, wid=None):
        """设置改动后重新应用壁纸。

        连续拖动滑块会触发很多次，所以做个防抖，避免把渲染器反复重启。

        wid 表示改的是哪张壁纸的设置。如果它没在任一块屏上运行，就完全
        不必重启渲染器——早期版本没区分这点，编辑别的壁纸会白白打断
        当前壁纸。多屏下重启是整体的（单进程），套用当前设置意图即可。
        """
        if not self.current_screens:
            return
        if wid is not None and wid not in self.current_screens.values():
            return
        if self._reapply_source is not None:
            GLib.source_remove(self._reapply_source)
        self._reapply_source = GLib.timeout_add(700, self._do_reapply)

    def _do_reapply(self):
        self._reapply_source = None
        # 按设置里的多屏意图整体重启渲染器（不改 last/克隆偏好）
        self.run_script_async(["--apply-plan"], self._after_reapply)
        return False

    def _after_reapply(self, result):
        if result.returncode == 0:
            self.current_screens = running_wallpaper_ids()
            self.current_id = next(iter(self.current_screens.values()), None)
        else:
            detail = (result.stderr or "").strip().splitlines()
            self.toast_overlay.add_toast(Adw.Toast(
                title=tr("启动失败：{}").format(
                    detail[-1][:100] if detail else tr("见日志"))))
        self.update_status()
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
        # 应用目标由侧栏「应用到显示器」决定；意图先写盘——start-wallpaper.sh
        # 的 launch 计划按 settings 拼装全部屏，改副屏不会丢主屏的壁纸
        scope = self.current_scope()
        if scope == "clone":
            self.settings["clone"] = True
            self.settings["screens"] = {}
        elif scope:
            self.settings["clone"] = False
            self.settings.setdefault("screens", {})[scope] = wall.wid
        self.settings["last"] = wall.wid
        self._flush_save()
        self.set_busy(True, tr("正在切换：{} …").format(wall.title))

        def done(result):
            self.set_busy(False)
            if result.returncode != 0:
                detail = (result.stderr or "").strip().splitlines()
                self.toast_overlay.add_toast(Adw.Toast(
                    title=tr("启动失败：{}").format(
                        detail[-1][:100] if detail else tr("见日志"))))
                self.update_status()
                return False
            # 以渲染器进程的真实 cmdline 为准刷新运行态（含多屏映射）
            self.current_screens = running_wallpaper_ids()
            self.current_id = next(iter(self.current_screens.values()), wall.wid)
            if self.settings.get("autostart", True):
                self.write_autostart()
            else:
                self.remove_autostart()
            self.schedule_save()   # 与 shell 的回写合并落盘（幂等）
            self.update_highlight()      # 原地更新，不重建网格（否则滚动条跳顶）
            self.update_status()
            self.apply_btn.set_sensitive(False)
            self.apply_btn.set_label(tr("使用中"))
            if not quiet:
                self.toast_overlay.add_toast(Adw.Toast(
                    title=tr("已应用：{}").format(wall.title)))
            return False

        if scope == "clone":
            args = ["--all-screens", wall.wid]
        elif scope:
            args = ["--screen", scope, wall.wid]
        else:
            args = [wall.wid]   # 兜底:无选择时按主屏语义
        self.run_script_async(args, done)

    def on_stop(self, _btn):
        self.stop_wallpaper()

    def stop_wallpaper(self):
        """停止动态壁纸。「停止」按钮与托盘菜单共用。"""
        self.set_busy(True, tr("正在停止 …"))
        self.run_script_async(["--stop"], self._after_stop)

    def _after_stop(self, result):
        self.set_busy(False)
        if result.returncode != 0:
            detail = (result.stderr or "").strip().splitlines()
            self.toast_overlay.add_toast(Adw.Toast(
                title=tr("停止失败：{}").format(
                    detail[-1][:100] if detail else tr("见日志"))))
            self.update_status()
            return False
        self.current_id = None
        self.remove_autostart()
        self.update_highlight()
        self.update_status()
        self.apply_btn.set_sensitive(True)
        self.apply_btn.set_label(tr("应用"))
        self.toast_overlay.add_toast(Adw.Toast(title=tr("已停止动态壁纸")))
        return False

    def remove_autostart(self):
        """停止即撤掉登录自启——用户明确要停，重启后壁纸不该自己回来。

        下次选壁纸时 write_autostart 会重建。文件不存在不算错误。
        """
        try:
            os.remove(AUTOSTART)
        except OSError:
            pass

    def write_autostart(self):
        """写登录自启：启动选择器本体（托盘常驻），由它延迟恢复上次的壁纸。

        旧版这里只启动渲染器，登录后壁纸虽然有了但托盘/后台服务不存在。
        """
        try:
            os.makedirs(os.path.dirname(AUTOSTART), exist_ok=True)
            with open(AUTOSTART, "w", encoding="utf-8") as fh:
                fh.write(
                    "[Desktop Entry]\n"
                    "Type=Application\n"
                    "Name=Wallpaper Engine 动态壁纸\n"
                    "Comment=壁纸选择器后台服务，登录后恢复上次的壁纸\n"
                    f'Exec=bash -c "sleep 5; {PICKER} --restore"\n'
                    "Icon=io.github.fitz.WallpaperPicker\n"
                    "X-GNOME-Autostart-enabled=true\n"
                    "NoDisplay=false\n"
                    "Terminal=false\n")
        except Exception:
            pass

    def ensure_autostart(self):
        """启动时自愈：开了自启但文件缺失或还是旧格式时，重写成新格式。"""
        if not self.settings.get("autostart", True):
            return
        try:
            content = ""
            if os.path.exists(AUTOSTART):
                with open(AUTOSTART, encoding="utf-8") as fh:
                    content = fh.read()
            if "--restore" in content:
                return
            if self.settings.get("last"):
                self.write_autostart()
        except Exception:
            pass

    def set_autostart(self, enabled):
        """开机自启开关（设置对话框）。"""
        self.settings["autostart"] = enabled
        self.schedule_save()
        if enabled:
            self.write_autostart()
        else:
            self.remove_autostart()

    def set_restore_on_start(self, enabled):
        self.settings["restore_on_start"] = enabled
        self.schedule_save()

    def set_close_action(self, action):
        self.settings["close_action"] = action
        self.schedule_save()

    def set_language(self, lang):
        self.settings["language"] = lang
        i18n.set_language(lang)   # 立即作用于之后创建的控件；完整生效需重启
        self.schedule_save()

    def current_scope(self):
        """侧栏「应用到显示器」的当前选择:clone / primary / connector。

        下拉只在点「应用」时读取，平时切换不触发任何动作。
        """
        idx = self.screen_scope._dropdown.get_selected()
        if 0 <= idx < len(self.screen_scope._keys):
            return self.screen_scope._keys[idx]
        return "clone"

    def set_clone(self, enabled):
        """克隆开关（设置对话框）：开 = 全部屏跟随主屏壁纸。

        关掉时不改变任何屏的当前画面（真正的逐屏差异从侧栏的
        「应用到显示器」开始），开着时切回克隆需要重拼全部屏。
        """
        self.settings["clone"] = bool(enabled)
        if enabled:
            self.settings["screens"] = {}
        self.schedule_save()
        self.schedule_reapply()

    def set_workshop(self, path):
        """壁纸目录手动选择（None = 恢复自动探测）。

        目录变化影响整个清单，保存后必须重新扫描；环境变量 LWE_WORKSHOP
        优先级更高，设了它的时候这里改了也不生效（对话框里有说明）。
        """
        self.settings["workshop"] = path
        self.schedule_save()
        self.reload()

    def restore_last(self):
        """登录/启动恢复：克隆 → 全屏上次的壁纸；逐屏 → 按设置意图拼装。

        settings 里的意图（clone/screens/last）由应用时写入，
        --apply-plan 让 launch 无壁纸参数地按意图重拼全部屏。
        """
        if not self.settings.get("clone", True):
            self.run_script_async(["--apply-plan"], self._after_reapply)
            return False
        last = self.settings.get("last")
        wall = next((w for w in self.wallpapers if w.wid == str(last)), None) \
            if last else None
        if wall is not None:
            self.apply(wall, quiet=True)
        return False

    def update_status(self):
        total = len(self.wallpapers)
        if self.current_screens:
            titles, seen = [], set()
            for wid in self.current_screens.values():
                if wid in seen:
                    continue
                seen.add(wid)
                titles.append(next((w.title for w in self.wallpapers
                                    if w.wid == wid), wid))
            if len(titles) == 1:
                text = tr("正在使用：{}　·　共 {} 张壁纸").format(titles[0], total)
            else:
                text = tr("正在使用：{}（主屏）+ {}　·　共 {} 张壁纸").format(
                    titles[0], " + ".join(titles[1:]), total)
            self.status.set_text(text)
            self.stop_btn.set_sensitive(True)
        else:
            self.status.set_text(
                tr("当前没有动态壁纸在运行　·　共 {} 张壁纸").format(total))
            # 正在切换时别把「停止」重新启用，否则状态文案会被覆盖
            self.stop_btn.set_sensitive(not self.switching)


class PickerApp(Adw.Application):
    def __init__(self, snapshot_path=None, preselect=None, restore=False):
        # 正常运行是单实例（重复启动唤起已运行的窗口，托盘软件的标配）；
        # snapshot 模式保持 NON_UNIQUE，便于应用正在运行时也能出开发截图
        flags = Gio.ApplicationFlags.NON_UNIQUE if snapshot_path \
            else Gio.ApplicationFlags.DEFAULT_FLAGS
        super().__init__(application_id="io.github.fitz.WallpaperPicker",
                         flags=flags)
        self.snapshot_path = snapshot_path
        self.preselect = preselect
        self.restore_mode = restore   # 开机自启路径：不弹窗，只进托盘
        self._activated = False
        # 用信号而不是覆写 do_shutdown：PyGObject 里对 shutdown vfunc
        # 做 chain-up 会报 "failed to chain up" 的 CRITICAL
        self.connect("shutdown", self._on_shutdown)

    def _on_shutdown(self, _app):
        # 写盘防抖意味着退出时可能有改动还没落盘，兜底 flush 一次
        window = self.props.active_window
        if window is not None:
            window._flush_save()

    def do_activate(self):
        window = self.props.active_window or WallpaperPicker(self)
        first = not self._activated
        self._activated = True
        if self.snapshot_path:
            # 开发截图时用更高的画布、并展开折叠区，方便一次看全
            window.set_default_size(1280, 1400)
            window.settings_expander.set_expanded(True)
        elif window.tray is None:
            # 托盘只在正常运行时挂（snapshot 模式挂了也是徒增注册噪音）
            window.init_tray()
        window.ensure_autostart()
        # 预选交给 reload() 消化：此时壁纸清单还没扫描，立刻 select 找不到对象
        window.preselect = self.preselect
        if self.restore_mode and first and not self.snapshot_path:
            # 开机自启：不弹窗口，等会话就绪后恢复上次的壁纸；
            # 之后用户再启动本应用走的是上面的 present 分支
            # （snapshot 是开发调试模式，必须 present 才能出图）
            GLib.timeout_add_seconds(5, window.restore_last)
        else:
            window.present()
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


def run_app(argv):
    """应用入口：解析 --snapshot/--select/--restore 并启动。

    供 cli.main（python -m 与根薄壳）调用；argv 直接改写（与历史
    __main__ 语义一致），Gtk.Application 不使用 argv[0]。
    """
    shot = None
    if "--snapshot" in argv:
        index = argv.index("--snapshot")
        shot = argv[index + 1]
        del argv[index:index + 2]
    select = None
    if "--select" in argv:
        index = argv.index("--select")
        select = argv[index + 1]
        del argv[index:index + 2]
    restore = "--restore" in argv
    if restore:
        argv.remove("--restore")
    i18n.set_language(load_settings().get("language", "system"))
    return PickerApp(shot, select, restore).run(argv)


if __name__ == "__main__":
    sys.exit(run_app(sys.argv))
