# Upstream issue 草稿 —— kv9898/linux-wallpaperengine (gnome 分支)

> 发布方式（需 gh 已登录）：
>
> ```bash
> gh issue create --repo kv9898/linux-wallpaperengine \
>   --title "Web (CEF) wallpapers never render: sandbox close() interposer crash, then CefInitialize hangs without spawning any child process" \
>   --body-file docs/upstream-issue-web-cef.md
> ```
>
> 草稿由本地调查（2026-10-05）整理。渲染器仓库本地实验基于 gnome 分支 6d7d838 + 9fbd435。

---

**Title:** Web (CEF) wallpapers never render: sandbox close() interposer crash, then CefInitialize hangs without spawning any child process

**Body:**

### Summary

Web (`"type": "web"`) wallpapers never show anything. The renderer process
stays alive with no error output, no CEF child processes are spawned, and the
windowless `OnPaint` callback is never invoked. Video and scene wallpapers
work fine on the same machine.

I traced this down through three distinct layers, fixed two of them locally,
and hit a remaining hang inside `CefInitialize` that I could not resolve.
Details and evidence below.

### Environment

- Ubuntu 24.04, GNOME Shell 46, Wayland, NVIDIA RTX 4060 (proprietary driver)
- gnome branch @ `9fbd435`, CEF binary
  `cef_binary_135.0.17+gcbc1c5b+chromium-135.0.7049.52_linux64_minimal`
- Reproducer (no shell needed):
  `linux-wallpaperengine --bg <web-id> --screenshot /tmp/out.png --screenshot-delay 20`
- Test wallpaper: workshop id `827982449` (project.json `"type": "web"`)

### Layer 1 — CEF resource lookup falls back to a non-existent path

`CefInitialize` hangs forever in `futex(FUTEX_WAIT_PRIVATE, ...)`. strace shows
chromium rewriting its own cmdline (empty `/proc/self/cmdline` read) and then
failing to locate runtime resources:

```
openat(".../build/cef/cef_binary_135.0.17+.../Release/icudtl.dat", O_RDONLY) = -1 ENOENT
futex(0x..., FUTEX_WAIT_PRIVATE, 1, NULL) = ? ERESTARTSYS   ← hangs forever
```

`icudtl.dat` and the `.pak` files live in `Resources/` (and are copied to the
output dir by `COPY_FILES`), not in `Release/`. Fixing resource discovery
explicitly unblocks this:

```cpp
// resolve exe dir via readlink("/proc/self/exe")
CefString (&settings.resources_dir_path) = exe_dir;
CefString (&settings.locales_dir_path) = exe_dir + "/locales";
CefString (&settings.browser_subprocess_path) = exe_dir + "/linux-wallpaperengine";
```

### Layer 2 — sandbox close() interposer crashes (ImmediateCrash)

With resources reachable, `CefInitialize` crashes with SIGTRAP. Backtrace:

```
#0 ImmediateCrash () at ../../base/immediate_crash.h:186
#1 close () at ../../base/files/scoped_file_linux.cc:110        ← "close symbol missing"
#2 WriteFile () at ../../base/files/file_util_posix.cc:1092
#3 AdjustOOMScore (...) at ../../chrome/app/chrome_main_delegate.cc:312
#4 AdjustLinuxOOMScore (...) at chrome_main_delegate.cc:312
#5 SandboxInitialized (...) at chrome_main_delegate.cc:1509
#6 Initialize (...) at content_main_runner_impl.cc:1054
...
#13 CefInitialize(...)  ← from liblinux-wallpaperengine-lib.so
```

`settings.no_sandbox` is only set under `#if defined(CEF_NO_SANDBOX)`, which
nothing defines, so the sandbox path always runs and the close() interposer
fails in this binary layout (all logic in `liblinux-wallpaperengine-lib.so`,
main executable is only a thin shell). Setting
`settings.no_sandbox = true;` unconditionally removes the crash.

### Layer 3 — still no rendering: CefInitialize hangs without spawning children

After the two fixes (plus `--disable-gpu` and
`settings.multi_threaded_message_loop = true` with the host's
`CefDoMessageLoopWork()` calls removed), the process still never reaches
browser creation:

- `strace -f -e trace=%process,socketpair` shows **zero** `clone/posix_spawn`
  and **zero** socketpairs — no GPU/zygote/renderer processes are ever created;
- gdb breakpoints on `WallpaperApplication::setupBrowser` and
  `CWeb::CWeb` are never hit within 110 s;
- the process blocks in `futex(FUTEX_WAIT_PRIVATE, 1, NULL)` inside
  `CefInitialize` until killed;
- `OnPaint` / `GetViewRect` breakpoints are never hit (verified under gdb).

So something inside `CefInitialize` on this platform waits forever before any
child process is spawned. Suspects: mojo bootstrap on NVIDIA/Wayland, or the
windowless path in this CEF version, but I could not pin it down further.

### Questions

1. Does a web wallpaper ever work for you on the gnome branch with CEF 135 on
   Wayland + NVIDIA? If yes, what's different in the setup?
2. Would you accept patches for layer 1 (explicit CEF resource paths) and
   layer 2 (`no_sandbox` for wallpaper renderers)? Happy to send them.

### Files touched in my local experiment

- `src/WallpaperEngine/WebBrowser/WebBrowserContext.cpp` — explicit
  resources/locales/subprocess paths, `no_sandbox = true`,
  `multi_threaded_message_loop = true`
- `src/WallpaperEngine/WebBrowser/CEF/BrowserApp.cpp` — `--disable-gpu`
  switch (experimental)
- `src/WallpaperEngine/Render/Wallpapers/CWeb.cpp` — removed manual
  `CefDoMessageLoopWork()` (required by MT message loop)
