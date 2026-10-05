## 安装

下载下方的 `wallpaper-engine-gnome_1.2.0_amd64_linux.deb` 附件（dpkg 不挑
文件名，以 `_amd64_linux` 结尾的纯 Python 包用 dpkg -i 安装没有问题），然后：

```bash
sudo dpkg -i ./wallpaper-engine-gnome_1.2.0_amd64_linux.deb
```

**首次安装后需要注销并重新登录一次**——Wayland 下 GNOME Shell 无法热加载新扩展。

## 注意：本包不含渲染器

壁纸的实际渲染由社区渲染器
[linux-wallpaperengine](https://github.com/Almamu/linux-wallpaperengine)
的 GNOME 分支负责，它需要另行编译（构建时会下载约 370MB 的 CEF）。
详见仓库 README 的安装章节。

## 本版本内容

- **支持非 Steam 来源的壁纸**：第三方下载、手动整理的壁纸，在
  「设置 → 壁纸来源」里指定目录即可，网格、属性面板、音量、开机恢复
  全部照常工作；没有 Steam 也不再需要碰环境变量
- **修复新版工坊壁纸的大块白贴图**：新版层脚本带 ES import 语句，渲染器
  压平脚本时漏剥离导致整层失效（如 2897629925 的音乐播放器区域），
  补丁已随安装脚本自动应用
- **修复切换壁纸时的日志告警刷屏**（`already disposed`）：扩展生命周期
  改为信号驱动，不再在已销毁对象上做清理
- **属性面板适配自定义目录壁纸**：修复其参数读取走错路径的问题
- 安装脚本现在能区分「补丁已应用」与「补丁和源码版本不匹配」，
  上游更新后不再静默跳过失效的补丁
