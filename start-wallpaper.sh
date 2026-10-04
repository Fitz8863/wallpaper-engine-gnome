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
# 可用环境变量覆盖：
#   LWE_BIN        渲染器二进制路径
#   LWE_SCREEN     显示器名（默认 eDP-1，多屏时改）
#   LWE_STATE_DIR  运行时状态目录（清单/日志/pid）

set -u

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"

BIN="${LWE_BIN:-$HOME/linux-wallpaperengine/build/output/linux-wallpaperengine}"
SCREEN="${LWE_SCREEN:-eDP-1}"
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
if cfg.get('silent'):
    flags.append('--silent')

volume = cfg.get('volume')
if isinstance(volume, (int, float)) and int(volume) != 15:
    flags += ['--volume', str(int(volume))]

fps = cfg.get('fps')
if isinstance(fps, (int, float)):
    flags += ['--fps', str(int(fps))]

scaling = cfg.get('scaling')
if scaling and scaling != 'default':
    flags += ['--scaling', str(scaling)]

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

# 在常见的 Steam 安装布局里找创意工坊目录
find_workshop() {
    local base
    for base in \
        "$HOME/.local/share/Steam/steamapps/workshop/content/431960" \
        "$HOME/.steam/steam/steamapps/workshop/content/431960" \
        "$HOME/.steam/debian-installation/steamapps/workshop/content/431960" \
        "$HOME/.var/app/com.valvesoftware.Steam/.local/share/Steam/steamapps/workshop/content/431960"
    do
        [ -d "$base" ] && { printf '%s\n' "$base"; return 0; }
    done
    return 1
}

# 重新扫描创意工坊目录刷新清单，这样新订阅的壁纸立刻可用
refresh_list() {
    local ws
    ws="$(find_workshop)" || { echo "找不到创意工坊目录，请确认 Steam 里已订阅壁纸" >&2; return 1; }
    python3 - "$ws" "$LIST" <<'PY'
import glob, json, os, sys

ws, out = sys.argv[1], sys.argv[2]
rows = []
for d in sorted(glob.glob(os.path.join(ws, '*/'))):
    pj = os.path.join(d, 'project.json')
    if not os.path.exists(pj):
        continue
    wid = os.path.basename(d.rstrip('/'))
    try:
        meta = json.load(open(pj, encoding='utf-8-sig'))
        rows.append((str(meta.get('type', '?')).lower(), wid,
                     str(meta.get('title', '?')).strip()))
    except Exception:
        rows.append(('error', wid, '(配置无法解析)'))

order = {'scene': 0, 'video': 1, 'web': 2}
rows.sort(key=lambda r: (order.get(r[0], 9), r[2]))
with open(out, 'w', encoding='utf-8') as fh:
    for wtype, wid, title in rows:
        fh.write(f'{wid}\t{wtype}\t{title}\n')
print(f'扫描到 {len(rows)} 张壁纸')
PY
}

case "${1:-}" in
    --list)
        refresh_list >&2
        column -t -s $'\t' "$LIST"
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
        [ "$stopped" = 1 ] && echo "已停止壁纸" || echo "没有正在运行的壁纸"
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
    BG="$(grep -i -- "$ARG" "$LIST" | head -1 | cut -f1)"
    if [ -z "$BG" ]; then
        echo "没找到匹配 '$ARG' 的壁纸，用 --list 看看有哪些" >&2
        exit 1
    fi
    echo "匹配到: $(grep -- "$BG" "$LIST" | cut -f3)"
fi

# 先停掉旧实例
pkill -f "linux-wallpaperengine.*--gnome" 2>/dev/null && sleep 1

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
sleep 3

if kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "壁纸已启动 (PID $(cat "$PIDFILE")，显示器 $SCREEN)"
    echo "日志: $LOG"
else
    echo "启动失败，日志末尾:" >&2
    tail -20 "$LOG" >&2
    exit 1
fi
