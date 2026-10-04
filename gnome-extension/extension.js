/**
 * linux-wallpaperengine GNOME Shell Extension
 *
 * Entry point. Creates and destroys the WallpaperManager which handles
 * detection of renderer windows and injection of Clutter.Clone actors
 * into GNOME Shell's desktop background.
 *
 * Architecture follows gnome-ext-hanabi's two-component model:
 *   - C++ app: renders to xdg-shell Wayland windows with encoded titles
 *   - This extension: clones those windows into the desktop background
 */

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import {WallpaperManager} from './wallpaperManager.js';

export default class LWPEExtension extends Extension {
    enable() {
        this._manager = new WallpaperManager();
    }

    disable() {
        if (this._manager) {
            this._manager.destroy();
            this._manager = null;
        }
    }
}
