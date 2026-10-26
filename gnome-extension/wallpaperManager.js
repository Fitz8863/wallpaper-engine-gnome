/**
 * linux-wallpaperengine GNOME Shell Extension - Wallpaper Manager
 *
 * This intentionally follows gnome-ext-hanabi's Shell-side structure:
 *   - LiveWallpaper clones the renderer's surface actor into GNOME's background.
 *   - ManagedWindow parses state from the renderer title, minimizes the renderer
 *     window (so it doesn't cover the desktop), and pins the surface container
 *     actor at (0,0) so it remains composited.
 *   - Shell overrides hide renderer windows from overview, Alt+Tab, and apps.
 *
 * Window title format (set by the C++ renderer):
 *   @linux-wallpaperengine!{"monitor":"HDMI-1","position":[0,0],
 *     "keepAtBottom":true,"keepMinimized":true,"keepPosition":true}
 */

import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import GObject from 'gi://GObject';
import Graphene from 'gi://Graphene';
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';
import St from 'gi://St';

import * as Background from 'resource:///org/gnome/shell/ui/background.js';
import * as Config from 'resource:///org/gnome/shell/misc/config.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as Workspace from 'resource:///org/gnome/shell/ui/workspace.js';
import * as WorkspaceThumbnail from 'resource:///org/gnome/shell/ui/workspaceThumbnail.js';
import {InjectionManager} from 'resource:///org/gnome/shell/extensions/extension.js';

const APPLICATION_ID = '@linux-wallpaperengine!';
const SHELL_VERSION = parseInt(Config.PACKAGE_VERSION.split('.')[0]);
const BACKGROUND_FADE_ANIMATION_TIME = 1000;

// Cached MetaWindowActors — fallback when Mutter removes minimized
// actors from get_window_actors() on GNOME 50+.
const _rendererActors = new Set();

// 运行时状态目录与渲染器 pid 文件（与 picker/启动器约定一致，默认
// ~/.cache/wallpaper-picker）。扩展进程通常继承 LWE_STATE_DIR；如果用户
// 只给启动器设置了该变量，则从正在运行的渲染器/守护进程环境中解析它。
const _DEFAULT_STATE_DIR = GLib.build_filenamev(
    [GLib.get_user_cache_dir(), 'wallpaper-picker']);
let _runtimeStateDir = null;
let _lastRestartRequest = 0;

function addStateDir(dirs, path) {
    if (!path || !path.trim())
        return;
    const normalized = path.replace(/\/+$/, '');
    if (normalized && !dirs.includes(normalized))
        dirs.push(normalized);
}

function processStateDirs() {
    const dirs = [];
    let enumerator = null;
    try {
        const proc = Gio.File.new_for_path('/proc');
        enumerator = proc.enumerate_children(
            'standard::name', Gio.FileQueryInfoFlags.NONE, null);
        let info;
        while ((info = enumerator.next_file(null)) !== null) {
            const name = info.get_name();
            if (!/^\d+$/.test(name))
                continue;
            const [cmdOk, cmdBytes] = GLib.file_get_contents(
                `/proc/${name}/cmdline`);
            if (!cmdOk)
                continue;
            const cmdline = new TextDecoder().decode(cmdBytes);
            const renderer = cmdline.includes('--gnome');
            const daemon = cmdline.includes('suspend_daemon.py');
            if (!renderer && !daemon)
                continue;
            const [envOk, envBytes] = GLib.file_get_contents(
                `/proc/${name}/environ`);
            if (!envOk)
                continue;
            for (const item of new TextDecoder().decode(envBytes).split('\0')) {
                if (item.startsWith('LWE_STATE_DIR='))
                    addStateDir(dirs, item.substring('LWE_STATE_DIR='.length));
                else if (item.startsWith('XDG_CACHE_HOME='))
                    addStateDir(dirs, GLib.build_filenamev([
                        item.substring('XDG_CACHE_HOME='.length),
                        'wallpaper-picker']));
            }
        }
    } catch (e) {
        // /proc may be unavailable or restricted; the default/env paths remain.
    } finally {
        try { enumerator?.close(null); } catch (e) {}
    }
    return dirs;
}

