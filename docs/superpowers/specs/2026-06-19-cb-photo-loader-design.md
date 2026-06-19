# cb-photo-loader — Design

**Date:** 2026-06-19
**Status:** Approved (pending spec review)

## Purpose

A background service that watches a directory (default `/mnt/temp/phone-downloads`)
for newly downloaded image files and automatically places the most recent one on
the clipboard so it can be pasted immediately. Targets a WSL2 environment: images
are pushed to **both** the Windows clipboard (for Windows apps) and the Linux/WSLg
clipboard (for Linux GUI apps), and a desktop notification with a thumbnail is
shown on each copy.

## Requirements

- Watch a configurable directory for new image files.
- Recognize common image types: `png`, `jpg`, `jpeg`, `gif`, `bmp`, `webp`.
- "Latest wins": each newly completed image replaces the clipboard contents.
- Copy to **both** the Windows clipboard and the Linux clipboard on each detection.
- Show a desktop notification per copy, using the image as the notification icon.
- Run as a long-lived `systemctl --user` service that auto-starts.
- Watch directory must be configurable (config file + environment override).

## Key Technical Decisions

### 1. File-completion detection
Phone sync / browser downloads write files incrementally. Acting mid-write copies a
corrupt image. The watcher therefore waits for the file size to remain unchanged for
a configurable interval (`stability_ms`, default 750ms) before treating the file as
ready. Files that disappear during the wait (e.g. temp `.crdownload` renamed away)
are dropped.

### 2. Windows clipboard persistence
Calling `[System.Windows.Forms.Clipboard]::SetImage` loses the data when the
PowerShell process exits. The implementation uses
`[System.Windows.Forms.Clipboard]::SetDataObject($img, $true)` — the second argument
(`copy = $true`) flushes the data so it persists after the process exits. WSL paths
are converted to Windows paths with `wslpath -w` before being handed to PowerShell.

### 3. Linux/WSLg clipboard
WSLg presents a Wayland session, so the Linux backend prefers
`wl-copy --type image/png` (from `wl-clipboard`). If `wl-copy` is unavailable it
falls back to `xclip -selection clipboard -t image/png`. The image bytes are streamed
to the tool via stdin.

### 4. Notifications
`notify-send` (libnotify) works under WSLg. Each successful copy fires
`notify-send -i <image-path> "Image copied" "<filename>"`, so the notification shows
a thumbnail of the copied image. If `notify-send` is missing, notification is skipped
with a logged warning (non-fatal).

## Architecture

Four small, independently testable units under `src/cb_photo_loader/`:

| Unit | Responsibility | Depends on |
|------|----------------|------------|
| `config.py` | Load `watch_dir`, `extensions`, `stability_ms`, `notifications` from a TOML file with defaults; apply env-var overrides. | stdlib `tomllib` |
| `watcher.py` | `watchdog` observer on the watch dir; filter by extension; wait for size stability; emit "image ready" callbacks. | `watchdog` |
| `clipboard.py` | `WindowsBackend` + `LinuxBackend` + `NotifyBackend`; a `Clipboard` dispatcher invokes each available backend and logs per-backend success/failure. | stdlib `subprocess` |
| `__main__.py` | Wire config → watcher → clipboard dispatcher; configure logging; handle SIGTERM/SIGINT for clean shutdown. | the above |

### Data flow

```
new file in watch_dir
  → extension matches configured set?
  → size-stability poll (stable for stability_ms)
  → Clipboard.copy_image(path):
       ├─ WindowsBackend: wslpath -w → PowerShell SetDataObject($img, $true)
       ├─ LinuxBackend:   wl-copy --type image/png  (fallback xclip)
       └─ NotifyBackend:  notify-send -i <path> "Image copied" "<name>"
  → log per-backend result
```

Each backend failure is isolated: a failure in one backend (e.g. no X/Wayland
display) does not prevent the others from running.

## Configuration

File: `~/.config/cb-photo-loader/config.toml`

```toml
watch_dir = "/mnt/temp/phone-downloads"
extensions = ["png", "jpg", "jpeg", "gif", "bmp", "webp"]
stability_ms = 750
notifications = true
```

Environment override: `CB_PHOTO_LOADER_WATCH_DIR` overrides `watch_dir` (lets the
systemd unit point at a directory without editing the config file). Missing config
file → all defaults are used.

## Deployment

Ships a `cb-photo-loader.service` unit installed to `~/.config/systemd/user/`:

```ini
[Unit]
Description=Clipboard photo loader — watch a directory and copy new images to the clipboard
After=graphical-session.target

[Service]
ExecStart=%h/.local/bin/cb-photo-loader
Restart=on-failure

[Install]
WantedBy=default.target
```

Run as a **user** service so it inherits the WSLg `WAYLAND_DISPLAY` / `DISPLAY`
environment needed for the Linux clipboard and notifications. Enable with
`systemctl --user enable --now cb-photo-loader`.

Fallback: if systemd is unavailable, the same entry point can be run under `nohup`
(documented in the README).

## Packaging

- `pyproject.toml`, `src/cb_photo_loader/` layout.
- Console entry point: `cb-photo-loader` → `cb_photo_loader.__main__:main`.
- Runtime dependency: `watchdog`. Dev dependency: `pytest`.
- System tools expected at runtime (not pip-installed): `powershell.exe` (WSL interop),
  `wslpath`, `wl-clipboard` or `xclip`, `libnotify-bin`.

## Testing (pytest)

- **config**: defaults when file absent; TOML parsing; env override precedence.
- **watcher**: extension filtering; stability poll (file growing then settling;
  file deleted mid-wait).
- **clipboard**: each backend mocks `subprocess` and asserts the exact command /
  stdin bytes — no real clipboard touched. Dispatcher continues after one backend
  raises.

Real-clipboard and real-WSLg behavior are verified manually, not in CI.

## Out of Scope (YAGNI)

- Clipboard history / queue (latest-wins only).
- Watching multiple directories.
- Non-image file types.
- A GUI / system-tray icon (notifications cover feedback).
