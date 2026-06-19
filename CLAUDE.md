# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Dev commands

Install in editable mode with dev dependencies (required for running tests):

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

Run the full test suite:

```bash
.venv/bin/pytest
```

Run a single test file or individual test:

```bash
.venv/bin/pytest tests/test_clipboard.py
.venv/bin/pytest tests/test_watcher.py::test_wait_for_stable_returns_true
```

Run the service manually (config file is optional; env var overrides `watch_dir`):

```bash
CB_PHOTO_LOADER_WATCH_DIR=/mnt/d/Downloads .venv/bin/cb-photo-loader
```

## Architecture

### Data flow

```
config.py (load_config)
    └─> watcher.py (run_observer / ImageHandler)
            └─> clipboard.py (Clipboard.copy_image)
                    ├─> WindowsBackend.copy
                    ├─> LinuxBackend.copy
                    └─> Notifier.notify
```

`load_config` reads `~/.config/cb-photo-loader/config.toml` (with built-in defaults as fallback) and applies the `CB_PHOTO_LOADER_WATCH_DIR` env override. The resulting `Config` dataclass is passed to `run_observer`, which sets up a `watchdog` filesystem observer. For each relevant file event, `ImageHandler` calls `Clipboard.copy_image`.

**`run_observer` deliberately uses watchdog's `PollingObserver`, not the default inotify observer.** inotify does not deliver events for files on Windows drives (`/mnt/c`, DrvFs/9p) — and a Windows download folder is exactly what this tool watches — so an inotify observer silently sees nothing. `PollingObserver` stat-scans the directory (1s interval) and works on those mounts.

### Per-backend failure isolation

`Clipboard.copy_image` iterates over all backends (Windows, Linux) and catches exceptions from each individually. A failure in one backend (e.g. `powershell.exe` unavailable) is logged as a warning and does not prevent the other backend from running. The notifier is also wrapped in its own try/except. This means the service degrades gracefully in non-WSL or headless environments.

### File size-stability gate (`watcher.wait_for_stable`)

Before dispatching a new image to the clipboard, `ImageHandler` calls `wait_for_stable`. This polls the file size at `stability_ms` intervals and returns `True` only when the size is unchanged across one full interval (and `False` if the file disappears).

**Why this exists:** Phone and browser downloads write files incrementally. If the clipboard copy were triggered on the initial `created` event, it would read a partially written file, producing a corrupt image. The stability gate ensures the download is complete before copying.

### Windows clipboard: `SetDataObject($img, $true)`

`WindowsBackend.copy` calls PowerShell with:

```powershell
[System.Windows.Forms.Clipboard]::SetDataObject($img, $true)
```

The second argument (`$true`) is the `copy` flag that tells Windows to keep the clipboard data alive after the PowerShell process exits. Without it (`$false` or omitted), the image is placed on the clipboard but vanishes as soon as the PowerShell process terminates — leaving the clipboard empty by the time you try to paste.

### Linux/WSLg clipboard

`LinuxBackend.copy` prefers `wl-copy` (Wayland, used by WSLg) and falls back to `xclip` (X11). It passes the image bytes via stdin with the correct MIME type. If neither tool is found, a `RuntimeError` is raised (caught by the per-backend isolation above).

### Notifications (Windows toast)

`Notifier.notify` shows a **native Windows toast** (with a thumbnail) through PowerShell's WinRT API (`Windows.UI.Notifications`). WSLg runs no Linux notification daemon, so `notify-send` is deliberately not used. The dynamic values — image path, title, filename — are handed to PowerShell via environment variables (`CBPL_IMG` / `CBPL_TITLE` / `CBPL_BODY`) and XML-escaped inside the script (`SecurityElement::Escape`), never interpolated into the script text. This means filenames containing `'`, `&`, `<`, `>`, or `$` cannot break or inject into the toast. The toast XML lives in the `_TOAST_SCRIPT` constant in `clipboard.py`.

## Test strategy

All clipboard and notifier backends shell out to system tools (`powershell.exe`, `wl-copy`, `xclip`, `wslpath`). Tests mock these at the `subprocess.run` and `shutil.which` boundaries so the suite runs without any of those tools being installed. The `watchdog` observer is also replaced with stubs in watcher tests.