function stateDirCandidates() {
    const dirs = [];
    addStateDir(dirs, GLib.getenv('LWE_STATE_DIR'));
    const xdgCache = GLib.getenv('XDG_CACHE_HOME');
    if (xdgCache) {
        addStateDir(dirs, GLib.build_filenamev([xdgCache, 'wallpaper-picker']));
    }
    addStateDir(dirs, _DEFAULT_STATE_DIR);
    processStateDirs().forEach(path => addStateDir(dirs, path));
    return dirs;
}

function rendererProcessAlive(stateDir) {
    try {
        const pidfile = GLib.build_filenamev([stateDir, 'wallpaper.pid']);
        const [ok, contents] = GLib.file_get_contents(pidfile);
        if (!ok)
            return false;
        const pid = new TextDecoder().decode(contents).trim();
        if (!pid || !GLib.file_test(`/proc/${pid}`, GLib.FileTest.EXISTS))
            return false;
        const [cmdOk, cmdBytes] = GLib.file_get_contents(
            `/proc/${pid}/cmdline`);
        return cmdOk && new TextDecoder().decode(cmdBytes).includes('--gnome');
    } catch (e) {
        return false;
    }
}

function resolveStateDir(force = false) {
    if (!force && _runtimeStateDir && rendererProcessAlive(_runtimeStateDir))
        return _runtimeStateDir;

    const candidates = stateDirCandidates();
    for (const candidate of candidates) {
        if (rendererProcessAlive(candidate)) {
            _runtimeStateDir = candidate;
            return candidate;
        }
    }
    _runtimeStateDir = candidates[0] || _DEFAULT_STATE_DIR;
    return _runtimeStateDir;
}

// 桌面背景上没有任何可克隆的渲染器窗口时，只有重启渲染器进程才能恢复
// （新窗口才会重新走 map → 被 WindowManager 纳管）。这里只写标记文件，
// 实际重启由 suspend_daemon.py 执行；没在跑渲染器（用户主动停了）时不写。
function requestRendererRestart(reason) {
    const now = Date.now();
    if (now - _lastRestartRequest < 10000)
        return;
    const stateDir = resolveStateDir(true);
    if (!rendererProcessAlive(stateDir))
        return;
    _lastRestartRequest = now;
    try {
        // 注意：file_set_contents 的 contents 必须是字节数组，
        // 传裸字符串会抛类型错误。
        GLib.file_set_contents(
            GLib.build_filenamev([stateDir, 'lwpe-needs-restart']),
            new TextEncoder().encode(String(now)));
        log(`lwpe: ${reason}, requested renderer restart`);
    } catch (e) {
        log(`lwpe: restart request failed: ${e.message}`);
    }
}

// 画面正常的证据：所有 LiveWallpaper 都挂着克隆时定期刷新心跳文件。
// suspend_daemon.py 用它判断解锁/唤醒后是否真需要重启渲染器——新版扩展
// 锁屏时不会被停用，正常情况下无需重启；只有心跳过期（扩展找不到画面源）
// 才重启。与 lwpe-needs-restart 的区别：那个是「现在坏了，请重启」，
// 这个是「现在是好的，别动手」。
const _starved = new Set();
let _lastHeartbeat = 0;

function reportWallpaperState(instance, hasSource) {
    if (hasSource)
        _starved.delete(instance);
    else
        _starved.add(instance);

    const now = Date.now();
    if (_starved.size > 0 || now - _lastHeartbeat < 5000)
        return;
    _lastHeartbeat = now;
    try {
        GLib.file_set_contents(
            GLib.build_filenamev([resolveStateDir(), 'lwpe-healthy']),
            new TextEncoder().encode(String(now)));
    } catch (e) {
        // 心跳写不出去只会让守护多做一次无谓重启，不影响画面
    }
}

// Meta 的窗口列表包含最小化窗口，而 get_window_actors() 从 GNOME 46 起
// 不含——渲染器窗口恰恰是被刻意最小化的。扩展被停用再启用（锁屏解锁走的
// 就是这条路）时，这是找回画面源的最后一道兜底。
function listRendererWindows() {
    try {
        return global.display.list_all_windows().filter(isRendererWindow);
    } catch (e) {
        log(`lwpe: list_all_windows failed: ${e.message}`);
        return [];
    }
}

