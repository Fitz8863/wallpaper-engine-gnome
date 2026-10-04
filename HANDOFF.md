# 开发交接说明

面向接手继续开发的人（或 AI 助手）。**用户文档见 [README.md](README.md)**，本文只讲
开发和维护相关的东西。

---

## 一分钟了解项目

在 GNOME（Wayland）上使用 Wallpaper Engine 创意工坊壁纸。因为 GNOME 的 Mutter
合成器不实现 `wlr-layer-shell`，官方渲染器 [linux-wallpaperengine] 在 GNOME 上直接跑
是黑屏，所以本方案用一个配套的 GNOME Shell 扩展，通过 `Clutter.Clone` 把渲染器窗口
克隆进桌面背景层。

本仓库只包含**自己写的那一层**：图形界面、启动器、安装/打包脚本、以及一份随仓库
附带的第三方扩展。渲染器本身是外部依赖，由 `install.sh` 克隆并编译。

[linux-wallpaperengine]: https://github.com/Almamu/linux-wallpaperengine

## 目录结构

```
wallpaper-picker.py     GTK4 + libadwaita 图形选择器（主程序，约 1100 行）
start-wallpaper.sh      命令行启动器：参数解析、进程管理、自启
lwe_paths.py            路径与显示器探测（被上面两者共用）
lwe_scan.py             创意工坊扫描：清单解析+排序（被上面两者共用）
install.sh              一键安装：系统依赖 → 编译渲染器 → 装扩展 → 注册入口
packaging/
  build-deb.sh          构建 deb（只打包本项目这一层）
  publish.sh            发版：建 GitHub Release + 设置仓库信息
  enable-extension.py   deb 安装/卸载时增删 GNOME 扩展启用项
gnome-extension/        配套 GNOME Shell 扩展（GPL-3.0 第三方代码，见其 README）
desktop/                桌面入口模板，用 @PROJECT_DIR@ 占位，安装时替换
icons/                  应用图标：generate.py 一键再生成 SVG 与各尺寸 PNG
```

## 架构与数据流

```
wallpaper-picker.py ──调用──▶ start-wallpaper.sh ──启动──▶ linux-wallpaperengine
        │                            │                          │
        └── 写 settings.json ────────┘                    --gnome 模式渲染到
                                    │                     普通 xdg-shell 窗口
                                    │                          │
                              读 settings.json           窗口标题编码元数据:
                              翻译成命令行参数            @linux-wallpaperengine!{...}
                                                               │
                                                     GNOME Shell 扩展识别并克隆
                                                     进背景层，隐藏原窗口
```

**关键契约**：渲染器和扩展之间只靠**窗口标题**传递信息。渲染器
（`WaylandOutputViewport::buildWindowTitle()`）生成：

```
@linux-wallpaperengine!{"monitor":"eDP-1","width":...,"height":...,"position":[...]}
```

扩展（`wallpaperManager.js`）匹配 `@linux-wallpaperengine!` 前缀并解析 JSON。
**修改任何一边的这个格式，另一边都会静默失效。**

## 关键设计决策

这几条看起来绕，但都有原因，改之前先读：

**1. 为什么必须用 kv9898 的 fork 而不是上游**
上游 `Almamu/linux-wallpaperengine` 只支持 `wlr-layer-shell`，GNOME 没有这个协议。
`kv9898` 的 `gnome` 分支加了 `--gnome` 模式（改用 xdg-shell 窗口）和配套扩展。
上游至今未合并。这是目前 GNOME Wayland 下唯一可行的路径。

**2. 为什么有 `lwe_paths.py` 这个独立模块**
图形界面和命令行都需要"壁纸在哪、显示器叫什么"，早期两边各写了一份硬编码列表，
必然会出现"界面找得到、命令行找不到"的不一致。现在两边都调这个模块。
它还负责解析 Steam 的 `libraryfolders.vdf`——**这是让 Steam 库装在别的硬盘上也能
被找到的关键**，早期只认 4 个固定路径，装在别的盘就一个都命中不了。

**3. 为什么设置写在 `settings.json` 而不是直接拼命令行**
图形界面和命令行共用同一份配置：界面改设置写文件，`start-wallpaper.sh` 启动时读文件
翻译成参数。这样两个入口的行为永远一致。命令行透传的参数优先级更高，可以临时覆盖。

