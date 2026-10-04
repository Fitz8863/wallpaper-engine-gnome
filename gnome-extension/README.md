# GNOME Shell 扩展（来源说明）

这里的三个文件 **不是本项目原创**，是下面这条依赖链上的第三方代码，为便于开发调试而附带在本仓库中：

```
Almamu/linux-wallpaperengine  (GPL-3.0，渲染器主体)
        └── kv9898/linux-wallpaperengine  gnome 分支  (GPL-3.0，增加了 --gnome 模式)
                └── gnome-extension/  ← 本目录的文件来自这里
```

- 上游仓库：https://github.com/kv9898/linux-wallpaperengine （分支 `gnome`）
- 原始路径：仓库根目录下的 `gnome-extension/`
- 许可证：**GPL-3.0**（继承自 Almamu/linux-wallpaperengine）

## 这个扩展做什么

GNOME 的 Mutter 合成器不实现 `wlr-layer-shell` 协议，所以渲染器无法像在 wlroots 系合成器上那样直接把自己铺到桌面背景层。这个扩展绕开该限制：

1. 渲染器用普通 xdg-shell 窗口渲染，并把元数据编码进窗口标题
   （前缀 `@linux-wallpaperengine!` + 一段 JSON，含显示器名、尺寸、位置）
2. 扩展识别带该前缀的窗口，用 `Clutter.Clone` 把窗口内容克隆进 GNOME Shell 的桌面背景层
3. 原窗口从 Alt+Tab、概览、任务栏里隐藏

## 为什么放在这里

一是方便对照调试（排查问题时需要同时看渲染器和扩展的行为），二是如果后续要修复扩展自身的缺陷（例如切换壁纸时控制台会刷 `.LWPELiveWallpaper has been already disposed` 警告），可以直接在本仓库里改，改完再把软链指过来。

**如果只是使用本项目、不打算改扩展**，可以删掉本目录，改用 `install.sh` 从上游拉取。
