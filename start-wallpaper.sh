#!/bin/bash
# Wallpaper Engine 动态壁纸启动器 —— GNOME + Wayland
#
# 用法:
#   ./start-wallpaper.sh --list              列出所有可用壁纸
#   ./start-wallpaper.sh 3050160027          按 ID 启动
#   ./start-wallpaper.sh 芙莉莲               按名称关键词启动
#   ./start-wallpaper.sh --stop              停止当前壁纸
#   ./start-wallpaper.sh --status            查看运行状态
#
# 额外参数会透传给渲染器，例如限帧省电、关掉粒子:
#   ./start-wallpaper.sh 3050160027 -f 30
#   ./start-wallpaper.sh 3050160027 --disable-particles
#
# 前提: GNOME 扩展 linux-wallpaperengine@github.io 已启用（装完需注销重登一次）
#
# 可用环境变量覆盖（详见 README「目录与自动探测」）：
#   LWE_BIN        渲染器二进制路径
#   LWE_SCREEN     显示器名；不设则向 Mutter 查询主屏
#   LWE_WORKSHOP   创意工坊壁纸目录；不设则读 Steam 的 libraryfolders.vdf 自动找
#   LWE_STATE_DIR  运行时状态目录（清单/日志/pid）

set -u

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"

# 渲染器位置也由 wallpaper_picker/paths.py 决定（它认 LWE_BIN 环境变量）
if [ -f "$SCRIPT_DIR/wallpaper_picker/paths.py" ]; then
    BIN="$(python3 "$SCRIPT_DIR/wallpaper_picker/paths.py" renderer 2>/dev/null)"
fi
BIN="${BIN:-${LWE_BIN:-$HOME/linux-wallpaperengine/build/output/linux-wallpaperengine}}"

# 显示器名没有通用常量，向 Mutter 查当前主屏；查不到才回落到 eDP-1。
# 同样可以用 LWE_SCREEN 覆盖。
if [ -f "$SCRIPT_DIR/wallpaper_picker/paths.py" ]; then
    DETECTED_SCREEN="$(python3 "$SCRIPT_DIR/wallpaper_picker/paths.py" screen 2>/dev/null)"
fi
SCREEN="${LWE_SCREEN:-${DETECTED_SCREEN:-eDP-1}}"
STATE_DIR="${LWE_STATE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/wallpaper-picker}"
LIST="$STATE_DIR/wallpapers.txt"
LOG="$STATE_DIR/wallpaper.log"
PIDFILE="$STATE_DIR/wallpaper.pid"
SETTINGS="$STATE_DIR/settings.json"

mkdir -p "$STATE_DIR"

# 把 settings.json 翻译成渲染器参数。
# 图形化选择器改的就是这份配置，所以界面和命令行的行为保持一致。
build_flags() {
    local wid="$1"
    [ -f "$SETTINGS" ] || return 0
    python3 - "$SETTINGS" "$wid" <<'PY'
import json, sys

path, wid = sys.argv[1], sys.argv[2]
try:
    cfg = json.load(open(path, encoding='utf-8'))
except Exception:
    sys.exit(0)

flags = []
RENDERER_DEFAULT_VOLUME = 15
if cfg.get('silent'):
    flags.append('--silent')

# 音量是逐壁纸的。三种情况都要兼容：
#   volumes[wid]      当前格式
#   volume_default    当前格式的兜底值
#   volume            旧格式的全局音量（用户可能还没打开过新版界面）
volumes = cfg.get('volumes') or {}
if wid in volumes:
    volume = volumes[wid]
elif 'volume_default' in cfg:
    volume = cfg['volume_default']
else:
    volume = cfg.get('volume', RENDERER_DEFAULT_VOLUME)
if isinstance(volume, (int, float)) and int(volume) != RENDERER_DEFAULT_VOLUME:
    flags += ['--volume', str(int(volume))]

fps = cfg.get('fps')
if isinstance(fps, (int, float)):
    flags += ['--fps', str(int(fps))]

scaling = cfg.get('scaling')
if scaling and scaling != 'default':
    flags += ['--scaling', str(scaling)]

# 渲染器默认会在其他程序出声时自动静音壁纸；关掉这个行为要显式传参
if cfg.get('automute') is False:
    flags.append('--noautomute')

for key, disabled in (('particles', '--disable-particles'),
                      ('parallax', '--disable-parallax'),
                      ('mouse', '--disable-mouse')):
    if cfg.get(key) is False:
        flags.append(disabled)

for name, value in (cfg.get('properties') or {}).get(wid, {}).items():
    flags += ['--set-property', f'{name}={value}']

print('\n'.join(flags))
PY
}