**4. 为什么 deb 不包含渲染器**
渲染器必须从源码编译，构建时要下载 354MB 的 CEF，产物 1.5GB（光 `libcef.so` 就
1.3GB），而且 CEF 只服务于网页类壁纸。塞进 deb 会让体积失控，构建期联网抓文件也不
符合 Debian 政策。

**5. 为什么属性面板能自动生成控件**
渲染器的 `--list-properties` 会输出每张壁纸的可调项（作者在 Wallpaper Engine 编辑器
里定义的），格式规整，于是可以按类型自动生成开关/滑块/下拉/取色器，而不必为每张
壁纸写界面代码。这与官方 Wallpaper Engine 的 Properties 概念一致。

## 踩过的坑

按主题分组。每条都是实际调试出来的，**重复踩的概率很高**。

### 界面

**GIF 预览显示为空白**
`GdkPixbuf.Pixbuf.new_from_file_at_scale()` 遇到动图会直接抛
`Not all frames of the GIF image were loaded`。必须改用 `PixbufAnimation` 取首帧再
`scale_simple`。工坊壁纸里 GIF 预览很常见（本机 42 张里有 9 张）。
另：不少壁纸本身画面极暗，缩略图要和卡片背景区分开，需要描边。

**解析 `--list-properties` 时属性标签被污染**
输出格式是「属性名 - 类型」加缩进的 `Text:` / `Value:` 行。两个坑：
- 组合类型的候选项（`Values:` 之后的 `0 = 24H`）缩进两格，而 `Values:` 自身顶格；
- **跳过某个属性时必须把「当前属性」指针置空**，否则它后面那些缩进的行会被算到
  上一个属性头上——表现为某个滑块的标题莫名其妙变成小写的内部名。

**作者用 HTML 写属性标签**
例如 `<p>颜色<br>color`。见到尖括号就退回内部名会让界面显示 `pbrcolor`。
正确做法是剥掉标签取纯文本，过长（多半是整段说明或广告）才退回内部名。

**`schemecolor` 是个假开关**
它在官方 Wallpaper Engine 里是**给浏览器界面用的元数据**（决定创意工坊详情页的强调色），
不是绘制参数。渲染器把它列进了属性表，但改它对画面毫无影响。
实测：同一张壁纸只改这一个属性渲染两次，像素差 0.06%（仅动画时间差）；
对照 `pbrcolor` 是 3%、`clouds` 开关是 17%。
所以界面上把它过滤掉——**一个改了没反应的控件比没有更糟**，用户会以为软件坏了。

**`Gdk.RGBA(...)` 不能传构造参数**
PyGObject 会静默忽略并给一个全透明色，只能先建对象再逐个赋值。

**覆写 `do_shutdown` 做 chain-up 会报 CRITICAL**
想给应用加收尾钩子（如退出前 flush 设置），覆写 `do_shutdown` 再
`super().do_shutdown()`，PyGObject 会报 `failed to chain up on ::shutdown`。
改用 `self.connect("shutdown", ...)` 信号，语义相同、没有这个坑。

### 性能与交互

**切换壁纸时界面卡死约 4 秒**
两层原因叠加，缺一不可：
1. `start-wallpaper.sh` 里有两处固定 `sleep`（停旧实例后 1 秒、启动后 3 秒）。
   实测脚本返回耗时 4.13 秒，而扩展贴上桌面只比脚本返回晚 0.07 秒——**渲染器早就
   准备好了，纯粹是脚本在空等**。改成轮询进程状态后降到 1.1 秒。
2. 界面用 `subprocess.run` 同步调用，这几秒里 GTK 主循环完全停摆。
   改成后台线程 + `GLib.idle_add` 回主线程。

**切换后壁纸网格的滚动条跳回顶部**
`apply()` 原先调用 `populate()` 重建整个网格，`ScrolledWindow` 的调整值随之归零。
改为 `update_highlight()` 只改受影响卡片的 CSS 类和角标文字。
注意角标文字长度会变（「场景」→「使用中 · 场景」），等宽网格下可能撑动布局，
所以给它固定了字符宽度。
**网格重建现在只应发生在搜索、切换筛选、重新扫描时。**

**量性能要用功耗，不要用 GPU 利用率**
笔记本上 `nvidia-smi` 的 `utilization.gpu` 极不可靠——限帧 30→10fps，
利用率读数几乎不变，而功耗从 24W 降到 18W（省 55%）。
`power.draw` 才是可信信号。