function getWindowTitle(window) {
    try {
        return window?.get_title?.() || window?.title || '';
    } catch (e) {
        return '';
    }
}

function isRendererWindow(window) {
    return getWindowTitle(window).startsWith(APPLICATION_ID);
}

function isRendererActor(actor) {
    const window = actor?.meta_window || actor?.get_meta_window?.();
    return isRendererWindow(window);
}

const LiveWallpaper = GObject.registerClass({
    GTypeName: 'LWPELiveWallpaper',
}, class LiveWallpaper extends St.Widget {
    constructor(backgroundActor) {
        super({
            layout_manager: new Clutter.BinLayout(),
            width: backgroundActor.width,
            height: backgroundActor.height,
            reactive: false,
            x_expand: true,
            y_expand: true,
            opacity: 0,
        });

        this._backgroundActor = backgroundActor;
        this._monitorIndex = backgroundActor.monitor;
        this._wallpaper = null;
        this._cloneSource = null;
        this._sourceDestroyId = 0;
        this._sourceMappedId = 0;
        this._retryId = 0;
        this._destroying = false;
        this._failTicks = 0;

        const monitor = Main.layoutManager.monitors[this._monitorIndex];
        this._monitorWidth = monitor?.width ?? backgroundActor.width;
        this._monitorHeight = monitor?.height ?? backgroundActor.height;

        backgroundActor.layout_manager = new Clutter.BinLayout();
        backgroundActor.add_child(this);

        this._applyWallpaper();
        log(`lwpe: LiveWallpaper created for monitor ${this._monitorIndex} (${this._monitorWidth}x${this._monitorHeight})`);
    }

    _applyWallpaper() {
        // Like Hanabi: try synchronously first so that workspace
        // backgrounds created during overview open have their clone
        // ready before _updateBorderRadius fires.  Then keep a
        // perpetual retry loop for wallpaper change detection.
        let _firstRun = true;
        const operation = () => {
            if (this._destroying) return GLib.SOURCE_REMOVE;

            const live = this._getSource();

            if (this._wallpaper) {
                // 克隆可能比它的源活得久（GC 期间 destroy 回调被抑制、
                // 锁屏/唤醒把窗口 actor 拆掉等）：此时画面会永远冻结在
                // 最后一帧且不会自愈。每个 tick 把记住的源与实时解析的
                // 源比对，不一致就重建克隆。
                if (this._cloneSource !== live) {
                    this._clearClone();
                }
            }

            if (!this._wallpaper) {
                if (live) {
                    this._attachClone(live);
                    this._failTicks = 0;
                } else {
                    if (_firstRun) {
                        log(`lwpe: no renderer yet for monitor ${this._monitorIndex}, retrying...`);
                    }
                    this._failTicks += 1;
                    if (this._failTicks >= 5) {
                        // 持续 5 秒找不到任何可克隆窗口：渲染器多半还活着
                        // 但它的窗口已经失效，只有重启渲染器能恢复。
                        requestRendererRestart(
                            `no live source for monitor ${this._monitorIndex}`);
                        this._failTicks = 0;
                    }
                }
            }

            reportWallpaperState(this, Boolean(this._wallpaper));
            _firstRun = false;
            return GLib.SOURCE_CONTINUE;
        };

        // Try immediately (sync) so clones are ready for overview transitions.
        // Start the perpetual timer afterwards for wallpaper change detection.
        operation();
        this._retryId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 1000, operation);
    }

    _attachClone(source) {
        const clone = new Clutter.Clone({
            source,
            pivot_point: new Graphene.Point({x: 0.5, y: 0.5}),
        });
        this._cloneSource = source;
        // Renderer windows are torn down by C code, which only emits the
        // destroy signal — no JS hook runs.  React to it here and detach
        // the clone right away; the next poll tick attaches the new
        // window.  Without this we'd have to poll-detect the stale source,
        // and every probe of a disposed actor logs a GJS warning.
        this._sourceDestroyId = source.connect('destroy', () => {
            this._sourceDestroyId = 0;
            this._cloneSource = null;
            this._clearClone();
            log(`lwpe: renderer gone on monitor ${this._monitorIndex}`);
        });
        // Lock screen tears the window actor down via unmap (not destroy):
        // on unlock Mutter remaps it and our clone would keep painting the
        // stale, unmapped actor — the wallpaper silently disappears while
        // renderer and clone both look alive.  Watch mapping state and
        // rebuild the clone through the regular retry tick when the
        // window comes back.
        this._sourceMappedId = source.connect('notify::mapped', src => {
            if (src.mapped) {
                this._clearClone();
            }
        });
        clone.connect('destroy', () => {
            this._wallpaper = null;
        });
        clone.set_size(this._monitorWidth, this._monitorHeight);
        this.add_child(clone);
        this._wallpaper = clone;
        this._fade();
        log(`lwpe: wallpaper applied on monitor ${this._monitorIndex}`);
    }

    _clearClone() {
        // The source destroy callback resets both ids before calling us,
        // so a live disconnect only happens on teardown paths.
        for (const [obj, id] of [
            [this._cloneSource, this._sourceDestroyId],
            [this._cloneSource, this._sourceMappedId],
        ]) {
            if (obj && id) {
                try {
                    obj.disconnect(id);
                } catch (e) {
                    // source already disposed
                }
            }
        }
        this._sourceDestroyId = 0;
        this._sourceMappedId = 0;
        this._cloneSource = null;
        if (this._wallpaper) {
            const clone = this._wallpaper;
            this._wallpaper = null;
            clone.destroy();
        }
    }

    /**
     * Find the MetaWindowActor to clone.
     *
     * We clone the MetaWindowActor directly, just like gnome-ext-hanabi.
     * The ManagedWindow keeps the window minimized and pins its surface
     * container at (0,0) so the clone always paints the latest frame.
     *
     * Monitor matching is done first by meta_window.get_monitor() (index),
     * and if that fails, by parsing the "monitor" name from the window
     * title JSON and resolving it to a monitor index.
     */
    _getSource() {
        let windowActors = [];
        try {
            windowActors = global.get_window_actors(false);
        } catch (e) {
            log(`lwpe: get_window_actors(false) threw: ${e.message}`);
            windowActors = global.get_window_actors();
        }

        let rendererActors = windowActors.filter(isRendererActor);

        // Fall back to cached actors: Mutter keeps minimized windows out of
        // get_window_actors(), and the renderer window is minimized by design.
        if (rendererActors.length === 0) {
            rendererActors = [..._rendererActors].filter(actor => {
                try {
                    return !(actor.is_destroyed?.() ?? false) && isRendererActor(actor);
                } catch (e) {
                    return false;
                }
            });
        }

        // Last resort: ask Meta for the full window list (minimized windows
        // included) and use each window's compositor actor.
        if (rendererActors.length === 0) {
            rendererActors = listRendererWindows()
                .map(window => window.get_compositor_private?.())
                .filter(actor => actor != null);
        }

        if (rendererActors.length === 0) {
            return null;
        }

        // First try: match by monitor index
        let renderer = rendererActors.find(actor => {
            try {
                return actor.meta_window.get_monitor() === this._monitorIndex;
            } catch (e) {
                return false;
            }
        });

        if (renderer) return renderer;

        // Second try: parse monitor name from window title and resolve to index
        const monitors = Main.layoutManager.monitors;
        for (const actor of rendererActors) {
            try {
                const title = getWindowTitle(actor.meta_window);
                const jsonStr = title.substring(APPLICATION_ID.length);
                const info = JSON.parse(jsonStr);
                if (info.monitor) {
                    // Find the monitor index whose connector name matches
                    const idx = monitors.findIndex(m => m?.connector === info.monitor);
                    if (idx !== -1 && idx === this._monitorIndex) {
                        log(`lwpe: matched renderer by connector "${info.monitor}" → monitor ${idx}`);
                        return actor;
                    }
                    // Also log what monitors are available for debugging
                    const connectors = monitors.map((m, i) => `[${i}]=${m?.connector || '?'}`).join(', ');
                    log(`lwpe: renderer for "${info.monitor}", monitor ${this._monitorIndex}, available: ${connectors}`);
                }
            } catch (e) {
                // Skip title parse errors
            }
        }

        // Last resort: if we have exactly one renderer and one monitor, use it
        if (rendererActors.length === 1 && monitors.length === 1) {
            log('lwpe: falling back to single-renderer match');
            return rendererActors[0];
        }

        // Debug: log why _getSource failed (once per 30s per LiveWallpaper)
        if (!this.__lastSourceFailLog || Date.now() - this.__lastSourceFailLog > 30000) {
            this.__lastSourceFailLog = Date.now();
            log(`lwpe: _getSource FAIL — monitorIndex=${this._monitorIndex}, rendererCount=${rendererActors.length}, monitorCount=${monitors.length}, cached=${_rendererActors.size}`);
        }
        return null;
    }

    _fade(visible = true) {
        this.ease({
            opacity: visible ? 255 : 0,
            duration: BACKGROUND_FADE_ANIMATION_TIME,
            mode: Clutter.AnimationMode.EASE_OUT_QUAD,
        });
    }

    // vfunc, not a JS destroy() override: C-side teardown (Mutter
    // destroying the background group, BackgroundManager cleanup) only
    // emits the destroy signal and never calls a JS method.  A JS
    // override would miss it, leaving the retry timer running on a
    // disposed object — the "LWPELiveWallpaper ... already disposed"
    // log spam on every wallpaper change came from exactly that.
    vfunc_destroy() {
        this._destroying = true;
        _starved.delete(this);
        if (this._retryId) {
            GLib.source_remove(this._retryId);
            this._retryId = 0;
        }
        this._clearClone();
        super.vfunc_destroy();
    }
});

