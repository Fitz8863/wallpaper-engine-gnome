# 多显示器支持 —— 调研与实施方案

> 状态:**方案已定,等待外接显示器后实施**。调研基于本机代码(2026-10-05),
> 所有"渲染器会怎样"均来自渲染器源码与帮助文本,标注"待验证"的必须真机确认。
> 前置结论:渲染器与扩展端都已就绪,改动全部落在我们自己这层。

---

## 三端现状

### 渲染器(外部依赖,零改动)

`--screen-root` 可以重复指定,单进程管理多块屏。官方帮助文本(`ApplicationContext.cpp:657-664`)自带的例子:

```bash
# 两屏不同壁纸:--bg 跟随它前面的 --screen-root
linux-wallpaperengine --screen-root HDMI-1 --bg 2317494988 --screen-root HDMI-2 --bg 1108150151

# 两屏同一壁纸(位置参数同时挂给两个 screen-root)
linux-wallpaperengine --screen-root HDMI-1 --screen-root HDMI-2 2317494988
```

关键语义:`--scaling`、`--clamp` 等"applies to the **previous** --screen-root"——
这些参数本身就是逐屏的,将来做逐屏缩放不用改架构。

### GNOME 扩展(基本零改动,已就绪)

`wallpaperManager.js` 里每个背景 actor 绑定一个 `_monitorIndex`,匹配渲染窗口的
顺序(`_getSource`,约 156-229 行):

1. `meta_window.get_monitor()` —— 窗口落在哪块屏;
2. 解析窗口标题 JSON 的 `"monitor"` connector 名,解析到对应屏索引;
3. 兜底:只有一个渲染窗口且只有一块屏时直接用(多屏时不会走到)。

即两个渲染窗口同时存在时,扩展按 connector 名各自认领,天然支持多屏。
待验证:两个窗口标题的 `"monitor"` 字段确实各带各的 connector(从
`WaylandOutputViewport::buildWindowTitle()` 每 viewport 一份窗口推断,应当如此)。

### 我们这端(全部改动所在)

单屏假设清单:

| 位置 | 现状 | 改法 |
|---|---|---|
| `paths.py:135` `find_screen()` | 只返回主屏 connector | 加 `find_screens()` 返回全部(Mutter GetCurrentState 的 logical_monitors 本来就是逐屏的) |
| `start-wallpaper.sh:38,196` | `SCREEN` 单值、`--screen-root` 单次 | 支持 `--screen`(可重复)与 `--all-screens` |
| `start-wallpaper.sh` pidfile | 单 pid | **不用改**——渲染器多屏仍是单进程 |
| `start-wallpaper.sh --stop` | pkill 按进程名 | 不用改,单进程全杀 |
| `settings.json` | `last` 单壁纸、无屏概念 | 加 `screens` 映射与 `clone`(见下) |
| `picker.py:459` `running_wallpaper_id()` | 只取 cmdline 里一个 `--bg` | 改为收集全部 `--bg`(单屏时行为不变) |
| `picker.py` 网格高亮/badge | 单一"使用中" | 多壁纸并行高亮 |
| `picker.py` `restore_last()` / 自启 | 恢复一张 `last` | 按 `screens` 逐屏恢复 |

## 目标设计

### settings.json(新增两键,旧值保留)

```json
{
  "last": "3050160027",
  "clone": false,
  "screens": { "HDMI-1": "2317494988", "eDP-1": "3050160027" },
  "volumes": { "...": "逐壁纸,不变" },
  "properties": { "...": "逐壁纸,不变" }
}
```

- `screens`:connector → 壁纸 ID。**音量/属性仍按壁纸 ID**,两屏同壁纸天然共享
  设置(与官方 WE 一致),避免"逐屏 × 逐壁纸"的组合爆炸。
- `last` 保留:作为主屏兜底与旧版兼容;单屏用户永远不会碰 `screens`,
  行为与现在完全一致。
- `clone`:true 时所有屏用主屏那张壁纸,启动器循环拼 `--screen-root` 传同一 bg。
  UI 上是一个开关,比让用户挨个屏设同一张友好得多。
