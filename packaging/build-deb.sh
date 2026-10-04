#!/bin/bash
# 构建 Debian 安装包（只打包本项目自己这一层：界面 + 脚本 + GNOME 扩展）
#
# 用法:
#   ./packaging/build-deb.sh            # 产物在 dist/
#   ./packaging/build-deb.sh --install  # 构建后直接安装
#
# 为什么渲染器不打包进来：它必须从源码编译，构建时要下载 354MB 的 CEF，
# 产物 1.5GB（光 libcef.so 就 1.3GB）。塞进 deb 会让包大到没人愿意下载，
# 而且构建期联网抓文件违反 Debian 政策，官方归档不会收。
# 渲染器由 install.sh 单独编译，见 README。

set -eu

PROJECT_DIR="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
PACKAGE="wallpaper-engine-gnome"
# Debian 要求版本号以数字开头，所以没有 tag 时不能直接用提交哈希。
# 可用 VERSION=1.0.0 显式指定——例如 tag 之后只有非功能性改动，
# 想重建同一个版本的包（重命名仓库、改文档之类）。
BASE_VERSION="1.0.0"
if [ -n "${VERSION:-}" ]; then
    : # 沿用调用方指定的版本号
elif TAG="$(cd "$PROJECT_DIR" && git describe --tags --exact-match 2>/dev/null)"; then
    VERSION="${TAG#v}"
elif HASH="$(cd "$PROJECT_DIR" && git rev-parse --short HEAD 2>/dev/null)"; then
    VERSION="${BASE_VERSION}+git$(date +%Y%m%d).${HASH}"
else
    VERSION="$BASE_VERSION"
fi
ARCH="all"
# 发布前改成你的仓库地址（也可以用环境变量覆盖）
HOMEPAGE="${HOMEPAGE:-https://github.com/Fitz8863/wallpaper-engine-gnome}"
STAGE="$(mktemp -d)"
OUT="$PROJECT_DIR/dist"
INSTALL_AFTER="${1:-}"

cleanup() { rm -rf "$STAGE"; }
trap cleanup EXIT

echo "==> 打包 $PACKAGE $VERSION ($ARCH)"
PKG="$STAGE/${PACKAGE}_${VERSION}_${ARCH}"
mkdir -p "$PKG/DEBIAN" "$PKG/usr/bin" "$PKG/usr/lib/$PACKAGE" \
         "$PKG/usr/share/applications" \
         "$PKG/usr/share/gnome-shell/extensions/linux-wallpaperengine@github.io" \
         "$PKG/usr/share/doc/$PACKAGE"

# ---- 程序本体 ----
install -m 755 "$PROJECT_DIR/wallpaper-picker.py" "$PKG/usr/lib/$PACKAGE/"
install -m 755 "$PROJECT_DIR/start-wallpaper.sh"  "$PKG/usr/lib/$PACKAGE/"
install -m 755 "$PROJECT_DIR/lwe_paths.py"        "$PKG/usr/lib/$PACKAGE/"
install -m 755 "$PROJECT_DIR/lwe_scan.py"         "$PKG/usr/lib/$PACKAGE/"
install -m 755 "$PROJECT_DIR/packaging/enable-extension.py" "$PKG/usr/lib/$PACKAGE/"

# 命令行入口
cat > "$PKG/usr/bin/wallpaper-picker" <<EOF
#!/bin/sh
exec /usr/bin/python3 /usr/lib/$PACKAGE/wallpaper-picker.py "\$@"
EOF
cat > "$PKG/usr/bin/wallpaper-engine-start" <<EOF
#!/bin/sh
exec /usr/lib/$PACKAGE/start-wallpaper.sh "\$@"
EOF
chmod 755 "$PKG/usr/bin/wallpaper-picker" "$PKG/usr/bin/wallpaper-engine-start"

# ---- GNOME 扩展 ----
install -m 644 "$PROJECT_DIR/gnome-extension/extension.js" \
               "$PROJECT_DIR/gnome-extension/wallpaperManager.js" \
               "$PROJECT_DIR/gnome-extension/metadata.json" \
               "$PKG/usr/share/gnome-shell/extensions/linux-wallpaperengine@github.io/"

