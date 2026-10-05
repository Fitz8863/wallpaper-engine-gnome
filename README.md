# Wallpaper Engine 动态壁纸 · GNOME Wayland

在 GNOME（Wayland 会话）上使用 Wallpaper Engine 创意工坊壁纸：原生 GTK4 图形化选择器
+ 命令行启动器 + 配套 GNOME Shell 扩展，一个安装脚本搞定。

[![Latest Release](https://img.shields.io/badge/release-v1.1.0-blue)](https://github.com/Fitz8863/wallpaper-engine-gnome/releases/latest)
![License](https://img.shields.io/badge/license-GPL--3.0-green)

> **背景**：Wallpaper Engine 官方只支持 Windows；Linux 社区渲染器
> [linux-wallpaperengine](https://github.com/Almamu/linux-wallpaperengine)
> 依赖的 `wlr-layer-shell` 协议在 GNOME 的 Mutter 合成器上不存在，直接运行是黑屏。
> 本方案用一个 GNOME Shell 扩展把渲染窗口克隆进桌面背景层，绕开这个限制——
> 这是目前 GNOME Wayland 下唯一可行的路径。

## 功能一览

| | |
|---|---|
| 🖼️ **图形化选择器** | 16:9 缩略图网格、搜索、按类型筛选、点击即切换；每张壁纸显示素材原始分辨率 |
| ⚙️ **属性面板** | 自动读取壁纸作者定义的可调项（开关/滑块/下拉/取色器），改动即时生效 |
| 🔊 **逐壁纸音量** | 每张壁纸记住自己的音量，与官方 Wallpaper Engine 行为一致 |
| 🎚️ **播放设置** | 静音、帧率上限（预设下拉）、缩放模式、自动静音、粒子/视差/鼠标交互 |
| 🧰 **顶栏托盘** | 常驻托盘图标：左键唤起窗口，右键菜单打开/停止/退出；点 ✕ 最小化到托盘 |
| 🌙 **开机自启** | 登录后自动恢复上次的壁纸并常驻托盘（设置中可关闭） |
| 🌐 **中英双语** | 界面语言可选：跟随系统 / 简体中文 / English |
| 💻 **命令行** | 按 ID / 名称关键词切换，支持透传渲染器参数，可脚本化 |
| 🚀 **性能** | 缩略图双层缓存 + 异步加载，搜索筛选不卡顿；壁纸以屏幕原生分辨率渲染 |

## 环境要求

| 项目 | 要求 |
|---|---|
| 发行版 | Ubuntu 24.04 或兼容的 Debian 系（`install.sh` 用 apt 装依赖） |
| 桌面环境 | GNOME Shell 45–50 |
| 会话类型 | **Wayland**（X11 会话不适用） |
| 显卡 | OpenGL 3.3+，NVIDIA 专有驱动可用 |
| 磁盘 | 渲染器及 CEF 约需 **4 GB**（CEF 解压后约 1.5 GB） |
| Steam | 已安装 Wallpaper Engine（appid `431960`）并订阅过至少一张壁纸 |

**本方案不下载壁纸**——它把 Steam 已经下载到本地的工坊内容渲染成桌面背景。
Steam 原生 deb / Flatpak / Snap 安装方式都会被自动识别，Steam 库放在其他硬盘
（读 `libraryfolders.vdf`）也能找到。

## 安装

### 方式一：一键安装（推荐）

```bash
git clone https://github.com/Fitz8863/wallpaper-engine-gnome.git
cd wallpaper-engine-gnome
./install.sh
```

安装脚本依次完成：装系统依赖 → 克隆并编译渲染器 → 安装 GNOME 扩展 → 注册应用入口。
每一步幂等，重复执行自动跳过已完成部分；也可以分步：

```bash
./install.sh --deps        # 只装系统依赖
./install.sh --renderer    # 只克隆并编译渲染器
./install.sh --extension   # 只安装 GNOME 扩展
./install.sh --desktop     # 只注册应用入口
```

**首次安装后需要注销并重新登录一次**——Wayland 下 GNOME Shell 无法热加载新扩展。

### 方式二：deb 包安装

从 [Releases](https://github.com/Fitz8863/wallpaper-engine-gnome/releases/latest)
下载 deb 后：

```bash
sudo dpkg -i ./wallpaper-engine-gnome_1.1.0_amd64_linux.deb
```

> 包内架构为 `all`（纯 Python + 脚本，无平台二进制），附件名带 `amd64_linux`
> 平台标识便于识别；安装命令用 `dpkg -i`（`apt` 对非标准 deb 文件名挑剔，
> 缺依赖时跑 `sudo apt-get install -f` 补齐即可）。

> **注意**：deb 只包含本方案这一层（界面 + 脚本 + 扩展），**不含渲染器**——
> 渲染器必须从源码编译（产物 1.5GB，其中 CEF 占 1.3GB 且只有网页类壁纸用得上）。
> 装完 deb 后仍需运行一次 `install.sh --renderer` 编译渲染器。

### 关于渲染器与 CEF

渲染器默认编译到 `~/linux-wallpaperengine`，可用 `RENDERER_DIR` 改位置：

```bash
RENDERER_DIR=~/src/lwe ./install.sh --renderer
```

编译时会自动下载约 **370MB** 的 CEF。国内网络下如果下载断流
（报 `Transferred a partial file`），手动续传：

```bash
URL="https://cef-builds.spotifycdn.com/cef_binary_135.0.17%2Bgcbc1c5b%2Bchromium-135.0.7049.52_linux64_minimal.tar.bz2"
curl -L -C - --retry 10 "$URL" -o "$RENDERER_DIR/build/cef/$(basename "$URL")"
# 下载完整后删掉半成品解压目录，再重跑 cmake
```

## 使用

### 图形界面

在应用列表搜索「**壁纸选择器**」打开：

- 点击卡片即应用壁纸，当前壁纸有高亮标记
- 右侧面板显示该壁纸的可调项与音量；改动自动保存并即时生效
- 顶栏齿轮打开设置：语言、开机自启、启动时恢复壁纸、关闭窗口行为
- 右上角「停止」关闭动态壁纸（同时撤掉登录自启，下次开机不会自己恢复）

### 命令行

```bash
./start-wallpaper.sh --list          # 列出全部壁纸
./start-wallpaper.sh 3050160027      # 按 ID 切换
./start-wallpaper.sh 芙莉莲           # 按名称关键词切换
./start-wallpaper.sh --stop          # 停止（并撤掉登录自启）
./start-wallpaper.sh --status        # 查看运行状态
./start-wallpaper.sh 3050160027 -f 30 --disable-particles   # 透传渲染器参数
```

### 帧率上限怎么调

不同类型壁纸对帧率上限的响应完全不同（参见官方文档
[Performance / GPU](https://help.wallpaperengine.io/en/performance/gpu.html)）：

| 壁纸类型 | 帧率由谁决定 | 调整这一项 |
|---|---|---|
| 视频 | 视频文件本身（24/30/60fps） | 无效 |
| 场景 | 引擎实时渲染 | **有效，主要的省电手段** |
| 网页 | CEF 渲染 | 效果有限 |

界面提供与官方相同形式的预设下拉（240 ~ 1 fps，默认 30）。实测场景壁纸把上限
从 30 降到 10，整机功耗从 24W 降到 18W（省约 55%）。

### 播放速度怎么调

渲染器没有全局倍速参数。速度由壁纸自带属性实现——不少场景壁纸带有
`scrollspeed` 之类的属性，选中后会在「壁纸属性」里出现对应控件；
没有这类属性的壁纸本身就不支持变速。

### 多显示器

默认自动输出到主显示器（向 Mutter 查询）。想输出到副屏时：

```bash
export LWE_SCREEN="HDMI-1"     # 显示器名用下面命令查
```

```bash
gdbus call --session --dest org.gnome.Mutter.DisplayConfig \
  --object-path /org/gnome/Mutter/DisplayConfig \
  --method org.gnome.Mutter.DisplayConfig.GetResources \
  | grep -oP "'[a-zA-Z0-9-]+'"
```

要永久生效可写进 `~/.profile` 或 `~/.config/environment.d/`。

## 配置与数据

### settings.json

所有设置写入 `~/.cache/wallpaper-picker/settings.json`，图形界面和命令行共用
同一份，删除该文件即恢复默认。可手动编辑的常用键：

| 键 | 含义 |
|---|---|
| `volumes` / `properties` | 逐壁纸的音量 / 壁纸属性，键为壁纸 ID |
| `volume_default` | 未单独设过音量的壁纸的兜底值 |
| `autostart` / `last` | 开机自启开关 / 上次的壁纸 ID（登录恢复用） |
| `close_action` | 关闭窗口行为：`tray`（默认，隐藏到托盘）或 `quit` |
| `language` | 界面语言：`system` / `zh` / `en` |
| `workshop` | 手动指定的壁纸目录；删掉该键或设为 `null` 恢复自动探测 |

命令行透传的参数优先级更高，可临时覆盖（不改设置文件）：

```bash
./start-wallpaper.sh 3050160027 -f 60     # 这一次用 60 帧
```

### 路径探测与环境变量

壁纸目录的优先级：**环境变量 `LWE_WORKSHOP` > 设置里手动选择 > 自动探测**。
没有 Steam、或壁纸是第三方下载/手动整理的，在**设置 → 壁纸来源**里选择
目录即可，选择器会像 Steam 工坊一样列出并管理里面的壁纸（每张壁纸一个
子目录、内含 `project.json`）；启动时会把不在 Steam 工坊下的壁纸以完整
路径传给渲染器，无需其他配置。

其余路径自动探测，特殊布局可用环境变量覆盖：

| 环境变量 | 作用 | 默认行为 |
|---|---|---|
| `LWE_WORKSHOP` | 创意工坊壁纸目录 | 读 Steam 的 `libraryfolders.vdf` 自动找 |
| `LWE_ASSETS` | WE 的 `assets` 目录 | 在各 Steam 库中查找 |
| `LWE_BIN` | 渲染器二进制路径 | `~/linux-wallpaperengine/build/output/...` |
| `LWE_SCREEN` | 显示器名 | 向 Mutter 查询主屏，失败回落 `eDP-1` |
| `LWE_STATE_DIR` | 运行时状态目录 | `~/.cache/wallpaper-picker` |

路径排查：

```bash
python3 wallpaper_picker/paths.py report      # 完整报告（库/壁纸/assets/渲染器/显示器）
python3 wallpaper_picker/paths.py screen      # 只打印主显示器
```

## 常见问题

**点应用图标没反应？**
注销重登一次。Wayland 下 GNOME Shell 缓存了 `.desktop` 内容，首次安装后必须重登
才会加载新扩展。若手动挪过项目目录，重跑 `./install.sh --desktop`。

**壁纸播放时没有声音？**
检查右侧面板的逐壁纸音量与「静音」开关；「其他程序出声时自动静音」开着时，
播放音乐或看视频会让壁纸静音——这是默认行为，可在播放设置中关闭。

**怎么彻底退出程序？**
托盘右键 → 退出。点窗口 ✕ 是最小化到托盘（可在设置里改为直接退出）。

**找不到创意工坊壁纸？**
确认 Steam 已订阅且下载完成，然后跑 `python3 wallpaper_picker/paths.py report`
看探测结果；库在非标准位置时用 `LWE_WORKSHOP` 指定。

**网页类壁纸打开慢？**
网页壁纸由 CEF（Chromium）渲染，首次初始化需要十几秒，属正常现象。

## 已知限制

- 仅支持 Wayland 会话（X11 不适用）。
- 当前面向单显示器输出（多屏用 `LWE_SCREEN` 指定）。
- **网页类壁纸在当前渲染器下不可用**：其 GNOME 分支的 CEF（浏览器引擎）
  初始化失败，进程挂着但永远没有画面（已定位为渲染器上游缺陷，与选择器
  无关）。选择器会正常列出这类壁纸，点击后会一直停在空白桌面。
- 「预设包」类型壁纸是参数配置而非壁纸本体，需要它所依赖的壁纸，选择器
  会明确提示（渲染器暂不支持直接启动）。
- 全屏应用时自动暂停在 Wayland 下不可用（无统一焦点查询接口）。
- 动态壁纸本身有 GPU 开销：场景类约 +11W / 1.5GB 显存，帧率上限调低可显著缓解
  （实测 30→10fps 约省一半功耗）。这是动态壁纸的固有代价。

## 工作原理

```
┌──────────────────────┐
│  wallpaper-picker.py │  GTK4 + libadwaita 图形界面
└──────────┬───────────┘
           │ 调用
┌──────────▼───────────┐
│  start-wallpaper.sh  │  参数解析 / 清单扫描 / 进程管理
└──────────┬───────────┘
           │ 启动
┌──────────▼────────────────┐
│ linux-wallpaperengine     │  C++ 渲染器，--gnome 模式
│ （kv9898 fork, gnome 分支）│  渲染到普通 xdg-shell 窗口
└──────────┬────────────────┘
           │ 窗口标题编码元数据
┌──────────▼────────────────┐
│ GNOME Shell 扩展           │  识别窗口 → Clutter.Clone 克隆进
│ linux-wallpaperengine@... │  背景层，并从 Alt+Tab/概览隐藏
└───────────────────────────┘
```

渲染器和扩展之间只靠窗口标题传递信息（`@linux-wallpaperengine!{json}`），
窗口画面被克隆进 GNOME Shell 背景组，原窗口从任务栏与概览中隐藏。
图形界面与命令行通过 `wallpaper_picker/paths.py` 共用同一份路径探测，
两个入口的行为始终一致。

## 参与开发

架构说明、设计决策、开发注意事项与待办清单见 [HANDOFF.md](HANDOFF.md)；
运行测试：`python3 -m pytest tests/`（38 例，不需要显示环境）。

## 相关项目

- [Almamu/linux-wallpaperengine](https://github.com/Almamu/linux-wallpaperengine) — 渲染器主体（GPL-3.0）
- [kv9898/linux-wallpaperengine](https://github.com/kv9898/linux-wallpaperengine) — `gnome` 分支：`--gnome` 模式与配套扩展（GPL-3.0）

## 许可证

GPL-3.0。`gnome-extension/` 目录下的第三方代码来源与许可见该目录 README。
