#!/bin/bash
# 一键安装脚本 —— 把本方案部署到当前用户环境
#
# 用法:
#   ./install.sh              执行全部步骤（已完成的会自动跳过）
#   ./install.sh --deps       只装系统依赖
#   ./install.sh --renderer   只编译渲染器
#   ./install.sh --extension  只安装 GNOME 扩展
#   ./install.sh --desktop    只安装应用入口
#
# 环境变量:
#   RENDERER_DIR   渲染器源码/构建目录（默认 ~/linux-wallpaperengine）
#
# 注意：本脚本不调用任何操作 Windows 分区的命令。

set -u

PROJECT_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
RENDERER_DIR="${RENDERER_DIR:-$HOME/linux-wallpaperengine}"
RENDERER_REPO="https://github.com/kv9898/linux-wallpaperengine.git"
RENDERER_BRANCH="gnome"
EXT_UUID="linux-wallpaperengine@github.io"
EXT_DIR="$HOME/.local/share/gnome-shell/extensions/$EXT_UUID"
APPS_DIR="$HOME/.local/share/applications"

STEP="${1:-all}"
[ "$STEP" = "--help" ] || [ "$STEP" = "-h" ] && {
    sed -n '2,17p' "$0" | sed 's/^# \?//'
    exit 0
}

