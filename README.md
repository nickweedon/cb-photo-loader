# cb-photo-loader

A background service that watches a directory for newly downloaded images and automatically copies the latest one to both the Windows clipboard and the Linux/WSLg clipboard, then fires a desktop notification.

It is designed for WSL2 workflows where you download photos on a phone or browser and want them immediately available for pasting in Windows applications.

## What it does

1. Watches a configured directory for new image files (created or moved in).
2. Waits for each file to finish writing (size-stability gate) before acting.
3. Copies the image to the **Windows clipboard** via PowerShell and to the **Linux/WSLg clipboard** via `wl-copy` or `xclip`.
4. Sends a desktop notification via `notify-send`.

## Prerequisites

The following system tools must be installed and on `$PATH`:

| Tool | Package | Purpose |
|---|---|---|
| `wl-copy` | `wl-clipboard` | Linux/WSLg clipboard (preferred) |
| `xclip` | `xclip` | Linux clipboard fallback if `wl-copy` is absent |
| `notify-send` | `libnotify-bin` | Desktop notifications |
| `powershell.exe` | WSL interop | Windows clipboard |
| `wslpath` | WSL interop | Path translation for Windows clipboard |

Install the Linux tools on Ubuntu/Debian:

```bash
sudo apt install wl-clipboard libnotify-bin
```

`powershell.exe` and `wslpath` are provided by WSL interop and are available automatically in a standard WSL2 installation.

**Note:** `wl-clipboard` and `libnotify-bin` are NOT always installed by default. The service will log warnings and skip the corresponding step if a tool is missing, but you must have at least one clipboard tool present for clipboard copying to work.

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
| `notifications` | `true` | Whether to send desktop notifications via `notify-send` |

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

Follow the logs:

```bash
journalctl --user -u cb-photo-loader -f
```

## Running without systemd

If systemd is not available (e.g. older WSL setups):

```bash
nohup ~/.local/bin/cb-photo-loader &
```

## Known limitations

1. Rapid successive downloads are processed serially because the stability wait runs on the watcher's dispatch thread. Under a burst of incoming files the clipboard ends up holding whichever file finishes its stability check last, not strictly the newest one.
2. A download that stalls mid-write for longer than `stability_ms` can be treated as complete and copied while still truncated. Increasing `stability_ms` reduces this risk but does not eliminate it entirely.