- 逐屏 fps/scaling 暂不做(阶段 2,有真需求再说)。

### CLI

```bash
wallpaper-engine-start 3050160027            # 现行为不变:主屏
wallpaper-engine-start --all-screens 3050160027   # 所有屏同一张(clone)
wallpaper-engine-start --screen HDMI-1 --screen DP-1 2317494988 3050160027
                                             # 逐屏指定,顺序对应
```

### UI(保持现有单屏流程为主)

- 侧栏状态区列出每块屏及各自当前壁纸;
- 多屏时「应用」按钮旁给"此屏/所有屏"选择(默认所有屏 = clone 开关联动);
- 设置对话框加"显示器"区块:每行一块屏(名称 + 当前壁纸 + 更改按钮)。
  第一版宁可窄,主屏流程不增加任何步骤。

## 分期计划

### 阶段 0 —— 单屏机器上就能做、能测(可以先做)

只做"参数化",不改行为;单屏机器上 `find_screens()` 返回一块屏,一切与现状相同:

1. `paths.py`:`find_screens()` + `screens` 子命令;把 Mutter 返回值的解析拆成
   纯函数(喂构造数据),进 pytest;
2. `start-wallpaper.sh`:`--screen`(可重复)、`--all-screens`;展开逻辑放 Python
   侧保证可单测,shell 只拼接;
3. `settings.json` 结构与 `load_settings()` 迁移逻辑(旧文件没有新键 = 单屏现状);
4. `running_wallpaper_id()` → `running_wallpaper_ids()`(收集全部 --bg);
5. 上述全部配单测。

风险极低:单屏环境可完整回归。

### 阶段 1 —— 需要外接显示器(真机)

settings 写入 `screens`、UI 屏选择器、逐屏高亮、`--restore` 多屏恢复、
网格 badge(「使用中 · 屏2」)。

### 阶段 2 —— 可选

逐屏 fps/scaling/属性;拔插屏的热响应(监听 Mutter monitors-changed 后重挂)。

## 真机验证清单(阶段 1 开工前过一遍)

1. `python3 wallpaper_picker/paths.py screens` 双屏输出正确,主屏标记正确;
2. 渲染器单进程多屏启动后,两个窗口标题 JSON 的 `"monitor"` 各带各 connector
   (`WAYLAND_DEBUG=client` 或扩展日志确认);
3. `journalctl --user -b | grep lwpe` 应看到两次 `matched renderer by connector`;
4. 两屏不同壁纸:画面各归各屏,不串;交换两屏壁纸再试(防索引巧合);
5. clone 模式:两屏画面一致;**音频是否双份**(若双份,查渲染器是否支持逐屏
   --volume,不行就列为上游 issue;官方 WE 克隆时只有一份声音);
6. 混合分辨率(如 2560x1600 + 1080p):`patches/0001` 全屏补丁两屏都生效,
   scaling 模式逐屏正确;
7. 登录自启 `--restore` 恢复两屏各自的壁纸;
8. 睡眠唤醒、断开/重接外接屏后的表现(扩展是否重新认领);
9. 拔掉外接屏后回到单屏:`screens` 里的失效键不致命,回落主屏并提示。

## 已知风险

- **connector 名漂移**:外接屏换接口/换屏后 HDMI-1 可能变 HDMI-2,`screens`
  键失配。缓解:失配时回落主屏并 toast 提示,不静默用错。官方 WE 按显示器
  识别同样有此问题,不算倒退。
- **clone = 两份独立渲染**:同一壁纸每屏各渲染一遍,功耗近似翻倍(对照
  HANDOFF「量性能要用功耗」一条的测法)。追求省电的用户应只用单屏。
  官方 WE 的 Clone 是一份内容跨屏贴图,渲染器目前没有这个概念——若实测
  功耗不可接受,再考虑上游方案,不在本仓库修。
- **多屏时窗口匹配时序**:两个渲染窗口出现先后不定,扩展的匹配重试逻辑
  (`retrying...` 日志那条路径)对两个窗口是否都收敛,待验证。
