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
        this._retryId = 0;
        this._destroying = false;

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

            // Detect when the current clone's source has gone stale
            const cloneSource = this._wallpaper?.get_source?.();
            const sourceAlive = cloneSource && !this._isActorDisposed(cloneSource);

            if (!sourceAlive && this._wallpaper) {
                // Old renderer window is gone — tear down the clone
                this._wallpaper.destroy();
                this._wallpaper = null;
                log(`lwpe: renderer gone on monitor ${this._monitorIndex}`);
            }

            if (!this._wallpaper) {
                const source = this._getSource();
                if (source) {
                    this._wallpaper = new Clutter.Clone({
                        source,
                        pivot_point: new Graphene.Point({x: 0.5, y: 0.5}),
                    });
                    this._wallpaper.connect('destroy', () => {
                        this._wallpaper = null;
                    });
                    this._wallpaper.set_size(this._monitorWidth, this._monitorHeight);
                    this.add_child(this._wallpaper);
                    this._fade();
                    log(`lwpe: wallpaper applied on monitor ${this._monitorIndex}`);
                } else if (_firstRun) {
                    log(`lwpe: no renderer yet for monitor ${this._monitorIndex}, retrying...`);
                }
            }

            _firstRun = false;
            return GLib.SOURCE_CONTINUE;
        };

        // Try immediately (sync) so clones are ready for overview transitions.
        // Start the perpetual timer afterwards for wallpaper change detection.
        operation();
        this._retryId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 1000, operation);
    }

    /**
     * Safely check whether a GObject actor has been disposed.
     */
    _isActorDisposed(actor) {
        try {
            // Accessing any property on a disposed object throws
            void actor.get_parent?.();
            return false;
        } catch (e) {
            return true;
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

        // Fall back to cached actors: on GNOME 50+ Mutter may remove
        // minimized MetaWindowActors from get_window_actors().
        if (rendererActors.length === 0) {
            rendererActors = [..._rendererActors].filter(actor => {
                try {
                    return !(actor.is_destroyed?.() ?? false) && isRendererActor(actor);
                } catch (e) {
                    return false;
                }
            });
            if (rendererActors.length === 0) {
                return null;
            }
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

    destroy() {
        this._destroying = true;
        if (this._retryId) {
            GLib.source_remove(this._retryId);
            this._retryId = 0;
        }
        if (this._wallpaper) {
            this._wallpaper.destroy();
            this._wallpaper = null;
        }

        super.destroy();
    }
});

class ManagedWindow {
    constructor(window) {
        this._window = window;
        this._signals = [];
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

        // Cache MetaWindowActor before minimizing — on GNOME 50+ Mutter
        // may remove minimized actors from get_window_actors().
        const actor = this._window.get_compositor_private?.();
        if (actor) {
            _rendererActors.add(actor);
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

        // Remove cached actor when the window is unmapped
        const actor = window.get_compositor_private?.();
        if (actor) {
            _rendererActors.delete(actor);
        }

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

        this._enable();
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
        // Each override wrapped individually so a single incompatible
        // API won't disable the entire extension on startup (issue #4).
        try {
            this._injectionManager.overrideMethod(
                Background.BackgroundManager.prototype,
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
        } catch (e) {
            log(`lwpe: _createBackgroundActor override FAILED: ${e.message}`);
        }

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
        this._windowManager.disable();
        this._injectionManager.clear();
        this._reloadBackgrounds();

        if (global.lwpeWallpaperManager === this) {
            global.lwpeWallpaperManager = null;
        }

        log('lwpe: disabled');
    }
}
