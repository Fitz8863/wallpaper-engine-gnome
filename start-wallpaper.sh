#!/bin/bash
# Wallpaper Engine 动态壁纸启动器 —— GNOME + Wayland
#
# 用法:
#   ./start-wallpaper.sh --list              列出所有可用壁纸
#   ./start-wallpaper.sh 3050160027          按 ID 启动（应用到主屏，
#                                            其余屏按设置里的多屏意图）
#   ./start-wallpaper.sh 芙莉莲               按名称关键词启动
#   ./start-wallpaper.sh --all-screens ID    克隆到全部已连接的显示器
#   ./start-wallpaper.sh --screen HDMI-1 ID1 --screen eDP-1 ID2
#                                            逐屏指定（可只给部分屏）
#   ./start-wallpaper.sh --stop              停止当前壁纸
#   ./start-wallpaper.sh --status            查看运行状态
#
# 额外参数会透传给渲染器，例如限帧省电、关掉粒子:
#   ./start-wallpaper.sh 3050160027 -f 30
#   ./start-wallpaper.sh 3050160027 --disable-particles
#
# 启动前会自动启用并确认 GNOME 扩展 linux-wallpaperengine@github.io 已 ACTIVE
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

# LWE_SCREEN 只在多屏计划失效回落单屏时作为主屏名使用（launch 通常覆盖它）
SCREEN="${LWE_SCREEN:-$(python3 "$SCRIPT_DIR/wallpaper_picker/paths.py" screen 2>/dev/null || echo eDP-1)}"
STATE_DIR="${LWE_STATE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/wallpaper-picker}"
EXT_UUID="linux-wallpaperengine@github.io"
LIST="$STATE_DIR/wallpapers.txt"
LOG="$STATE_DIR/wallpaper.log"
PIDFILE="$STATE_DIR/wallpaper.pid"
SETTINGS="$STATE_DIR/settings.json"
DAEMON_PIDFILE="$STATE_DIR/suspend-daemon.pid"
STOP_MARKER="$STATE_DIR/wallpaper-stop"

mkdir -p "$STATE_DIR"

# 只识别当前启动器记录的渲染器。不能用全局 pkill：LWE_BIN 可以是任意
# 可执行文件名，而且同一台机器上可能还有别的 Wallpaper Engine 实例。
renderer_pid_matches() {
    local pid="$1" arg has_gnome=0 has_bin=0 expected exe
    [ -r "/proc/$pid/cmdline" ] || return 1
    while IFS= read -r -d '' arg; do
        [ "$arg" = "--gnome" ] && has_gnome=1
        [ "$arg" = "$BIN" ] && has_bin=1
    done < "/proc/$pid/cmdline"
    [ "$has_gnome" = 1 ] || return 1
    if [ "$has_bin" = 1 ]; then
        return 0
    fi
    exe="$(readlink -f "/proc/$pid/exe" 2>/dev/null || true)"
    expected="$(readlink -f "$BIN" 2>/dev/null || printf '%s' "$BIN")"
    [ -n "$exe" ] && [ "$exe" = "$expected" ]
}

renderer_pids() {
    local proc pid
    for proc in /proc/[0-9]*; do
        pid="${proc##*/}"
        if renderer_pid_matches "$pid"; then
            printf '%s\n' "$pid"
        fi
    done
}

stop_renderers() {
    local stopped=0 pid
    while read -r pid; do
        [ -n "$pid" ] || continue
        kill "$pid" 2>/dev/null && stopped=1 || true
    done < <(renderer_pids)
    sleep 0.2
    while read -r pid; do
        [ -n "$pid" ] || continue
        kill -9 "$pid" 2>/dev/null && stopped=1 || true
    done < <(renderer_pids)
    [ "$stopped" = 1 ]
}

daemon_pid_matches() {
    local pid="$1" arg
    [ -r "/proc/$pid/cmdline" ] || return 1
    while IFS= read -r -d '' arg; do
        [ "$arg" = "$SCRIPT_DIR/wallpaper_picker/suspend_daemon.py" ] && return 0
        case "$arg" in
            */suspend_daemon.py) return 0;;
        esac
    done < "/proc/$pid/cmdline"
    return 1
}

