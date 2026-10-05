# Upstream issue 草稿 —— kv9898/linux-wallpaperengine (gnome 分支)

> 发布方式（需 gh 已登录）：
>
> ```bash
> gh issue create --repo kv9898/linux-wallpaperengine \
>   --title "Schema v4 scene: shader compile failures, unresolved user textures, garbled text, misplaced layers" \
>   --body-file docs/upstream-issue-draft.md
> ```
>
> 草稿由本地调查（2026-10-05）整理，发布前可按需增删。

---

**Title:** Schema v4 scene: shader compile failures, unresolved user textures, garbled text, misplaced layers

**Body:**

### Summary

A complex "highly customizable" workshop wallpaper using the newer scene
schema (version 4, `objects`/`camera` structure) renders incorrectly: some
objects fail to set up with GLSL vertex shader compile errors and draw as
black quads, user textures referenced by passes cannot be resolved, text
layers render as mojibake, and the main character image layer is positioned
partially off-screen. The same wallpaper renders correctly in Wallpaper
Engine on Windows.

### Wallpaper

- Workshop item: 3351163962 (「[高度自定义] 樱花庄的宠物女孩 椎名真白」)
- scene.json schema **version 4**, 136 objects (clock, media visualizer,
  custom text layers, particle systems, camera parallax, bloom/HDR)
- Assets fully packed in `scene.pkg` (PKGV), including 5 custom TTF fonts
- `--list-properties` works and exposes 164 properties (27 sliders ship with `Step: 0`)

### Symptoms (full-res native screenshot reproduces all of them)

1. 9 objects fail to set up, drawing as black quads:

   ```text
   GLSL vertex unit parsing Failed: ERROR: 0:156: '#endif' : mismatched statements
   ERROR: 0:156: '' : compilation terminated
   ERROR: 2 compilation errors.  No code generated.
   (0) : error C5145: must write to gl_Position
   Failed to setup object 338: Vertex info
   (0) : error C5145: must write to gl_Position
   ```

2. User textures in passes cannot be resolved from the package:

   ```text
   Cannot resolve user texture pbr2 for pass filesystem error: Cannot find file: Success [pbr2]
   Cannot resolve user texture pbr3 for pass filesystem error: Cannot find file: Success [pbr3]
   ```

3. Text layers render as mojibake — UTF-8 text appears as Latin-1-like
   garbage (e.g. `"à!¯ è!² à®! à¹! æ!!"`), including inside object names
   shown by `--list-properties` with the pkg's bundled CJK fonts
   (fonts/演示秋鸿楷2.0.ttf etc.).

4. The character image layer sits at the bottom-left, mostly off-screen,
   while WE on Windows centers it. Reproduces identically in window
   screenshot mode at full monitor resolution (2560x1600), so it is not a
   compositor/scaling artifact.

### Environment

- Ubuntu 24.04, GNOME Shell 46, Wayland, NVIDIA RTX 4060 (proprietary driver)
- Built from `gnome` branch @ 6d7d838 (current HEAD)
- Reproduce: `linux-wallpaperengine -w 0x0x2560x1600 --bg 3351163962 --screenshot /tmp/out.png --screenshot-delay 12`

### Notes

- A different schema-v4 wallpaper (workshop 1149860629, simpler object
  tree) renders correctly on the same build, so this looks like specific
  v4 features (user textures in passes / text encoding / layer transforms)
  rather than v4 support being entirely absent.
- Happy to provide the parsed scene.json or additional logs if useful.