# 在常见的 Steam 安装布局里找创意工坊目录。
# 探测逻辑放在 wallpaper_picker/paths.py 里（会读 libraryfolders.vdf，因此 Steam 库
# 装在别的硬盘上也能找到），与图形界面共用同一份实现，行为保持一致。
find_workshop() {
    python3 "$SCRIPT_DIR/wallpaper_picker/paths.py" workshop 2>/dev/null
}

# 重新扫描创意工坊目录刷新清单，这样新订阅的壁纸立刻可用。
# 扫描逻辑在 wallpaper_picker/scan.py（与图形界面共用同一份实现，排序和格式只维护一份）。
refresh_list() {
    local ws
    ws="$(find_workshop)" || { echo "找不到创意工坊目录，请确认 Steam 里已订阅壁纸" >&2; return 1; }
    python3 "$SCRIPT_DIR/wallpaper_picker/scan.py" "$ws" "$LIST" >&2
}

case "${1:-}" in
    --list)
        refresh_list >&2
        if command -v column >/dev/null 2>&1; then
            column -t -s $'\t' "$LIST"
        else
            cat "$LIST"   # 没有 column 就直接输出，不强求对齐
        fi
        exit 0
        ;;
    --stop)
        stopped=0
        if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
            kill "$(cat "$PIDFILE")" 2>/dev/null && stopped=1
        fi
        # pidfile 可能过期，再按进程名兜一次
        pkill -f "linux-wallpaperengine.*--gnome" 2>/dev/null && stopped=1
        rm -f "$PIDFILE"
        if [ "$stopped" = 1 ]; then
            # 停止即撤掉登录自启，与图形界面的「停止」按钮行为一致——
            # 用户明确要停，重启后壁纸不该自己回来。再选壁纸时会重建。
            rm -f "$HOME/.config/autostart/wallpaper-engine.desktop"
            echo "已停止壁纸"
        else
            echo "没有正在运行的壁纸"
        fi
        exit 0
        ;;
    --status)
        if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
            echo "运行中 (PID $(cat "$PIDFILE"))"
        else
            echo "未运行"
        fi
        exit 0
        ;;
    "" | --help | -h)
        sed -n '2,20p' "$0" | sed 's/^# \?//'
        exit 0
        ;;
esac

if [ ! -x "$BIN" ]; then
    echo "渲染器还没编译: $BIN" >&2
    echo "先跑 scripts/install.sh，或用 LWE_BIN 指定其他位置" >&2
    exit 1
fi

# 解析壁纸参数：纯数字当 ID，否则按标题模糊匹配
ARG="$1"; shift
if [[ "$ARG" =~ ^[0-9]+$ ]]; then
    BG="$ARG"
else
    refresh_list >&2
    # -F 按字面匹配：标题里常带 [4K] (汉化) 这类括号，当正则解释必错
    BG="$(grep -iF -- "$ARG" "$LIST" | head -1 | cut -f1)"
    if [ -z "$BG" ]; then
        echo "没找到匹配 '$ARG' 的壁纸，用 --list 看看有哪些" >&2
        exit 1
    fi
    echo "匹配到: $(grep -- "$BG" "$LIST" | cut -f3)"
fi

# 先停掉旧实例。轮询等它真的退出（通常几十毫秒），而不是固定 sleep 1——
# 固定等待会让切换壁纸白等一秒，图形界面调用时尤其明显。
pkill -f "linux-wallpaperengine.*--gnome" 2>/dev/null
for _ in $(seq 1 20); do
    pgrep -f "linux-wallpaperengine.*--gnome" >/dev/null 2>&1 || break
    sleep 0.05
done

# 从设置文件生成参数；命令行透传的参数放最后，可以覆盖设置里的值
mapfile -t FLAGS < <(build_flags "$BG")

nohup "$BIN" \
    --gnome \
    --screen-root "$SCREEN" \
    --bg "$BG" \
    ${FLAGS[@]+"${FLAGS[@]}"} \
    "$@" \
    > "$LOG" 2>&1 &

echo $! > "$PIDFILE"

# 轮询确认进程活着。只要熬过 STARTUP_GRACE 就算启动成功，
# 期间进程死掉则立刻报错——比原来的固定 sleep 3 快得多。
STARTUP_GRACE=6   # 单位 0.1 秒，即 0.6 秒
alive=0
for _ in $(seq 1 "$STARTUP_GRACE"); do
    sleep 0.1
    if ! kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
        alive=-1
        break
    fi
    alive=1
done

if [ "$alive" = 1 ]; then
    echo "壁纸已启动 (PID $(cat "$PIDFILE")，显示器 $SCREEN)"
    echo "日志: $LOG"
else
    echo "启动失败，日志末尾:" >&2
    tail -20 "$LOG" >&2
    exit 1
fi