### 系统集成

**点了应用图标没反应，日志报旧路径不存在**
GNOME Shell 会缓存 `.desktop` 的内容，而**原地改写文件不一定能让缓存失效**。
症状是 journal 里报"文件不存在"，但磁盘上的文件明明是对的。
解法是删除后重建（产生目录级文件系统事件），`install.sh` 已经这么做。
改完注销重登最保险。

**GNOME 限制了若干 D-Bus 接口**
`org.gnome.Shell.Screenshot` 返回 `Screenshot is not allowed`；
`org.gnome.Shell.FocusApp` 返回 `FocusApp is not allowed`。
所以界面自查要用自带的 `--snapshot`（见下），不要指望系统截图。

**`pkill` 的模式会匹配到自己**
`pkill -f "wallpaper-picker.py"` 会连带杀掉执行它的那个 shell（命令行里含同样字符串），
而 shell 异常终止可能带走其他进程。用方括号技巧避开：
`pkill -f "[w]allpaper-picker.py"`。注意 `grep -F` 不支持这个技巧（按字面匹配）。

### 构建

**CEF 下载断流**
约 370MB，实测直连完全不通、必须走代理，且单连接会稳定在 ~11MB 处截断。
需要分块并行下载 + 小块续传才能拿到完整包。

**`libmpv-dev` 是硬依赖**
`find_package(MPV REQUIRED)`，没有开关可绕。装它必须用 sudo。

**子模块首次克隆可能不完整**
`git clone --recurse-submodules` 后要确认 `src/External/` 下的目录非空，
必要时 `git submodule update --init --recursive --force` 补一次。

**hicolor 的 scalable SVG 图标不能假设目标机器渲染得了**
图标分发以 PNG 为主（48/64/128/256 全尺寸），SVG 只作矢量源附带。
原因：SVG 加载依赖目标机器的 gdk-pixbuf librsvg 加载器，实测存在
`loaders.cache` 里注册了 SVG 加载器、加载时却报
"Couldn't recognize the image file format" 的环境。另外 ImageMagick
的 `convert` 渲染 SVG 不认渐变/圆角裁剪，矢量图一律用
`icons/generate.py`（cairo）出图，别用 convert。

## 开发与调试

### 从源码运行（不要点图标）

```bash
cd <项目目录>
python3 wallpaper-picker.py
```

点图标的话错误信息会被吞掉。**从终端跑才能看到报错**。
改代码后重启程序即生效，不需要重装。

### 改动纪律

每次改动跑完验证（`py_compile`/`bash -n`、`--snapshot` 目检、计时对比、
针对性回归脚本）就 `git commit` 存档——一个主题一个提交，保证任何时候都
能回退到「已验证过的状态」。push 时机由维护者决定。

### 界面自查（绕开 GNOME 截图限制）

```bash
python3 wallpaper-picker.py --snapshot /tmp/ui.png [--select <壁纸ID>]
```

用 `Gtk.WidgetPaintable` 把窗口内容渲染成 PNG 后退出。截图会用更高的画布，
方便一次看全整个属性面板。

### 渲染自查（不用注销就能验证）

```bash
"$RENDERER" -w 50x50x1280x720 --bg <ID> --screenshot /tmp/s.png --screenshot-delay 8
```

窗口模式出图，注意几何格式是 `XxYxWxH`（坐标在前）。
比较两个参数的效果时，逐像素比对是最可靠的手段（见 schemecolor 那条）。

### 看日志

```bash
journalctl --user -b | grep lwpe          # GNOME 扩展的行为
tail -f ~/.cache/wallpaper-picker/wallpaper.log   # 渲染器输出
```

扩展成功把画面贴上桌面时会打印 `lwpe: wallpaper applied on monitor 0`。
切换壁纸时刷的 `Object .LWPELiveWallpaper ... has been already disposed` 是
清理旧实例的告警，不影响功能（属于可修的噪音）。

### 路径排查

```bash
python3 lwe_paths.py report      # 完整报告：库、壁纸目录、assets、渲染器、显示器
python3 lwe_paths.py workshop    # 只打印壁纸目录
python3 lwe_paths.py screen      # 只打印探测到的主显示器
```

## 当前状态与待办

