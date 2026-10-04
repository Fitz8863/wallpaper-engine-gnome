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
- **登录自启**：切换壁纸时自动记住，下次开机还是它
- **命令行**：可脚本化调用，支持 ID、名称关键词、播放列表
- **限帧省电**：笔记本上可限制帧率降低 GPU 功耗

## 环境要求

| 项目 | 要求 |
|---|---|
| 桌面环境 | GNOME Shell 45–50（本方案在 **GNOME 46 + Wayland** 上验证通过） |
| 会话类型 | **Wayland**（X11 会话下 GNOME 有别的方案，不适用本方案） |
| 显卡 | 需要 OpenGL 3.3+；NVIDIA 专有驱动可用（本项目在 RTX 4060 上验证） |
| 其他 | Steam 上已购买 Wallpaper Engine，并订阅了至少一张壁纸 |

Wallpaper Engine 本体在 Linux 上通过 Proton 运行，**仅用于浏览和订阅壁纸**——它的"应用壁纸"功能依赖 Windows 外壳 API，在 Linux 上无法生效（这是原理性限制）。

## 安装

```bash
git clone <你的仓库地址> ~/projects/wallpaper
cd ~/projects/wallpaper
./install.sh
```

安装脚本会依次：装系统依赖 → 克隆并编译渲染器 → 安装 GNOME 扩展 → 注册应用入口。

**首次安装后需要注销并重新登录一次**——Wayland 下 GNOME Shell 无法热加载新扩展。

也可以分步执行：`./install.sh --deps` / `--renderer` / `--extension` / `--desktop`。

### 关于 CEF 下载

编译时会自动下载约 **370MB** 的 CEF（Chromium 嵌入式框架，网页类壁纸需要它）。
如果卡在下载或报 `Transferred a partial file`：

```bash
# 用支持断点续传的方式手动拉取，放到渲染器的 build/cef/ 目录
URL="https://cef-builds.spotifycdn.com/cef_binary_135.0.17%2Bgcbc1c5b%2Bchromium-135.0.7049.52_linux64_minimal.tar.bz2"
curl -L -C - --retry 10 "$URL" \
  -o ~/linux-wallpaperengine/build/cef/$(basename "$URL")
# 校验通过后删掉半成品解压目录再重跑 cmake
```

国内网络下大文件容易中途断流，必要时走代理或分段下载。

## 使用

### 图形界面

在应用列表里搜索「**壁纸选择器**」，或：

```bash
python3 ~/projects/wallpaper/wallpaper-picker.py
```

点击任意壁纸卡片即切换；右上角「停止」可关闭动态壁纸。

### 命令行

```bash
./start-wallpaper.sh --list          # 列出全部壁纸（自动扫描工坊目录）
./start-wallpaper.sh 3050160027      # 按 ID 切换
./start-wallpaper.sh 芙莉莲           # 按名称关键词切换
./start-wallpaper.sh --stop          # 停止
./start-wallpaper.sh --status        # 查看状态
./start-wallpaper.sh 3050160027 -f 30 --disable-particles   # 透传渲染器参数
```

### 可配置项

| 环境变量 | 说明 | 默认 |
|---|---|---|
| `LWE_BIN` | 渲染器二进制路径 | `~/linux-wallpaperengine/build/output/linux-wallpaperengine` |
| `LWE_SCREEN` | 显示器名 | `eDP-1` |
| `LWE_STATE_DIR` | 运行时状态目录（清单/日志/pid） | `~/.cache/wallpaper-picker` |

多显示器用户需要查自己的显示器名：

```bash
gdbus call --session --dest org.gnome.Mutter.DisplayConfig \
  --object-path /org/gnome/Mutter/DisplayConfig \
  --method org.gnome.Mutter.DisplayConfig.GetResources | grep -oP "'[a-zA-Z0-9-]+'"
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
           │ 窗口标题编码元数据:  @linux-wallpaperengine!{"monitor":"eDP-1",...}
┌──────────▼────────────────┐
│ GNOME Shell 扩展           │  识别窗口 → Clutter.Clone 克隆进背景层
│ linux-wallpaperengine@... │  并从 Alt+Tab / 概览 / 任务栏隐藏原窗口
└───────────────────────────┘
```

关键点：**渲染器和扩展之间只靠窗口标题传信息**。扩展匹配 `@linux-wallpaperengine!`
前缀并解析随后的 JSON（显示器名、尺寸、位置），把窗口画面克隆进 GNOME Shell 的
背景组。这是目前 GNOME Wayland 下唯一可行的路径——任何 GUI 前端都必须能把
`--gnome` 和 `--screen-root` 透传给渲染器，这也是绝大多数现成前端无法直接套用的原因。

## 已知限制

- **仅 Wayland**。X11 会话下渲染器走的是另一套逻辑，本方案的扩展不起作用。
- **网页类壁纸可能不稳定**。上游的 CEF 集成仍在修，场景类和视频类壁纸基本没问题。
- **全屏自动暂停不可用**。渲染器会提示 `Fullscreen detection not supported by your
  Wayland compositor`——Wayland 下没有统一的焦点查询接口。
- **切换壁纸时控制台会有噪音**。日志出现
  `Object .LWPELiveWallpaper ... has been already disposed` 是扩展清理旧实例时的
  告警，不影响功能。
- **GPU 占用不低**（场景类壁纸约 40% / 1.5GB 显存），笔记本建议限帧：
  `-f 30`。

## 开发笔记

几个踩过的坑，避免重复踩：

- **GdkPixbuf 加载 GIF 动图**：`Pixbuf.new_from_file_at_scale()` 遇到动图会直接抛
  `Not all frames of the GIF image were loaded`，必须改用 `PixbufAnimation`
  取首帧再 `scale_simple`。工坊壁纸里 GIF 预览很常见。
- **暗色壁纸缩略图**：不少壁纸本身平均亮度极低，缩略图铺在深色卡片上会和背景糊成
  一片，需要加描边。
- **免注销验证渲染**：调试时不必每次都注销看效果，用窗口模式直接出图：
  ```bash
  ./linux-wallpaperengine -w 50x50x1280x720 --bg <ID> \
      --screenshot /tmp/test.png --screenshot-delay 8
  ```
  注意几何格式是 `XxYxWxH`（坐标在前）。
- **GNOME 截图权限**：`org.gnome.Shell.Screenshot` D-Bus 接口对普通程序返回
  `Screenshot is not allowed`。选择器内置了 `--snapshot <png>` 参数，用
  `Gtk.WidgetPaintable` 把界面自己渲染成图片，方便无头自查布局。

## 相关项目

- [Almamu/linux-wallpaperengine](https://github.com/Almamu/linux-wallpaperengine) — 渲染器主体（GPL-3.0）
- [kv9898/linux-wallpaperengine](https://github.com/kv9898/linux-wallpaperengine) — `gnome` 分支，提供 `--gnome` 模式与配套扩展（GPL-3.0）

## 许可证

GPL-3.0。本仓库附带 `gnome-extension/` 目录下的第三方代码，来源与许可见该目录的 README。
