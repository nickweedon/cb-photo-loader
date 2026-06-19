# cb-photo-loader

A background service that watches a directory for newly downloaded images and automatically copies the latest one to both the Windows clipboard and the Linux/WSLg clipboard, then fires a native Windows toast notification.

It is designed for WSL2 workflows where you download photos on a phone or browser and want them immediately available for pasting in Windows applications.

## What it does

1. Watches a configured directory for new image files (created or moved in).
2. Waits for each file to finish writing (size-stability gate) before acting.
3. Copies the image to the **Windows clipboard** via PowerShell and to the **Linux/WSLg clipboard** via `wl-copy` or `xclip`.
4. Shows a native Windows toast notification (with a thumbnail of the image) via PowerShell's WinRT notification API.

## Prerequisites

The following system tools must be installed and on `$PATH`:

| Tool | Package | Purpose |
|---|---|---|
| `wl-copy` | `wl-clipboard` | Linux/WSLg clipboard (preferred) |
| `xclip` | `xclip` | Linux clipboard fallback if `wl-copy` is absent |
| `powershell.exe` | WSL interop | Windows clipboard **and** toast notifications |
| `wslpath` | WSL interop | Path translation for the Windows clipboard and toast |

Install the Linux clipboard tools on Ubuntu/Debian:

```bash
sudo apt install wl-clipboard
```

`powershell.exe` and `wslpath` are provided by WSL interop and are available automatically in a standard WSL2 installation. Notifications use the native Windows toast system through PowerShell, so no Linux notification daemon (e.g. `libnotify-bin`/`notify-send`) is required — WSLg does not run one anyway.

**Note:** `wl-clipboard` is NOT always installed by default. The service logs a warning and skips any backend whose tool is missing (failures are isolated), but you need at least one Linux clipboard tool present for the Linux clipboard copy to work.

## Installation

### Option A — pipx (recommended for isolation)

```bash
pipx install .
```

### Option B — virtual environment

```bash
python3 -m venv .venv
.venv/bin/pip install .
```

The `cb-photo-loader` command is installed to `~/.local/bin/` (pipx) or `.venv/bin/` (venv).

## Configuration

The service reads `~/.config/cb-photo-loader/config.toml` on startup. If the file does not exist, built-in defaults are used.

Copy the example and edit it:

```bash
mkdir -p ~/.config/cb-photo-loader
cp config.example.toml ~/.config/cb-photo-loader/config.toml
```

### Configuration keys

| Key | Default | Description |
|---|---|---|
| `watch_dir` | `/mnt/temp/phone-downloads` | Directory to watch for new images |
| `extensions` | `["png","jpg","jpeg","gif","bmp","webp"]` | File extensions to act on (case-insensitive) |
| `stability_ms` | `750` | Milliseconds to wait for file size to stabilise before copying |
| `notifications` | `true` | Whether to show a Windows toast notification per copy |

Example `~/.config/cb-photo-loader/config.toml`:

```toml
watch_dir = "/mnt/d/Downloads"
extensions = ["png", "jpg", "jpeg"]
stability_ms = 500
notifications = true
```

### Environment variable override

`CB_PHOTO_LOADER_WATCH_DIR` overrides `watch_dir` from the config file without editing it:

```bash
CB_PHOTO_LOADER_WATCH_DIR=/mnt/d/Downloads cb-photo-loader
```

## Running as a systemd user service (recommended)

```bash
mkdir -p ~/.config/systemd/user
cp cb-photo-loader.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now cb-photo-loader
```

If you installed into a virtualenv rather than with pipx, edit the `ExecStart`
line in the copied unit to point at your venv binary (e.g.
`/path/to/cb-photo-loader/.venv/bin/cb-photo-loader`) before `daemon-reload`.

Follow the logs:

```bash
journalctl --user -u cb-photo-loader -f
```

### Why the unit sets extra environment (WSL)

A systemd `--user` service does **not** inherit your interactive shell's
environment, and the defaults break both backends under WSL. The shipped unit
therefore sets:

- **`PATH`** including the Windows directories, so `powershell.exe` resolves —
  it drives both the Windows clipboard and the toast notifications.
- **`WAYLAND_DISPLAY` / `DISPLAY`**, needed by the Linux/WSLg clipboard.

`WSL_INTEROP` is **not** pinned in the unit: it is tied to a WSL session and
goes stale when WSL restarts, so the app re-resolves a live one at runtime (by
scanning `/proc`) before each PowerShell call. This means the service keeps
working across `wsl --shutdown` / reboots with no manual steps. The service
starts when you open WSL (no `loginctl enable-linger` required).

## Running without systemd

If systemd is not available (e.g. older WSL setups):

```bash
nohup ~/.local/bin/cb-photo-loader &
```

## Known limitations

1. Rapid successive downloads are processed serially because the stability wait runs on the watcher's dispatch thread. Under a burst of incoming files the clipboard ends up holding whichever file finishes its stability check last, not strictly the newest one.
2. A download that stalls mid-write for longer than `stability_ms` can be treated as complete and copied while still truncated. Increasing `stability_ms` reduces this risk but does not eliminate it entirely.
3. Detection is poll-based (the directory is scanned about once a second), not event-driven. This is required because inotify delivers no events for files on Windows drives (`/mnt/c`, DrvFs/9p), which are exactly the folders this tool watches. The practical effect is up to roughly a second of latency between a file appearing and being copied.