**已完成并验证**：图形界面（网格/搜索/筛选/属性面板/播放设置）、
路径与显示器自动探测、deb 打包（含安装/升级/卸载全流程）、v1.0.0 已发布。
另有后续六项已实现（见下方提交历史），其中界面性能专项实测：
重建网格 270ms→12ms、窗口显示前阻塞 444ms→12ms。

**设置的分层**（与官方对齐后的结构）：

| 层 | 内容 | 存储 |
|---|---|---|
| 全局 | 静音、帧率上限、缩放模式、自动静音、粒子/视差/鼠标 | `settings.json` 顶层 |
| 逐壁纸（我们的） | **音量** | `settings.json` 的 `volumes{壁纸ID: 值}` |
| 逐壁纸（作者定义） | 壁纸自己的可调项 | `settings.json` 的 `properties{壁纸ID: {...}}` |

音量之所以是逐壁纸的，是因为官方 Wallpaper Engine 就这么做（每张壁纸记住自己的
音量，静音则分全局/逐显示器/托盘三层）。`volume_default` 只作为没单独设过时的
兜底值。改这块时注意 `start-wallpaper.sh` 的 `build_flags()` 要同时兼容三种形态：
`volumes[wid]` / `volume_default` / 旧格式的全局 `volume`。

**待办，按价值排序**：

1. **多显示器**——目前只输出到 `LWE_SCREEN` 指定的单块屏。渲染器本身支持
   `--screen-root` 重复指定多块屏，理论上可以扩展。
2. **播放列表界面**——渲染器支持 `--playlist`（读 Wallpaper Engine 的
   `config.json`），界面还没有入口。
3. **修扩展的 `already disposed` 告警**——在 `gnome-extension/wallpaperManager.js`，
   属于能力范围内的清理工作。
4. **逐显示器保存壁纸属性**——官方支持（"Properties are now saved per-monitor too"），
   我们目前不分显示器。

### 已完成的后续改动

| 提交 | 内容 |
|---|---|
| `114660d` | 音量改为逐壁纸；顺带修了「编辑非当前壁纸会打断运行中的壁纸」 |
| `7cb26e0` | 暴露自动静音开关（对应渲染器的 `--noautomute`） |
| `979a1f4` | 帧率上限的说明跟着壁纸类型变（视频/场景/网页效果不同） |
| `1214d10` | 扫描逻辑抽成 `lwe_scan.py`，界面与命令行共用一份实现 |
| `6c91031` | 界面性能专项：缩略图双层缓存+异步解码（内存 Texture + `thumbs/` 磁盘层）、属性面板按壁纸 ID 缓存、设置写盘 400ms 防抖、启动先 present 再填数据 |
| `6827173` | 名称匹配改字面比较（`[4K]` 这类括号不再被当正则）；停止壁纸时撤掉登录自启（界面与 CLI 一致） |

## 发布流程

```bash
git tag -a v1.1.0 -m "说明"
git push origin main v1.1.0
./packaging/publish.sh        # 需 gh 已登录（gh auth login）
```

`publish.sh` 会自动构建 deb、创建 Release、上传附件、同步仓库描述与标签。
Release 已存在时改为覆盖上传附件。

如果 tag 之后又有非功能性改动（改文档、改仓库名），不想发新版本号：

```bash
VERSION=1.0.0 ./packaging/build-deb.sh
gh release upload v1.0.0 dist/*.deb --repo <owner>/<repo> --clobber
```

注意：**已发布的 tag 不要再移动**。仓库刚建、无使用者时移过一次可以接受，
有真实用户后就不行了。

## 本机开发环境

- 系统：Ubuntu 24.04 + GNOME Shell 46 + **Wayland** + NVIDIA RTX 4060（独显模式）
- 项目目录：`~/projects/wallpaper`
- 渲染器源码与构建产物：`~/linux-wallpaperengine`（外部依赖，**不要在这里改代码**）
- 运行时状态：`~/.cache/wallpaper-picker/`（壁纸清单、日志、pid、settings.json、缩略图缓存 thumbs/）
- Steam 库：`~/.steam/debian-installation`，42 张壁纸
  （26 场景 + 12 视频 + 3 网页 + 1 无法解析）
- 应用入口：`~/.local/share/applications/wallpaper-picker.desktop`
- 登录自启：`~/.config/autostart/wallpaper-engine.desktop`
- GNOME 扩展：`~/.local/share/gnome-shell/extensions/linux-wallpaperengine@github.io`
  → 软链到本仓库的 `gnome-extension/`