stop_daemon() {
    local pid stopped=0
    if [ -f "$DAEMON_PIDFILE" ]; then
        pid="$(cat "$DAEMON_PIDFILE" 2>/dev/null || true)"
        if [[ "$pid" =~ ^[0-9]+$ ]] && daemon_pid_matches "$pid"; then
            kill "$pid" 2>/dev/null && stopped=1 || true
            for _ in $(seq 1 20); do
                kill -0 "$pid" 2>/dev/null || break
                sleep 0.05
            done
            if kill -0 "$pid" 2>/dev/null; then
                kill -9 "$pid" 2>/dev/null || true
            fi
        fi
    fi
    rm -f "$DAEMON_PIDFILE"
    [ "$stopped" = 1 ]
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
        # 先写停止标记，再终止进程。守护可能正好在恢复线程里；它及其
        # 已启动的 --apply-plan 子进程都会看到这个标记并退出，不会复活壁纸。
        touch "$STOP_MARKER"
        stopped=0
        if stop_renderers; then stopped=1; fi
        rm -f "$PIDFILE"
        # 连守护一起停(它只在壁纸运行时有意义)
        if stop_daemon; then stopped=1; fi
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
        pid="$(cat "$PIDFILE" 2>/dev/null || true)"
        if [[ "$pid" =~ ^[0-9]+$ ]] && renderer_pid_matches "$pid"; then
            echo "运行中 (PID $pid)"
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
    echo "先跑 install.sh，或用 LWE_BIN 指定其他位置" >&2
    exit 1
fi

# 普通用户启动意味着重新允许壁纸；守护恢复则必须尊重用户刚执行的
# --stop。环境标记由 suspend_daemon.py 传入，避免恢复路径误删停止标记。
if [ "${LWE_RECOVERY:-}" = "1" ]; then
    if [ -e "$STOP_MARKER" ]; then
        echo "壁纸已被用户停止，跳过自动恢复" >&2
        exit 75
    fi
else
    rm -f "$STOP_MARKER"
fi

# --gnome 模式本身是普通 xdg-shell 窗口；只有 GNOME 扩展启用后才会把
# 它克隆进桌面背景并隐藏原窗口。扩展被手动禁用、安装后未写入启用列表，
# 或升级后 GNOME 恢复了禁用状态时，直接启动会让全屏窗口盖住顶栏和 Dock。
# 启动前先幂等启用扩展，失败就中止，避免留下一个覆盖桌面的窗口。
if ! PYTHONPATH="$SCRIPT_DIR${PYTHONPATH:+:$PYTHONPATH}" \
    python3 -m wallpaper_picker.gnome_extension; then
    echo "无法启用 GNOME 扩展 $EXT_UUID，已取消启动以避免覆盖桌面" >&2
    echo "请确认 GNOME Shell 正在运行，并执行: gnome-extensions enable $EXT_UUID" >&2
    exit 1
fi

# 前导多屏参数（必须出现在壁纸参数之前）：--all-screens 克隆到全部
# 已连接屏；--screen CONNECTOR ID 逐屏指定（可重复，出现即脱离克隆）；
# --apply-plan 不带壁纸参数，纯按 settings 意图（clone/screens/last）
# 拼装全部屏——登录恢复与设置改动后的 reapply 走这条通道。
ALL_SCREENS=""
APPLY_PLAN=0
EXPLICIT=()
while [ $# -gt 0 ]; do
    case "$1" in
        --all-screens) ALL_SCREENS=1; shift;;
        --apply-plan) APPLY_PLAN=1; shift;;
        --screen) EXPLICIT+=("--screen" "$2" "$3"); shift 3;;
        *) break;;
    esac
done