class ManagedWindow {
    constructor(window) {
        this._window = window;
        this._signals = [];
        this._actor = null;
        this._surfaceContainer = null;
        this._surfacePositionId = 0;
        this._states = {
            position: [0, 0],
            keepAtBottom: false,
            keepMinimized: false,
            keepPosition: false,
        };

        this._signals.push(this._window.connect('notify::title', () => {
            this._parseTitle();
        }));
        this._signals.push(this._window.connect_after('shown', () => {
            if (this._states.keepMinimized) this._window.minimize();
        }));
        this._signals.push(this._window.connect_after('raised', () => {
            if (this._states.keepAtBottom) this._window.lower();
        }));
        this._signals.push(this._window.connect('notify::above', () => {
            if (this._states.keepAtBottom && this._window.above) {
                this._window.unmake_above();
            }
        }));
        this._signals.push(this._window.connect('notify::minimized', () => {
            if (this._states.keepMinimized && !this._window.minimized) {
                this._window.minimize();
            }
        }));
        this._signals.push(this._window.connect('position-changed', () => {
            if (this._states.keepPosition) {
                const [x, y] = this._states.position;
                this._window.move_frame(true, x, y);
            }
        }));

        // GNOME 45 issue #3159: when a window is minimized, Mutter moves
        // the surface container to a negative position to hide it.  We
        // pin it at (0,0) so the Clutter.Clone still sees the content.
        // On GNOME 50+ this causes a feedback loop with minimize that
        // destroys the MetaWindowActor.  Hanabi gates this too.
        if (SHELL_VERSION === 45) {
            this._pinSurfaceContainer();
        }

        // Cache MetaWindowActor before minimizing — Mutter keeps minimized
        // windows out of get_window_actors(), so this cache is the main way
        // LiveWallpaper._getSource() finds the source.  It must survive the
        // extension's own disable/enable cycle (what a lock screen does to
        // us): clearing it left _getSource with every source empty and the
        // wallpaper gone for good.  Cleanup is owned by the actor itself so
        // it also runs while the extension is disabled.
        const actor = this._window.get_compositor_private?.();
        if (actor) {
            this._actor = actor;
            _rendererActors.add(actor);
            if (!actor._lwpeDestroyTracked) {
                actor._lwpeDestroyTracked = true;
                actor.connect('destroy', () => _rendererActors.delete(actor));
            }
        }

        this._parseTitle();
    }