# ---- 桌面入口 ----
sed "s|@PROJECT_DIR@|/usr/lib/$PACKAGE|g" \
    "$PROJECT_DIR/desktop/wallpaper-picker.desktop" \
    > "$PKG/usr/share/applications/wallpaper-picker.desktop"
chmod 644 "$PKG/usr/share/applications/wallpaper-picker.desktop"

# ---- 文档 ----
install -m 644 "$PROJECT_DIR/README.md" "$PKG/usr/share/doc/$PACKAGE/README.md"
install -m 644 "$PROJECT_DIR/LICENSE"   "$PKG/usr/share/doc/$PACKAGE/copyright"
gzip -9n -c "$PROJECT_DIR/README.md" > "$PKG/usr/share/doc/$PACKAGE/README.md.gz"
chmod 644 "$PKG/usr/share/doc/$PACKAGE/README.md.gz"

# ---- control ----
SIZE="$(du -sk "$PKG" | cut -f1)"
cat > "$PKG/DEBIAN/control" <<EOF
Package: $PACKAGE
Version: $VERSION
Architecture: $ARCH
Maintainer: Fitz <13725071087@163.com>
Installed-Size: $SIZE
Depends: python3 (>= 3.10), python3-gi, gir1.2-gtk-4.0 (>= 4.10), gir1.2-adw-1,
 gir1.2-graphene-1.0, gir1.2-gdkpixbuf-2.0, gir1.2-pango-1.0, bsdextrautils
Recommends: gnome-shell (>= 45)
Section: utils
Priority: optional
Homepage: $HOMEPAGE
Description: Wallpaper Engine 动态壁纸的 GNOME Wayland 图形化选择器
 在 GNOME（Wayland 会话）上使用 Wallpaper Engine 创意工坊壁纸。
 .
 包含 GTK4 图形化选择器、命令行启动器和配套 GNOME Shell 扩展。
 壁纸渲染依赖社区渲染器 linux-wallpaperengine 的 GNOME 分支，
 需另行编译安装（见 README），本包不包含它。
EOF

# ---- 安装后脚本：接上扩展 ----
cat > "$PKG/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e

# 把扩展加入「发起安装的那个用户」的启用列表。
# 只在能确定用户身份时动手，否则留一句提示，不擅自改别人的配置。
HELPER=/usr/lib/wallpaper-engine-gnome/enable-extension.py
if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
    if command -v sudo >/dev/null 2>&1; then
        sudo -u "$SUDO_USER" python3 "$HELPER" || true
    else
        su "$SUDO_USER" -c "python3 $HELPER" || true
    fi
fi

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database -q /usr/share/applications || true
fi

echo "wallpaper-engine-gnome 已安装。"
echo "若这是首次安装，请注销后重新登录一次，GNOME Shell 才会加载扩展。"
EOF

cat > "$PKG/DEBIAN/prerm" <<'EOF'
#!/bin/sh
set -e
HELPER=/usr/lib/wallpaper-engine-gnome/enable-extension.py
if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ] && [ -f "$HELPER" ]; then
    if command -v sudo >/dev/null 2>&1; then
        sudo -u "$SUDO_USER" python3 "$HELPER" --disable || true
    else
        su "$SUDO_USER" -c "python3 $HELPER --disable" || true
    fi
fi
EOF

chmod 755 "$PKG/DEBIAN/postinst" "$PKG/DEBIAN/prerm"

# ---- 打包 ----
mkdir -p "$OUT"
DEB="$OUT/${PACKAGE}_${VERSION}_${ARCH}.deb"
dpkg-deb --root-owner-group --build "$PKG" "$DEB" >/dev/null
echo "==> 产物: $DEB ($(du -h "$DEB" | cut -f1))"

if [ "$INSTALL_AFTER" = "--install" ]; then
    echo "==> 安装"
    sudo dpkg -i "$DEB" || sudo apt-get install -f -y
fi