info()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn()  { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }
ok()    { printf '\033[1;32m[✓]\033[0m %s\n' "$*"; }
die()   { printf '\033[1;31m[✗]\033[0m %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- 系统依赖

APT_PACKAGES=(
    # 渲染器构建
    build-essential cmake git pkg-config
    libxrandr-dev libxinerama-dev libxcursor-dev libxi-dev libgl-dev
    libglew-dev freeglut3-dev libsdl2-dev liblz4-dev libglm-dev libglfw3-dev
    libavcodec-dev libavformat-dev libavutil-dev libswscale-dev
    libxxf86vm-dev libmpv-dev libmpv2 mpv
    libpulse-dev libpulse0 libfftw3-dev libfreetype-dev
    # Wayland 支持（--gnome 模式必需）
    libwayland-dev wayland-protocols libxkbcommon-dev libegl-dev libgles-dev
    extra-cmake-modules ninja-build
    # 选择器界面
    python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-graphene-1.0
    # 视频壁纸分辨率探测（GStreamer Discoverer）
    gir1.2-gst-plugins-base-1.0
    # 其他（column 命令用于对齐 --list 的输出）
    bsdextrautils
)

install_deps() {
    info "检查系统依赖"
    local missing=()
    for pkg in "${APT_PACKAGES[@]}"; do
        dpkg -s "$pkg" >/dev/null 2>&1 || missing+=("$pkg")
    done

    if [ ${#missing[@]} -eq 0 ]; then
        ok "依赖已齐全"
        return 0
    fi

    warn "缺少 ${#missing[@]} 个包：${missing[*]}"
    echo
    read -r -p "现在用 apt 安装吗？需要输入 sudo 密码 [y/N] " reply
    [[ "$reply" =~ ^[Yy]$ ]] || die "已取消。请手动安装上述包后重试"

    sudo apt-get update || warn "apt update 失败，继续尝试安装"
    sudo apt-get install -y "${missing[@]}" || die "依赖安装失败"
    ok "依赖安装完成"
}

# ---------------------------------------------------------------- 渲染器

install_renderer() {
    info "准备渲染器（GNOME 分支 fork）"

    if [ ! -d "$RENDERER_DIR/.git" ]; then
        info "克隆 $RENDERER_REPO (分支 $RENDERER_BRANCH)"
        git clone --recurse-submodules -b "$RENDERER_BRANCH" \
            "$RENDERER_REPO" "$RENDERER_DIR" || die "克隆失败"
    else
        ok "源码已存在: $RENDERER_DIR"
    fi

    info "同步子模块"
    git -C "$RENDERER_DIR" submodule update --init --recursive \
        || warn "部分子模块拉取失败（网络原因），可重试"

    # 应用本地补丁（patches/ 下）。补丁已应用或与上游版本不匹配时自动跳过。
    # 0001 修的是：Mutter 会把 gnome 模式的窗口约束在工作区内
    # （2560 屏上只给 2464x1546），扩展克隆拉伸后壁纸整体发虚。
    for patch in "$PROJECT_DIR"/patches/*.patch; do
        [ -e "$patch" ] || continue
        if git -C "$RENDERER_DIR" apply --check "$patch" 2>/dev/null; then
            git -C "$RENDERER_DIR" apply "$patch" \
                && ok "已应用渲染器补丁: $(basename "$patch")" \
                || warn "渲染器补丁应用失败: $(basename "$patch")"
        else
            ok "渲染器补丁已应用，跳过: $(basename "$patch")"
        fi
    done

    local bin="$RENDERER_DIR/build/output/linux-wallpaperengine"
    if [ -x "$bin" ]; then
        ok "渲染器已编译: $bin"
        return 0
    fi

    info "配置并编译（首次需下载约 370MB 的 CEF，耗时较长）"
    mkdir -p "$RENDERER_DIR/build"
    ( cd "$RENDERER_DIR/build" \
        && cmake -DCMAKE_BUILD_TYPE=Release .. \
        && make -j"$(nproc)" ) || die "编译失败，详见 $RENDERER_DIR/build"

    ok "编译完成: $bin"
}

# ---------------------------------------------------------------- 扩展

install_extension() {
    info "安装 GNOME Shell 扩展"

    local src="$PROJECT_DIR/gnome-extension"
    [ -f "$src/metadata.json" ] || die "找不到扩展源码: $src"

    mkdir -p "$(dirname "$EXT_DIR")"
    if [ -L "$EXT_DIR" ] || [ -e "$EXT_DIR" ]; then
        rm -rf "$EXT_DIR"
    fi
    ln -s "$src" "$EXT_DIR"
    ok "已链接: $EXT_DIR -> $src"

    if command -v gnome-extensions >/dev/null 2>&1; then
        if gnome-extensions info "$EXT_UUID" >/dev/null 2>&1; then
            gnome-extensions enable "$EXT_UUID" 2>/dev/null \
                && ok "扩展已启用" \
                || warn "启用失败，请注销重登后再执行: gnome-extensions enable $EXT_UUID"
        else
            warn "GNOME Shell 尚未识别该扩展——注销重登后才会加载"
        fi
    fi

    # 顺便把扩展写进 dconf 的启用列表，重登后自动生效
    if command -v gsettings >/dev/null 2>&1; then
        python3 - "$EXT_UUID" <<'PY'
import ast, subprocess, sys
uuid = sys.argv[1]
cur = subprocess.run(['gsettings', 'get', 'org.gnome.shell', 'enabled-extensions'],
                     capture_output=True, text=True).stdout.strip()
try:
    lst = ast.literal_eval(cur) if cur else []
except Exception:
    lst = []
if uuid not in lst:
    lst.append(uuid)
    subprocess.run(['gsettings', 'set', 'org.gnome.shell', 'enabled-extensions',
                    str(lst)], check=False)
    print(f'已把 {uuid} 加入启用列表')
PY
    fi
}

# ---------------------------------------------------------------- 桌面入口

install_desktop() {
    info "安装应用入口"
    mkdir -p "$APPS_DIR"

    # 先删再建，而不是原地覆盖。
    # GNOME Shell 会缓存 .desktop 的内容，原地改写不一定能让它的缓存失效
    # （表现为点了图标没反应，日志里报旧路径不存在）。删除+新建会产生
    # 目录级的文件系统事件，缓存才会刷新。
    local target="$APPS_DIR/io.github.fitz.WallpaperPicker.desktop"
    rm -f "$target"
    # 清掉旧版的桌面文件名（改用应用 ID 命名前的遗留），避免应用列表出现两个入口
    rm -f "$APPS_DIR/wallpaper-picker.desktop"
    sed "s|@PROJECT_DIR@|$PROJECT_DIR|g" \
        "$PROJECT_DIR/desktop/io.github.fitz.WallpaperPicker.desktop" > "$target"
    chmod +x "$target"
    ok "已安装: $target"

    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "$APPS_DIR" 2>/dev/null || true
    fi

    # 应用图标装进用户的 hicolor 主题，应用列表 / 窗口 / Alt+Tab 才有专属图标
    local icon_name="io.github.fitz.WallpaperPicker"
    if [ -d "$PROJECT_DIR/icons/hicolor" ]; then
        mkdir -p "$HOME/.local/share/icons/hicolor"
        cp -R "$PROJECT_DIR/icons/hicolor/." "$HOME/.local/share/icons/hicolor/"
        if [ -f "$PROJECT_DIR/icons/$icon_name.svg" ]; then
            mkdir -p "$HOME/.local/share/icons/hicolor/scalable/apps"
            cp "$PROJECT_DIR/icons/$icon_name.svg" \
               "$HOME/.local/share/icons/hicolor/scalable/apps/"
        fi
        if command -v gtk-update-icon-cache >/dev/null 2>&1; then
            gtk-update-icon-cache -f "$HOME/.local/share/icons/hicolor" \
                2>/dev/null || true
        fi
        ok "已安装应用图标"
    fi

    chmod +x "$PROJECT_DIR/wallpaper-picker.py" "$PROJECT_DIR/start-wallpaper.sh"
    ok "脚本已可执行"
}

# ---------------------------------------------------------------- 主流程

case "$STEP" in
    --deps)      install_deps ;;
    --renderer)  install_renderer ;;
    --extension) install_extension ;;
    --desktop)   install_desktop ;;
    all)
        install_deps
        echo
        install_renderer
        echo
        install_extension
        echo
        install_desktop
        echo
        ok "安装完成"
        echo
        echo "接下来："
        echo "  1. 如果是第一次安装扩展，注销后重新登录一次（Wayland 下扩展无法热加载）"
        echo "  2. 打开「壁纸选择器」，或在终端运行:"
        echo "       $PROJECT_DIR/start-wallpaper.sh --list"
        echo "       $PROJECT_DIR/start-wallpaper.sh <壁纸ID或名称>"
        echo
        echo "多显示器用户请用下面的命令查出显示器名，再设置 LWE_SCREEN:"
        echo "  gdbus call --session --dest org.gnome.Mutter.DisplayConfig \\"
        echo "    --object-path /org/gnome/Mutter/DisplayConfig \\"
        echo "    --method org.gnome.Mutter.DisplayConfig.GetResources | grep -oP \"'[A-Za-z0-9-]+'\""
        ;;
    *)
        die "未知参数: $STEP（用 --help 查看用法）"
        ;;
esac