    _pinSurfaceContainer() {
        const windowActor = this._window.get_compositor_private?.();
        const children = windowActor?.get_children?.() ?? [];

        const surfaceTypes = [
            'MetaSurfaceContainerActorWayland',
            'MetaSurfaceActorWayland',
            'MetaSurfaceContainerActorX11',
            'MetaSurfaceActorX11',
        ];
        for (const typeName of surfaceTypes) {
            const surface = children.find(c => {
                try { return GObject.type_name(c) === typeName; } catch (e) { return false; }
            });
            if (surface) {
                this._surfaceContainer = surface;
                this._surfacePositionId = surface.connect('notify::position', () => {
                    surface.set_position(0, 0);
                });
                log(`lwpe: pinning surface container ${typeName} at (0,0)`);
                return;
            }
        }

        // Log children types for debugging
        const childTypes = children.map(c => {
            try { return GObject.type_name(c); } catch (e) { return '?'; }
        });
        log(`lwpe: no surface container found, children: [${childTypes.join(', ')}]`);
    }

    _parseTitle() {
        const title = getWindowTitle(this._window);
        if (!title.startsWith(APPLICATION_ID)) return;

        const json = title.substring(APPLICATION_ID.length);
        try {
            const newStates = JSON.parse(json);
            this._states = {...this._states, ...newStates};
        } catch (e) {
            log(`lwpe: failed to parse renderer title: ${e.message}`);
        }

        this._refresh();
    }

