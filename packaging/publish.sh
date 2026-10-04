#!/bin/bash
# 发布流程：构建 deb 并创建 GitHub Release，同时设置仓库描述与标签
#
# 用法:
#   ./packaging/publish.sh              # 用当前 git tag 作为版本号发布
#   ./packaging/publish.sh --dry-run    # 只检查，不做任何写操作
#
# 前置条件:
#   1. 已安装 GitHub CLI (gh) 并完成认证: gh auth login
#   2. 当前提交已打 tag（如 v1.0.0），且已推送到远端
#
# 说明: 推送代码本身用 SSH 即可，但创建 Release 和修改仓库描述要走
# GitHub API，必须有 gh 的登录凭据。

set -eu

PROJECT_DIR="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
REPO="Fitz8863/wallpaper-engine-gnome"
PACKAGE="wallpaper-engine-gnome"

REPO_DESCRIPTION="在 GNOME (Wayland) 上使用 Wallpaper Engine 创意工坊壁纸：GTK4 图形化选择器 + 命令行启动器 + 配套 GNOME Shell 扩展，绕过 Mutter 不支持 wlr-layer-shell 的限制。"
REPO_TOPICS="wallpaper-engine gnome wayland gtk4 libadwaita linux ubuntu dynamic-wallpaper"

DRY_RUN=""
[ "${1:-}" = "--dry-run" ] && DRY_RUN="1"

info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m[✓]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[✗]\033[0m %s\n' "$*" >&2; exit 1; }

cd "$PROJECT_DIR"

# ---- 版本号来自 git tag ----
TAG="$(git describe --tags --exact-match 2>/dev/null || true)"
[ -n "$TAG" ] || die "当前提交没有 tag。先执行: git tag v1.0.0 && git push origin v1.0.0"
VERSION="${TAG#v}"
info "发布版本: $VERSION (tag: $TAG)"

# ---- 检查工作区干净 ----
if [ -n "$(git status --porcelain)" ]; then
    die "工作区有未提交的改动，先提交再发布"
fi
ok "工作区干净"

# ---- 检查 gh 认证 ----
if ! command -v gh >/dev/null 2>&1; then
    die "未安装 gh。安装: sudo apt install gh"
fi
if ! gh auth status >/dev/null 2>&1; then
    die "gh 未登录。先执行: gh auth login
（创建 Release 和设置仓库描述需要 API 凭据，SSH 密钥不够）"
fi
ok "gh 已认证"

if [ -n "$DRY_RUN" ]; then
    info "dry-run 模式，以下操作不会真的执行："
    echo "  - 构建 $PACKAGE $VERSION 的 deb"
    echo "  - 创建 Release $TAG 并上传 deb"
    echo "  - 设置仓库描述：$REPO_DESCRIPTION"
    echo "  - 设置仓库标签：$REPO_TOPICS"
    exit 0
fi

# ---- 构建 ----
info "构建 deb"
rm -rf dist
./packaging/build-deb.sh >/dev/null
DEB="$(ls dist/*.deb)"
ok "产物: $DEB ($(du -h "$DEB" | cut -f1))"

# ---- 仓库描述与标签 ----
info "设置仓库描述"
gh repo edit "$REPO" --description "$REPO_DESCRIPTION" >/dev/null
ok "描述已更新"

info "设置仓库标签"
# shellcheck disable=SC2086
gh repo edit "$REPO" --add-topic $(echo "$REPO_TOPICS" | tr ' ' ',') >/dev/null
ok "标签已更新: $REPO_TOPICS"

# ---- 创建 Release ----
if gh release view "$TAG" --repo "$REPO" >/dev/null 2>&1; then
    info "Release $TAG 已存在，改为上传/覆盖附件"
    gh release upload "$TAG" "$DEB" --repo "$REPO" --clobber >/dev/null
    ok "附件已更新"
else
    info "创建 Release $TAG"
    gh release create "$TAG" "$DEB" \
        --repo "$REPO" \
        --title "$TAG" \
        --notes "$(cat <<EOF
## 安装

下载下方的 \`${PACKAGE}_${VERSION}_all.deb\`，然后：

\`\`\`bash
sudo apt install ./${PACKAGE}_${VERSION}_all.deb
\`\`\`

**首次安装后需要注销并重新登录一次**——Wayland 下 GNOME Shell 无法热加载新扩展。

## 注意：本包不含渲染器

壁纸的实际渲染由社区渲染器
[linux-wallpaperengine](https://github.com/Almamu/linux-wallpaperengine)
的 GNOME 分支负责，它需要另行编译（构建时会下载约 370MB 的 CEF）。
详见仓库 README 的安装章节。

## 本版本内容

- 图形化选择器：缩略图网格、搜索、按类型筛选、点击即切换
- 属性面板：自动读取每张壁纸自己的可调项并生成控件（开关/滑块/下拉/取色器）
- 播放设置：静音、音量、帧率上限、缩放模式、粒子/视差/鼠标交互
- 设置持久化，图形界面与命令行共用同一份配置
EOF
)" >/dev/null
    ok "Release 已创建: https://github.com/$REPO/releases/tag/$TAG"
fi

echo
ok "发布完成"
