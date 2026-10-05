# Wallpaper Engine 动态壁纸 · GNOME Wayland 方案

在 GNOME（Wayland 会话）上使用 Wallpaper Engine 创意工坊壁纸的完整方案，包含原生 GTK4 图形化选择器。

> **背景**：Wallpaper Engine 官方只支持 Windows；Linux 上有社区渲染器
> [linux-wallpaperengine](https://github.com/Almamu/linux-wallpaperengine)，
> 但它依赖 `wlr-layer-shell` 协议，而 GNOME 的 Mutter 合成器**不实现该协议**，
> 所以在 GNOME 上直接跑是黑屏。本方案通过一个配套的 GNOME Shell 扩展，
> 用 `Clutter.Clone` 把渲染窗口克隆进桌面背景层，绕开这个限制。

## 功能

- **图形化选择器**：缩略图网格浏览全部已订阅壁纸，支持搜索、按类型（场景/视频/网页）筛选
- **点击即切换**，当前生效的壁纸有高亮标记
- **右侧属性面板**（布局参考 Wallpaper Engine）：
  - **壁纸属性**——自动读取每张壁纸自己的可调项，按类型生成控件（开关 / 滑块 /
    下拉 / 取色器），改动即时生效。不同壁纸的可调项差别很大，从 0 项到几十项都有
  - **本壁纸设置**——音量。**逐壁纸记住**，与官方 Wallpaper Engine 的行为一致
  - **播放设置**——静音、帧率上限、缩放模式、其他程序出声时自动静音、
    粒子/视差/鼠标交互开关，全局生效
- **设置持久化**：写入 `settings.json`，界面和命令行共用同一份配置
- **托盘图标**：运行后常驻顶栏（右上角），点 ✕ 最小化到托盘而不是退出；
  左键唤起主窗口，右键菜单可打开、停止动态壁纸或真正退出
- **登录自启**：切换壁纸时自动记住，下次开机还是它；点「停止」会撤掉自启
- **命令行**：可脚本化调用，支持 ID、名称关键词、播放列表

### 关于"播放速度"

渲染器**没有全局倍速参数**。速度是通过壁纸自带属性实现的——不少场景壁纸带有
`scrollspeed`、`pbrscrollspeed`、`pxbrrollingspeed` 这类属性，选中该壁纸后会在
「壁纸属性」里出现对应的控件。若某张壁纸没有这类属性，则它本身就不支持变速。

同样地，「缩放模式」对应渲染器的 `--scaling`，可选
默认 / 填充（裁切边缘）/ 适应（保留黑边）/ 拉伸。

### 关于"帧率上限"

**这一项对不同类型壁纸的效果完全不同**（依据官方文档
[Performance / GPU](https://help.wallpaperengine.io/en/performance/gpu.html)）：

| 壁纸类型 | 帧率由谁决定 | 调整这一项 |
|---|---|---|
| 视频 | 视频文件本身（24/30/60fps） | **无效**，想省电只能换低帧率视频 |
| 场景 | 引擎实时渲染，没有固有帧率 | **有效**，这是主要的省电手段 |
| 网页 | CEF 渲染 | 效果有限 |

界面上选中壁纸后，这一行的说明会跟着类型变化，避免误以为调了就有用。
实测：场景壁纸把上限从 30 降到 10，整机功耗从 24W 降到 18W（省约 55%）；
同样操作对视频壁纸没有区别。

## 前置条件

开始之前，你需要已经具备这三样：

1. **装了 Steam**——原生 deb 包、Flatpak、Snap 都可以，本方案会自动识别
2. **在 Steam 上买过并安装了 Wallpaper Engine**（appid `431960`）
3. **在创意工坊订阅了至少一张壁纸**——订阅之后 Steam 才会把它下载到本地

这三步缺一不可。**本方案不下载壁纸**，它只负责把已经下载到本地的壁纸渲染成桌面背景。

Wallpaper Engine 本体在 Linux 上通过 Proton 运行，**仅用于浏览和订阅壁纸**——它的
"应用壁纸"功能依赖 Windows 外壳的窗口层级 API，在 Linux 上无法生效。这是原理性限制，
不是配置问题。真正"应用壁纸"由本方案完成。

### 壁纸下载到哪去了

Steam 把创意工坊内容放在 Steam 库目录下：

```
<Steam库>/steamapps/workshop/content/431960/<壁纸ID>/
                                    ├── project.json   ← 标题、类型
                                    └── preview.jpg    ← 预览图
```

其中 `431960` 就是 Wallpaper Engine 的 appid。`<Steam库>` 的位置取决于你的安装方式：

| Steam 安装方式 | 库目录常见位置 |
|---|---|
| Ubuntu 官方 deb（steam-installer） | `~/.steam/debian-installation` |
| 传统 `~/.steam` 布局 | `~/.steam/steam`、`~/.local/share/Steam` |
| Flatpak | `~/.var/app/com.valvesoftware.Steam/.local/share/Steam` |
| Snap | `~/snap/steam/common/.local/share/Steam` |

**如果你把 Steam 库放在了别的硬盘上**（比如 `/mnt/games/SteamLibrary`），也不用特殊处理——
本方案会读取 Steam 的 `libraryfolders.vdf` 配置文件，把注册过的库全部找出来。

### 不确定自己的路径？跑一下这个

```bash
python3 lwe_paths.py report
```

它会打印出探测到的 Steam 库、壁纸目录（含数量）、assets 目录和渲染器路径。例如：

```
Steam 根目录 / 库：
  /home/user/.steam/debian-installation

创意工坊壁纸目录：
  /home/user/.steam/debian-installation/steamapps/workshop/content/431960（42 张）

Wallpaper Engine assets：
  /home/user/.steam/debian-installation/steamapps/common/wallpaper_engine/assets

渲染器：
  /home/user/linux-wallpaperengine/build/output/linux-wallpaperengine（存在）
```

如果它找错了，或者你有特殊布局，用环境变量显式指定即可（见下方「目录与自动探测」）。

## 环境要求

| 项目 | 要求 |
|---|---|
| 发行版 | Ubuntu 24.04 或兼容的 Debian 系（`install.sh` 用 apt 装依赖） |
| 桌面环境 | GNOME Shell 45–50 |
| 会话类型 | **Wayland**（X11 会话下 GNOME 有别的方案，不适用本方案） |
| 显卡 | 需要 OpenGL 3.3+，NVIDIA 专有驱动可用 |
| 磁盘 | 渲染器及其依赖的 CEF 约需 **4 GB**（其中 CEF 解压后约 1.5 GB） |

## 安装

克隆到**你喜欢的任意位置**（下面的例子用 `~/apps`，换成你自己的路径即可）：

```bash
mkdir -p ~/apps && cd ~/apps
git clone https://github.com/Fitz8863/wallpaper-engine-gnome.git
cd wallpaper-engine-gnome
./install.sh
```

后续所有命令都在这个克隆目录里执行。为方便起见，下面把该目录记为 `$PROJECT`：

```bash
export PROJECT="$HOME/apps/wallpaper-engine-gnome"   # 改成你的实际路径
```

安装脚本会依次：装系统依赖 → 克隆并编译渲染器 → 安装 GNOME 扩展 → 注册应用入口。
每一步都幂等，重复执行会跳过已完成的部分。也可以分步执行：

```bash
./install.sh --deps        # 只装系统依赖
./install.sh --renderer    # 只克隆并编译渲染器
./install.sh --extension   # 只安装 GNOME 扩展
./install.sh --desktop     # 只注册应用入口
```

**首次安装后需要注销并重新登录一次**——Wayland 下 GNOME Shell 无法热加载新扩展。

> **把项目目录挪了位置？** 重新跑一次 `./install.sh --desktop`。
> `.desktop` 里存的是绝对路径，挪目录后旧路径就失效了。另外 GNOME Shell 会缓存
> `.desktop` 的内容，**原地改写不一定能让缓存刷新**——症状是点图标毫无反应，
> 日志里报旧路径不存在。所以安装脚本改成「先删再建」来触发目录级事件；
> 你要是手动改这个文件，改完注销重登最保险。

### 关于渲染器和 CEF

渲染器默认编译到 `~/linux-wallpaperengine`，用 `RENDERER_DIR` 可以改：

```bash
RENDERER_DIR=~/src/lwe ./install.sh --renderer
```

编译时会自动下载约 **370MB** 的 CEF（Chromium 嵌入式框架，只有网页类壁纸需要它）。
如果卡在下载或报 `Transferred a partial file`（大文件在国内网络下容易中途断流）：

```bash
URL="https://cef-builds.spotifycdn.com/cef_binary_135.0.17%2Bgcbc1c5b%2Bchromium-135.0.7049.52_linux64_minimal.tar.bz2"
curl -L -C - --retry 10 "$URL" -o "$RENDERER_DIR/build/cef/$(basename "$URL")"
# 下载完整后删掉半成品的解压目录，再重跑 cmake
```

## 使用

### 图形界面

在应用列表里搜索「**壁纸选择器**」，或：

```bash
python3 "$PROJECT/wallpaper-picker.py"
python3 "$PROJECT/wallpaper-picker.py" --select 3422875812   # 启动时预选某张
```

点击任意壁纸卡片即应用；右侧面板显示这张壁纸自己的可调项；右上角「停止」可关闭
动态壁纸——**停止会同时撤掉登录自启**，停了就是停了，下次开机不会再自动恢复；
重新选一张壁纸会自动恢复自启。改动设置不需要手动保存，会自动写盘并重新加载壁纸
（带防抖，拖滑块不会把渲染器反复重启）。

### 命令行

```bash
cd "$PROJECT"
./start-wallpaper.sh --list          # 列出全部壁纸（自动扫描工坊目录）
./start-wallpaper.sh 3050160027      # 按 ID 切换
./start-wallpaper.sh 芙莉莲           # 按名称关键词切换
./start-wallpaper.sh --stop          # 停止（并撤掉登录自启）
./start-wallpaper.sh --status        # 查看状态
./start-wallpaper.sh 3050160027 -f 30 --disable-particles   # 透传渲染器参数
```

### 显示器名一般不用管

渲染器需要知道把画面输出到哪块屏。这不是常量——笔记本内置屏通常叫 `eDP-1`，
外接显示器可能叫 `HDMI-1`、`DP-1`、`DP-2`……**本方案会向 Mutter 查询当前的主显示器，
自动填好这个参数**，正常情况下你不需要做任何事。

只有在自动探测不对时（比如想输出到副屏而不是主屏）才需要手动指定：

```bash
# 先看看系统里都有哪些屏
gdbus call --session --dest org.gnome.Mutter.DisplayConfig \
  --object-path /org/gnome/Mutter/DisplayConfig \
  --method org.gnome.Mutter.DisplayConfig.GetResources \
  | grep -oP "'[a-zA-Z0-9-]+'"

# 再指定要用哪一块
export LWE_SCREEN="HDMI-1"
```

想让它永久生效，写进 `~/.profile` 或 `~/.config/environment.d/` 里。

### 目录与自动探测

所有路径都可以用环境变量覆盖，优先级高于自动探测：

| 环境变量 | 作用 | 默认行为 |
|---|---|---|
| `LWE_WORKSHOP` | 创意工坊壁纸目录 | 读 Steam 的 `libraryfolders.vdf` 自动找 |
| `LWE_ASSETS` | Wallpaper Engine 的 `assets` 目录 | 同上，在库里找 `common/wallpaper_engine/assets` |
| `LWE_BIN` | 渲染器二进制路径 | `~/linux-wallpaperengine/build/output/linux-wallpaperengine` |
| `LWE_SCREEN` | 显示器名 | 向 Mutter 查询主屏，失败才回落 `eDP-1` |
| `LWE_STATE_DIR` | 运行时状态目录（壁纸清单/日志/pid/缩略图缓存） | `~/.cache/wallpaper-picker` |

排查路径问题时：

```bash
python3 lwe_paths.py report      # 完整报告（库、壁纸、assets、渲染器、显示器）
python3 lwe_paths.py workshop    # 只打印壁纸目录
python3 lwe_paths.py screen      # 只打印探测到的主显示器
```

### 配置文件

界面上的改动会写入 `~/.cache/wallpaper-picker/settings.json`，`start-wallpaper.sh`
启动时会读取它并翻译成渲染器参数——所以图形界面和命令行的行为始终一致。

```json
{
  "silent": false,
  "volume_default": 15,
  "volumes": { "3018048025": 30 },
  "fps": 30,
  "scaling": "default",
  "particles": true,
  "parallax": true,
  "mouse": true,
  "automute": true,
  "properties": {
    "3422875812": { "clouds": "0", "music": "0.3" }
  }
}
```

`volumes` 和 `properties` 都是逐壁纸的，键是壁纸 ID。`volume_default` 是没单独
设过音量的壁纸的兜底值。删掉这个文件即可恢复渲染器默认值。

命令行透传的参数优先级更高，会覆盖设置文件里的值：

```bash
./start-wallpaper.sh 3050160027 -f 60     # 这一次用 60 帧，不改设置文件
```

## 工作原理

```
┌──────────────────────┐
│  wallpaper-picker.py │  GTK4 + libadwaita 图形界面
└──────────┬───────────┘
           │ 调用
┌──────────▼───────────┐
│  start-wallpaper.sh  │  参数解析 / 清单扫描 / 进程管理 / 自启
└──────────┬───────────┘
           │ 启动
┌──────────▼────────────────┐
│ linux-wallpaperengine     │  C++ 渲染器，--gnome 模式
│ （kv9898 fork, gnome 分支）│  渲染到普通 xdg-shell 窗口
└──────────┬────────────────┘
           │ 窗口标题编码元数据:  @linux-wallpaperengine!{"monitor":"...",...}
┌──────────▼────────────────┐
│ GNOME Shell 扩展           │  识别窗口 → Clutter.Clone 克隆进背景层
│ linux-wallpaperengine@... │  并从 Alt+Tab / 概览 / 任务栏隐藏原窗口
└───────────────────────────┘
```

关键点：**渲染器和扩展之间只靠窗口标题传信息**。扩展匹配 `@linux-wallpaperengine!`
前缀并解析随后的 JSON（显示器名、尺寸、位置），把窗口画面克隆进 GNOME Shell 的
背景组。这是目前 GNOME Wayland 下唯一可行的路径——任何 GUI 前端都必须能把
`--gnome` 和 `--screen-root` 透传给渲染器，这也是绝大多数现成前端无法直接套用的原因。

`lwe_paths.py` 是两边的共用模块：图形界面和命令行都通过它解析路径，因此不存在
"界面找得到、命令行找不到"这类不一致。

## 已知限制

- **仅 Wayland**。X11 会话下渲染器走的是另一套逻辑，本方案的扩展不起作用。
- **单显示器**。当前只把画面输出到 `LWE_SCREEN` 指定的那一块屏，多屏需要分别指定。
- **网页类壁纸可能不稳定**。上游的 CEF 集成仍在修，场景类和视频类壁纸基本没问题。
- **全屏自动暂停不可用**。渲染器会提示 `Fullscreen detection not supported by your
  Wayland compositor`——Wayland 下没有统一的焦点查询接口。
- **切换壁纸时控制台会有噪音**。日志出现
  `Object .LWPELiveWallpaper ... has been already disposed` 是扩展清理旧实例时的
  告警，不影响功能。
- **部分复杂场景壁纸渲染异常**。个别使用新版场景格式（schema v4）且对象众多的
  "高度自定义"壁纸（如创意工坊 3351163962），渲染器暂不支持其中的部分特性：
  着色器编译失败的对象显示为黑块、文字层乱码、个别图层定位错误。这是上游
  渲染器的兼容性范围问题，同素材在 Windows 的 Wallpaper Engine 上正常。
- **GPU 占用不低**。实测场景类壁纸约 +11W 功耗 / 1.5GB 显存；把帧率上限调到 10
  可以降到 +5W。这是动态壁纸的固有代价。

## 打包

```bash
./packaging/build-deb.sh            # 产物在 dist/
./packaging/build-deb.sh --install  # 构建后直接安装
./packaging/publish.sh              # 发版：建 Release + 设置仓库信息（需 gh 已登录）
```

deb 只包含本方案自己这一层（界面 + 脚本 + GNOME 扩展），**不含渲染器**——渲染器必须
从源码编译，产物 1.5GB（光 `libcef.so` 就 1.3GB），且其 CEF 只有网页类壁纸用得上。
塞进包会让体积失控，构建期联网抓文件也不符合 Debian 政策。

## 开发笔记

几个踩过的坑，避免重复踩：

- **GdkPixbuf 加载 GIF 动图**：`Pixbuf.new_from_file_at_scale()` 遇到动图会直接抛
  `Not all frames of the GIF image were loaded`，必须改用 `PixbufAnimation`
  取首帧再 `scale_simple`。工坊壁纸里 GIF 预览很常见。
- **暗色壁纸缩略图**：不少壁纸本身平均亮度极低，缩略图铺在深色卡片上会和背景糊成
  一片，需要加描边。
- **解析 `--list-properties` 的坑**：输出是「属性名 - 类型」加缩进的 `Text:` /
  `Value:` 行，但组合类型的候选项（`Values:` 后的 `0 = 24H`）缩进两格且 `Values:`
  顶格。更要命的是跳过某个属性类型时必须把「当前属性」置空，否则它后面那些缩进的
  行会被算到上一个属性头上——表现为某个滑块的标题莫名其妙变成小写的内部名。
- **`Gdk.RGBA(...)` 在 PyGObject 里不能传构造参数**：会静默忽略并给一个全透明色，
  只能先建对象再逐个赋值。
- **免注销验证渲染**：调试时不必每次都注销看效果，用窗口模式直接出图：
  ```bash
  "$RENDERER" -w 50x50x1280x720 --bg <ID> --screenshot /tmp/test.png --screenshot-delay 8
  ```
  注意几何格式是 `XxYxWxH`（坐标在前）。
- **GNOME 截图权限**：`org.gnome.Shell.Screenshot` D-Bus 接口对普通程序返回
  `Screenshot is not allowed`。选择器内置了 `--snapshot <png>` 参数，用
  `Gtk.WidgetPaintable` 把界面自己渲染成图片，方便无头自查布局。

## 相关项目

- [Almamu/linux-wallpaperengine](https://github.com/Almamu/linux-wallpaperengine) — 渲染器主体（GPL-3.0）
- [kv9898/linux-wallpaperengine](https://github.com/kv9898/linux-wallpaperengine) — `gnome` 分支，提供 `--gnome` 模式与配套扩展（GPL-3.0）

## 参与开发

架构说明、设计决策的来龙去脉、踩过的坑和待办清单都在 [HANDOFF.md](HANDOFF.md)。

## 许可证

GPL-3.0。本仓库附带 `gnome-extension/` 目录下的第三方代码，来源与许可见该目录的 README。