    _refresh() {
        if (this._states.keepAtBottom && this._window.above) {
            this._window.unmake_above();
        }
        if (this._states.keepAtBottom) {
            this._window.lower();
        }
        if (this._states.keepMinimized && !this._window.minimized) {
            this._window.minimize();
        }
        if (this._states.keepPosition) {
            const [x, y] = this._states.position;
            this._window.move_frame(true, x, y);
        }
    }

    disconnect() {
        // The surface container may have been disposed by Mutter already;
        // wrap in try/catch to avoid crashing on disposed objects.
        if (this._surfaceContainer && this._surfacePositionId) {
            try {
                this._surfaceContainer.disconnect(this._surfacePositionId);
            } catch (e) {
                // surface container already disposed — nothing to do
            }
        }

        // Forget the actor but leave it in _rendererActors: that cache is
        // what lets a re-enabled extension find the (minimized) renderer
        // window again.  Stale entries age out through the actor's own
        // destroy handler.
        this._actor = null;

        this._signals.forEach(signal => {
            try {
                this._window.disconnect(signal);
            } catch (e) {
                // window may already be disposed
            }
        });
        this._signals = [];
        this._surfaceContainer = null;
        this._surfacePositionId = 0;
        this._window = null;
    }
}

class WindowManager {
    constructor() {
        this._windows = new Set();
        this._mapId = 0;
    }

    enable() {
        this._mapId = global.window_manager.connect_after('map', (_wm, windowActor) => {
            const window = windowActor.get_meta_window();
            if (isRendererWindow(window)) {
                this.addWindow(window);
            }
        });

        let windowActors = [];
        try {
            windowActors = global.get_window_actors(false);
        } catch (e) {
            windowActors = global.get_window_actors();
        }

        windowActors.filter(isRendererActor).forEach(actor => this.addWindow(actor));
        // get_window_actors() 不含最小化窗口：被刻意最小化的渲染器窗口只能
        // 从 Meta 的完整列表里找回来（扩展停用再启用后正是这种状态）。
        listRendererWindows().forEach(window => this.addWindow(window));
    }

    disable() {
        this._windows.forEach(window => this._clearWindow(window));
        this._windows.clear();

        if (this._mapId) {
            global.window_manager.disconnect(this._mapId);
            this._mapId = 0;
        }
    }

    addWindow(window) {
        if (window.get_meta_window) {
            window = window.get_meta_window();
        }

        if (this._windows.has(window)) return;

        window.lwpeManaged = new ManagedWindow(window);
        window.lwpeUnmanagedId = window.connect('unmanaged', _window => {
            this._clearWindow(_window);
            this._windows.delete(_window);
        });
        this._windows.add(window);
    }

    _clearWindow(window) {
        if (!window.lwpeManaged) return;

        // The cached MetaWindowActor is dropped inside disconnect();
        // looking it up via get_compositor_private() here would return
        // null (or a disposed actor) once the window is going away.
        window.disconnect(window.lwpeUnmanagedId);
        window.lwpeManaged.disconnect();
        window.lwpeManaged = null;
        window.lwpeUnmanagedId = 0;
    }
}