# 位置参数是壁纸：纯数字当 ID，否则按标题模糊匹配。纯 --screen 模式
# 可以没有它（此时未指定的屏回落 settings.screens 或上次的壁纸）。
BG=""
ARG="${1:-}"
[ $# -gt 0 ] && shift
if [ -n "$ARG" ]; then
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
fi
if [ -z "$BG" ] && [ ${#EXPLICIT[@]} -eq 0 ] && [ "$APPLY_PLAN" != 1 ]; then
    echo "用法: $0 [--all-screens | --screen 连接器 ID ...] [壁纸ID或名称]" >&2
    exit 1
fi

# 渲染器完整参数（每屏 --screen-root/--bg + 逐屏 flags + 进程级参数）：
# 由 paths.py 的 launch 一次性决定——settings 里的 clone/screens/last +
# 本次参数算出计划，展开成参数并以 NUL 分隔输出。在 bash 里做「flag 组
# 级」的拼装/去重（值可能含空格）不可靠，曾把属性值漏成位置参数导致
# 渲染器把它当 --bg 解析而崩溃，所以组装整体放在 Python 侧。
mapfile -d '' -t RENDER_ARGS < <(python3 "$SCRIPT_DIR/wallpaper_picker/paths.py" launch \
    ${ALL_SCREENS:+--all-screens} \
    ${EXPLICIT[@]+"${EXPLICIT[@]}"} \
    ${BG:+"$BG"})
if [ ${#RENDER_ARGS[@]} -eq 0 ] || [ -z "${RENDER_ARGS[0]}" ]; then
    echo "无法确定显示器计划（paths.py launch 无输出）" >&2
    exit 1
fi

# 先停掉旧实例。只匹配当前 BIN 且带 --gnome 的进程，避免误杀其它用户
# 或 LWE_BIN 指向的另一种渲染器。轮询等它真的退出，SIGKILL 保底。
stop_renderers || true

# 从参数列表里提取屏幕名给成功消息用（--screen-root 的下一个 token）
SCREEN_LIST=""
prev=""
for a in ${RENDER_ARGS[@]+"${RENDER_ARGS[@]}"}; do
    [ "$prev" = "--screen-root" ] && SCREEN_LIST="$SCREEN_LIST $a"
    prev="$a"
done

nohup "$BIN" \
    --gnome \
    ${RENDER_ARGS[@]+"${RENDER_ARGS[@]}"} \
    "$@" \
    > "$LOG" 2>&1 &

RENDERER_PID=$!
echo "$RENDERER_PID" > "$PIDFILE"

# 轮询确认进程活着。只要熬过 STARTUP_GRACE 就算启动成功，
# 期间进程死掉则立刻报错——比原来的固定 sleep 3 快得多。
STARTUP_GRACE=6   # 单位 0.1 秒，即 0.6 秒
alive=0
for _ in $(seq 1 "$STARTUP_GRACE"); do
    sleep 0.1
    if ! renderer_pid_matches "$RENDERER_PID"; then
        alive=-1
        break
    fi
    alive=1
done

if [ "$alive" = 1 ]; then
    # --stop 可能在启动检查期间并发写入标记。恢复路径再次确认，避免
    # 恢复脚本在用户停止动作之后才把新窗口拉起来。
    if [ "${LWE_RECOVERY:-}" = "1" ] && [ -e "$STOP_MARKER" ]; then
        stop_renderers || true
        rm -f "$PIDFILE"
        stop_daemon || true
        echo "壁纸已被用户停止，取消自动恢复" >&2
        exit 75
    fi
    # 只有渲染器通过启动检查后才拉起守护，失败路径不会留下孤儿进程。
    if [ -f "$SCRIPT_DIR/wallpaper_picker/suspend_daemon.py" ]; then
        nohup python3 "$SCRIPT_DIR/wallpaper_picker/suspend_daemon.py" > /dev/null 2>&1 &
    fi
    echo "壁纸已启动 (PID $(cat "$PIDFILE")，屏:${SCREEN_LIST:-$SCREEN})"
    echo "日志: $LOG"
else
    echo "启动失败，日志末尾:" >&2
    tail -20 "$LOG" >&2
    stop_renderers || true
    stop_daemon || true
    rm -f "$PIDFILE"
    exit 1
fi