export class WallpaperManager {
    constructor() {
        this._injectionManager = new InjectionManager();
        this._wallpaperActors = new Set();
        this._windowManager = new WindowManager();

        try {
            this._enable();
        } catch (e) {
            // Keep a failed enable from leaving a partially injected Shell.
            this._injectionManager.clear();
            throw e;
        }
        log('lwpe: enabled');
    }

    _reloadBackgrounds() {
        this._wallpaperActors.forEach(actor => actor.destroy());
        this._wallpaperActors.clear();

        try { Main.layoutManager._updateBackgrounds(); } catch (e) {}
        try { Main.screenShield?._dialog?._updateBackgrounds?.(); } catch (e) {}
        try {
            Main.overview?._overview?._controls?._workspacesDisplay?._updateWorkspacesViews?.();
        } catch (e) {}

        // Blur My Shell compatibility: emit signals so BMS re-samples
        // the background with our LiveWallpaper included.  Hanabi does
        // the same in their _reloadBackgrounds.
        if (Main.extensionManager?._enabledExtensions?.includes?.('blur-my-shell@aunetx')) {
            try { Main.layoutManager.emit('monitors-changed'); } catch (e) {}
            try { global.display.emit('workareas-changed'); } catch (e) {}
        }
    }

    _enable() {
        // The background hook is the safety boundary: without it the renderer
        // remains a normal fullscreen window and can cover the top bar/Dock.
        // A failure must abort enable instead of leaving the extension ACTIVE.
        try {
            const backgroundPrototype = Background.BackgroundManager.prototype;
            const originalBackgroundHook = backgroundPrototype._createBackgroundActor;
            if (typeof originalBackgroundHook !== 'function') {
                throw new Error('_createBackgroundActor is unavailable');
            }
            this._injectionManager.overrideMethod(
                backgroundPrototype,
                '_createBackgroundActor',
                originalMethod => {
                    return function () {
                        const backgroundActor = originalMethod.call(this);
                        this.videoActor = new LiveWallpaper(backgroundActor);
                        const manager = global.lwpeWallpaperManager;
                        manager?._wallpaperActors.add(this.videoActor);
                        this.videoActor.connect('destroy', actor => {
                            manager?._wallpaperActors.delete(actor);
                        });
                        return backgroundActor;
                    };
                }
            );
            if (backgroundPrototype._createBackgroundActor === originalBackgroundHook) {
                throw new Error('InjectionManager did not replace _createBackgroundActor');
            }
        } catch (e) {
            log(`lwpe: _createBackgroundActor override FAILED: ${e.message}`);
            throw new Error(`core background hook unavailable: ${e.message}`);
        }

        // Each non-core override is best-effort because GNOME Shell changes
        // these private APIs between releases.
        try {
            this._injectionManager.overrideMethod(
                Shell.Global.prototype,
                'get_window_actors',
                originalMethod => {
                    return function (hideRenderer = true) {
                        const windowActors = originalMethod.call(this);
                        return hideRenderer
                            ? windowActors.filter(actor => !isRendererActor(actor))
                            : windowActors;
                    };
                }
            );
        } catch (e) {
            log(`lwpe: get_window_actors override FAILED: ${e.message}`);
        }

        try {
            this._injectionManager.overrideMethod(
                Workspace.Workspace.prototype,
                '_isOverviewWindow',
                originalMethod => {
                    return function (window) {
                        return isRendererWindow(window) ? false : originalMethod.apply(this, [window]);
                    };
                }
            );
        } catch (e) {
            log(`lwpe: Workspace._isOverviewWindow override FAILED: ${e.message}`);
        }

        try {
            this._injectionManager.overrideMethod(
                WorkspaceThumbnail.WorkspaceThumbnail.prototype,
                '_isOverviewWindow',
                originalMethod => {
                    return function (window) {
                        return isRendererWindow(window) ? false : originalMethod.apply(this, [window]);
                    };
                }
            );
        } catch (e) {
            log(`lwpe: WorkspaceThumbnail._isOverviewWindow override FAILED: ${e.message}`);
        }

        try {
            this._injectionManager.overrideMethod(
                Meta.Display.prototype,
                'get_tab_list',
                originalMethod => {
                    return function (type, workspace) {
                        return originalMethod.apply(this, [type, workspace])
                            .filter(window => !isRendererWindow(window));
                    };
                }
            );
        } catch (e) {
            log(`lwpe: get_tab_list override FAILED: ${e.message}`);
        }

        try {
            this._injectionManager.overrideMethod(
                Shell.WindowTracker.prototype,
                'get_window_app',
                originalMethod => {
                    return function (window) {
                        return isRendererWindow(window) ? null : originalMethod.apply(this, [window]);
                    };
                }
            );
        } catch (e) {
            log(`lwpe: get_window_app override FAILED: ${e.message}`);
        }

        try {
            this._injectionManager.overrideMethod(
                Shell.App.prototype,
                'get_windows',
                originalMethod => {
                    return function () {
                        return originalMethod.call(this).filter(window => !isRendererWindow(window));
                    };
                }
            );
        } catch (e) {
            log(`lwpe: App.get_windows override FAILED: ${e.message}`);
        }

        try {
            this._injectionManager.overrideMethod(
                Shell.App.prototype,
                'get_n_windows',
                _originalMethod => {
                    return function () {
                        return this.get_windows().length;
                    };
                }
            );
        } catch (e) {
            log(`lwpe: App.get_n_windows override FAILED: ${e.message}`);
        }

        try {
            this._injectionManager.overrideMethod(
                Shell.AppSystem.prototype,
                'get_running',
                originalMethod => {
                    return function () {
                        return originalMethod.call(this).filter(app => app.get_n_windows() > 0);
                    };
                }
            );
        } catch (e) {
            log(`lwpe: AppSystem.get_running override FAILED: ${e.message}`);
        }

        // Replace WorkspaceBackground._updateBorderRadius to show our
        // LiveWallpaper in the overview instead of the default static
        // blurred background.  We do NOT call originalMethod — Hanabi
        // does the same — because the default applies a Meta.Background
        // blur that would cover our Clutter clone.
        try {
            this._injectionManager.overrideMethod(
                Workspace.WorkspaceBackground.prototype,
                '_updateBorderRadius',
                _originalMethod => {
                    return function () {
                        const video = this._bgManager?.videoActor;
                        log(`lwpe: _updateBorderRadius called, video=${!!video}, hasClone=${!!(video?._wallpaper)}`);
                        if (video && video._wallpaper) {
                            video._wallpaper.set_size(video._monitorWidth, video._monitorHeight);
                        }
                    };
                }
            );
            log('lwpe: _updateBorderRadius override OK');
        } catch (e) {
            log(`lwpe: _updateBorderRadius override FAILED: ${e.message}`);
        }

        global.lwpeWallpaperManager = this;

        // Session unlock / wake: log it (the per-tick source check in
        // LiveWallpaper heals stale clones, and requestRendererRestart asks
        // the daemon to restart the renderer if no live source can be found
        // at all).  NOTE: on GNOME 45+ the lock screen switches the session
        // to the 'unlock-dialog' mode, so the extension must declare that
        // mode in metadata.json's session-modes to stay active across
        // lock/unlock at all.
        try {
            this._lockSignalId = Main.screenShield.connect('wake-up-screen', () => {
                log('lwpe: wake-up-screen received');
            });
        } catch (e) {
            this._lockSignalId = 0;
            log(`lwpe: wake-up-screen signal unavailable: ${e.message}`);
        }

        try {
            this._windowManager.enable();
        } catch (e) {
            log(`lwpe: windowManager.enable FAILED: ${e.message}`);
        }

        try {
            this._reloadBackgrounds();
        } catch (e) {
            log(`lwpe: _reloadBackgrounds FAILED: ${e.message}`);
        }
    }

    destroy() {
        if (this._lockSignalId) {
            try {
                Main.screenShield.disconnect(this._lockSignalId);
            } catch (e) {
                // shield object already gone
            }
            this._lockSignalId = 0;
        }
        this._windowManager.disable();
        this._injectionManager.clear();
        this._reloadBackgrounds();

        if (global.lwpeWallpaperManager === this) {
            global.lwpeWallpaperManager = null;
        }

        log('lwpe: disabled');
    }
}
