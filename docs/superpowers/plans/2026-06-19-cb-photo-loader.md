# cb-photo-loader Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a background service that watches a configurable directory for newly downloaded images and copies the latest one to both the Windows and Linux/WSLg clipboards, with a desktop notification per copy.

**Architecture:** Four focused modules under `src/cb_photo_loader/` — `config` (TOML + env), `clipboard` (Windows/Linux backends + notifier + dispatcher), `watcher` (watchdog observer + file-stability gate), and `__main__` (wiring, logging, signals). A `systemctl --user` unit runs it as an always-on service.

**Tech Stack:** Python 3.11+ (stdlib `tomllib`, `subprocess`, `shutil`), `watchdog` for filesystem events, `pytest` for tests. System tools at runtime: `powershell.exe`, `wslpath`, `wl-clipboard`/`xclip`, `libnotify-bin`.

## Global Constraints

- Python `>=3.11` (requires stdlib `tomllib`).
- `src/` layout; package name `cb_photo_loader`; distribution name `cb-photo-loader`.
- Only runtime pip dependency is `watchdog>=4.0`. `pytest>=8.0` is dev-only.
- Default watch dir: `/mnt/temp/phone-downloads`. Default extensions: `png, jpg, jpeg, gif, bmp, webp`. Default `stability_ms = 750`. Default `notifications = true`.
- Config file path: `~/.config/cb-photo-loader/config.toml`. Missing file → all defaults.
- Env override: `CB_PHOTO_LOADER_WATCH_DIR` overrides `watch_dir` only.
- Push to both clipboards on every detection ("latest wins"); each backend failure is isolated and logged, never fatal.
- Console entry point: `cb-photo-loader` → `cb_photo_loader.__main__:main`.
- Use the project virtualenv at `.venv`; run tests with `.venv/bin/pytest`.

---

## File Structure

| File | Responsibility |
|------|----------------|
| `pyproject.toml` | Packaging, deps, entry point |
| `src/cb_photo_loader/__init__.py` | Package marker + version |
| `src/cb_photo_loader/config.py` | `Config` dataclass, `load_config()`, defaults, env override |
| `src/cb_photo_loader/clipboard.py` | `WindowsBackend`, `LinuxBackend`, `Notifier`, `Clipboard` dispatcher, `build_clipboard()` |
| `src/cb_photo_loader/watcher.py` | `wait_for_stable()`, `ImageHandler`, `run_observer()` |
| `src/cb_photo_loader/__main__.py` | `main()` — wiring, logging, signal handling |
| `tests/test_config.py` | Config tests |
| `tests/test_clipboard.py` | Backend + dispatcher tests (subprocess mocked) |
| `tests/test_watcher.py` | Stability + handler tests |
| `config.example.toml` | Sample config |
| `cb-photo-loader.service` | systemd `--user` unit |
| `README.md` | Install + usage + deployment |
| `CLAUDE.md` | Guidance for future Claude Code sessions |

---

## Task 1: Project scaffolding & packaging

**Files:**
- Create: `pyproject.toml`
- Create: `src/cb_photo_loader/__init__.py`
- Test: `tests/test_smoke.py`

**Interfaces:**
- Produces: importable package `cb_photo_loader` with `__version__: str`.

- [ ] **Step 1: Write the failing test**

`tests/test_smoke.py`:
```python
def test_package_imports():
    import cb_photo_loader
    assert isinstance(cb_photo_loader.__version__, str)
```

- [ ] **Step 2: Create the package marker**

`src/cb_photo_loader/__init__.py`:
```python
"""Watch a directory and copy newly downloaded images to the clipboard."""

__version__ = "0.1.0"
```

- [ ] **Step 3: Create `pyproject.toml`**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "cb-photo-loader"
version = "0.1.0"
description = "Watch a directory and copy newly downloaded images to the clipboard"
readme = "README.md"
requires-python = ">=3.11"
dependencies = ["watchdog>=4.0"]

[project.optional-dependencies]
dev = ["pytest>=8.0"]

[project.scripts]
cb-photo-loader = "cb_photo_loader.__main__:main"

[tool.hatch.build.targets.wheel]
packages = ["src/cb_photo_loader"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
```

- [ ] **Step 4: Create venv and install editable with dev deps**

Run:
```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```
Expected: installs `watchdog`, `pytest`, and `cb-photo-loader` without error.

- [ ] **Step 5: Run the test to verify it passes**

Run: `.venv/bin/pytest tests/test_smoke.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/cb_photo_loader/__init__.py tests/test_smoke.py
git commit -m "feat: scaffold cb-photo-loader package"
```

---

## Task 2: Configuration

**Files:**
- Create: `src/cb_photo_loader/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces:
  - `Config` — frozen dataclass with fields `watch_dir: Path`, `extensions: frozenset[str]` (lowercase, no leading dot), `stability_ms: int`, `notifications: bool`, and method `matches(path: Path) -> bool`.
  - `load_config(path: Path | None = None) -> Config` — loads from TOML (default path if `None`), applies defaults for missing keys, then applies the `CB_PHOTO_LOADER_WATCH_DIR` env override.
  - `DEFAULT_CONFIG_PATH: Path`.

- [ ] **Step 1: Write the failing tests**

`tests/test_config.py`:
```python
from pathlib import Path

from cb_photo_loader.config import Config, load_config


def test_defaults_when_file_missing(tmp_path):
    cfg = load_config(tmp_path / "nope.toml")
    assert cfg.watch_dir == Path("/mnt/temp/phone-downloads")
    assert cfg.extensions == frozenset({"png", "jpg", "jpeg", "gif", "bmp", "webp"})
    assert cfg.stability_ms == 750
    assert cfg.notifications is True


def test_parses_toml(tmp_path):
    f = tmp_path / "config.toml"
    f.write_text(
        'watch_dir = "/tmp/imgs"\n'
        'extensions = ["PNG", "jpg"]\n'
        "stability_ms = 1200\n"
        "notifications = false\n"
    )
    cfg = load_config(f)
    assert cfg.watch_dir == Path("/tmp/imgs")
    assert cfg.extensions == frozenset({"png", "jpg"})  # normalized lowercase
    assert cfg.stability_ms == 1200
    assert cfg.notifications is False


def test_env_overrides_watch_dir(tmp_path, monkeypatch):
    f = tmp_path / "config.toml"
    f.write_text('watch_dir = "/tmp/imgs"\n')
    monkeypatch.setenv("CB_PHOTO_LOADER_WATCH_DIR", "/env/dir")
    cfg = load_config(f)
    assert cfg.watch_dir == Path("/env/dir")


def test_matches_by_extension():
    cfg = Config(
        watch_dir=Path("/x"),
        extensions=frozenset({"png", "jpg"}),
        stability_ms=750,
        notifications=True,
    )
    assert cfg.matches(Path("/x/a.PNG")) is True
    assert cfg.matches(Path("/x/a.jpg")) is True
    assert cfg.matches(Path("/x/a.txt")) is False
    assert cfg.matches(Path("/x/noext")) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cb_photo_loader.config'`.

- [ ] **Step 3: Implement `config.py`**

`src/cb_photo_loader/config.py`:
```python
"""Configuration loading: TOML file + defaults + env override."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "cb-photo-loader" / "config.toml"

_DEFAULTS = {
    "watch_dir": "/mnt/temp/phone-downloads",
    "extensions": ["png", "jpg", "jpeg", "gif", "bmp", "webp"],
    "stability_ms": 750,
    "notifications": True,
}

_ENV_WATCH_DIR = "CB_PHOTO_LOADER_WATCH_DIR"


@dataclass(frozen=True)
class Config:
    watch_dir: Path
    extensions: frozenset[str]
    stability_ms: int
    notifications: bool

    def matches(self, path: Path) -> bool:
        ext = path.suffix.lower().lstrip(".")
        return bool(ext) and ext in self.extensions


def load_config(path: Path | None = None) -> Config:
    path = path or DEFAULT_CONFIG_PATH
    data = dict(_DEFAULTS)
    if path.exists():
        with path.open("rb") as fh:
            data.update(tomllib.load(fh))

    env_dir = os.environ.get(_ENV_WATCH_DIR)
    if env_dir:
        data["watch_dir"] = env_dir

    return Config(
        watch_dir=Path(data["watch_dir"]),
        extensions=frozenset(e.lower().lstrip(".") for e in data["extensions"]),
        stability_ms=int(data["stability_ms"]),
        notifications=bool(data["notifications"]),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_config.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/cb_photo_loader/config.py tests/test_config.py
git commit -m "feat: add config loading with defaults and env override"
```

---

## Task 3: Clipboard backends, notifier & dispatcher

**Files:**
- Create: `src/cb_photo_loader/clipboard.py`
- Test: `tests/test_clipboard.py`

**Interfaces:**
- Consumes: `Config` from `cb_photo_loader.config`.
- Produces:
  - `WindowsBackend` with `name = "windows"` and `copy(image_path: Path) -> None`.
  - `LinuxBackend` with `name = "linux"` and `copy(image_path: Path) -> None`.
  - `Notifier` with `notify(image_path: Path) -> None`.
  - `Clipboard(backends: list, notifier=None)` with `copy_image(path: Path) -> list[str]` (returns names of backends that succeeded; fires notifier only if at least one succeeded).
  - `build_clipboard(config: Config) -> Clipboard`.

- [ ] **Step 1: Write the failing tests**

`tests/test_clipboard.py`:
```python
from pathlib import Path

import cb_photo_loader.clipboard as clip
from cb_photo_loader.clipboard import (
    Clipboard,
    LinuxBackend,
    Notifier,
    WindowsBackend,
)


def test_windows_backend_runs_powershell(monkeypatch):
    calls = []

    def fake_run(cmd, **kw):
        calls.append((cmd, kw))

        class R:
            stdout = "C:\\imgs\\a.png\n"

        return R()

    monkeypatch.setattr(clip.subprocess, "run", fake_run)
    WindowsBackend().copy(Path("/mnt/c/imgs/a.png"))

    assert calls[0][0] == ["wslpath", "-w", "/mnt/c/imgs/a.png"]
    ps = calls[1][0]
    assert ps[0] == "powershell.exe"
    joined = " ".join(ps)
    assert "SetDataObject" in joined
    assert "C:\\imgs\\a.png" in joined


def test_linux_backend_streams_bytes_to_wl_copy(monkeypatch, tmp_path):
    img = tmp_path / "a.png"
    img.write_bytes(b"PNGDATA")
    monkeypatch.setattr(clip.shutil, "which", lambda t: "/usr/bin/wl-copy" if t == "wl-copy" else None)
    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        captured["input"] = kw.get("input")

    monkeypatch.setattr(clip.subprocess, "run", fake_run)
    LinuxBackend().copy(img)

    assert captured["cmd"] == ["wl-copy", "--type", "image/png"]
    assert captured["input"] == b"PNGDATA"


def test_linux_backend_falls_back_to_xclip(monkeypatch, tmp_path):
    img = tmp_path / "a.jpg"
    img.write_bytes(b"JPG")
    monkeypatch.setattr(clip.shutil, "which", lambda t: "/usr/bin/xclip" if t == "xclip" else None)
    captured = {}
    monkeypatch.setattr(clip.subprocess, "run", lambda cmd, **kw: captured.update(cmd=cmd))
    LinuxBackend().copy(img)

    assert captured["cmd"] == ["xclip", "-selection", "clipboard", "-t", "image/jpeg"]


def test_linux_backend_raises_when_no_tool(monkeypatch, tmp_path):
    img = tmp_path / "a.png"
    img.write_bytes(b"X")
    monkeypatch.setattr(clip.shutil, "which", lambda t: None)
    try:
        LinuxBackend().copy(img)
        assert False, "expected RuntimeError"
    except RuntimeError:
        pass


def test_dispatcher_isolates_failures_and_notifies(monkeypatch, tmp_path):
    img = tmp_path / "a.png"
    img.write_bytes(b"X")

    class Good:
        name = "good"

        def copy(self, p):
            pass

    class Bad:
        name = "bad"

        def copy(self, p):
            raise RuntimeError("boom")

    notified = []

    class FakeNotifier:
        def notify(self, p):
            notified.append(p)

    cb = Clipboard([Bad(), Good()], notifier=FakeNotifier())
    succeeded = cb.copy_image(img)

    assert succeeded == ["good"]
    assert notified == [img]


def test_dispatcher_skips_notify_when_all_fail(tmp_path):
    img = tmp_path / "a.png"
    img.write_bytes(b"X")

    class Bad:
        name = "bad"

        def copy(self, p):
            raise RuntimeError("boom")

    notified = []

    class FakeNotifier:
        def notify(self, p):
            notified.append(p)

    cb = Clipboard([Bad()], notifier=FakeNotifier())
    assert cb.copy_image(img) == []
    assert notified == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_clipboard.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cb_photo_loader.clipboard'`.

- [ ] **Step 3: Implement `clipboard.py`**

`src/cb_photo_loader/clipboard.py`:
```python
"""Clipboard backends (Windows + Linux), desktop notifier, and dispatcher."""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

_MIME = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "bmp": "image/bmp",
    "webp": "image/webp",
}


def _mime_for(path: Path) -> str:
    return _MIME.get(path.suffix.lower().lstrip("."), "image/png")


class WindowsBackend:
    name = "windows"

    def copy(self, image_path: Path) -> None:
        win_path = subprocess.run(
            ["wslpath", "-w", str(image_path)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        script = (
            "Add-Type -AssemblyName System.Windows.Forms,System.Drawing; "
            f"$img = [System.Drawing.Image]::FromFile('{win_path}'); "
            "[System.Windows.Forms.Clipboard]::SetDataObject($img, $true)"
        )
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", script],
            check=True,
            capture_output=True,
        )


class LinuxBackend:
    name = "linux"

    def copy(self, image_path: Path) -> None:
        mime = _mime_for(image_path)
        data = image_path.read_bytes()
        if shutil.which("wl-copy"):
            cmd = ["wl-copy", "--type", mime]
        elif shutil.which("xclip"):
            cmd = ["xclip", "-selection", "clipboard", "-t", mime]
        else:
            raise RuntimeError("no Linux clipboard tool found (install wl-clipboard or xclip)")
        subprocess.run(cmd, input=data, check=True)


class Notifier:
    def notify(self, image_path: Path) -> None:
        if not shutil.which("notify-send"):
            log.warning("notify-send not found; skipping notification")
            return
        subprocess.run(
            ["notify-send", "-i", str(image_path), "Image copied", image_path.name],
            check=True,
        )


class Clipboard:
    def __init__(self, backends, notifier=None):
        self.backends = backends
        self.notifier = notifier

    def copy_image(self, path: Path) -> list[str]:
        succeeded: list[str] = []
        for backend in self.backends:
            try:
                backend.copy(path)
                succeeded.append(backend.name)
                log.info("copied %s to %s clipboard", path.name, backend.name)
            except Exception as exc:  # isolate per-backend failures
                log.warning("%s backend failed for %s: %s", backend.name, path.name, exc)
        if succeeded and self.notifier is not None:
            try:
                self.notifier.notify(path)
            except Exception as exc:
                log.warning("notification failed for %s: %s", path.name, exc)
        return succeeded


def build_clipboard(config) -> Clipboard:
    notifier = Notifier() if config.notifications else None
    return Clipboard([WindowsBackend(), LinuxBackend()], notifier=notifier)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_clipboard.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/cb_photo_loader/clipboard.py tests/test_clipboard.py
git commit -m "feat: add clipboard backends, notifier and dispatcher"
```

---

## Task 4: Watcher (stability gate + event handler)

**Files:**
- Create: `src/cb_photo_loader/watcher.py`
- Test: `tests/test_watcher.py`

**Interfaces:**
- Consumes: `Config` from `cb_photo_loader.config`.
- Produces:
  - `wait_for_stable(path: Path, stability_ms: int, *, sleep=time.sleep, get_size=None) -> bool` — `True` once size is unchanged across one interval; `False` if the file disappears.
  - `ImageHandler(config: Config, on_ready: Callable[[Path], None])` — a `watchdog` `FileSystemEventHandler` reacting to created and moved-in image files.
  - `run_observer(config: Config, on_ready) -> Observer` — schedules an `ImageHandler` on `config.watch_dir` (non-recursive) and starts it.

- [ ] **Step 1: Write the failing tests**

`tests/test_watcher.py`:
```python
from pathlib import Path
from types import SimpleNamespace

from cb_photo_loader.config import Config
from cb_photo_loader.watcher import ImageHandler, wait_for_stable


def _cfg():
    return Config(
        watch_dir=Path("/x"),
        extensions=frozenset({"png", "jpg"}),
        stability_ms=10,
        notifications=False,
    )


def test_wait_for_stable_settles():
    sizes = iter([100, 200, 200])
    ok = wait_for_stable(Path("x"), 10, sleep=lambda s: None, get_size=lambda: next(sizes))
    assert ok is True


def test_wait_for_stable_returns_false_when_deleted():
    def boom():
        raise FileNotFoundError

    assert wait_for_stable(Path("x"), 10, sleep=lambda s: None, get_size=boom) is False


def test_handler_calls_on_ready_for_matching_created(monkeypatch):
    ready = []
    handler = ImageHandler(_cfg(), ready.append)
    monkeypatch.setattr("cb_photo_loader.watcher.wait_for_stable", lambda *a, **k: True)
    handler.on_created(SimpleNamespace(is_directory=False, src_path="/x/a.png"))
    assert ready == [Path("/x/a.png")]


def test_handler_ignores_non_image(monkeypatch):
    ready = []
    handler = ImageHandler(_cfg(), ready.append)
    monkeypatch.setattr("cb_photo_loader.watcher.wait_for_stable", lambda *a, **k: True)
    handler.on_created(SimpleNamespace(is_directory=False, src_path="/x/a.txt"))
    assert ready == []


def test_handler_skips_unstable_file(monkeypatch):
    ready = []
    handler = ImageHandler(_cfg(), ready.append)
    monkeypatch.setattr("cb_photo_loader.watcher.wait_for_stable", lambda *a, **k: False)
    handler.on_created(SimpleNamespace(is_directory=False, src_path="/x/a.png"))
    assert ready == []


def test_handler_handles_moved_in_file(monkeypatch):
    ready = []
    handler = ImageHandler(_cfg(), ready.append)
    monkeypatch.setattr("cb_photo_loader.watcher.wait_for_stable", lambda *a, **k: True)
    handler.on_moved(SimpleNamespace(is_directory=False, dest_path="/x/final.jpg"))
    assert ready == [Path("/x/final.jpg")]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_watcher.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cb_photo_loader.watcher'`.

- [ ] **Step 3: Implement `watcher.py`**

`src/cb_photo_loader/watcher.py`:
```python
"""Filesystem watcher with a file-completion (size-stability) gate."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Callable

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

log = logging.getLogger(__name__)


def wait_for_stable(path: Path, stability_ms: int, *, sleep=time.sleep, get_size=None) -> bool:
    """Return True once the file size is unchanged across one interval.

    Returns False if the file disappears (e.g. a temp download renamed away).
    """
    interval = stability_ms / 1000.0
    if get_size is None:
        def get_size():
            return path.stat().st_size

    try:
        prev = get_size()
    except FileNotFoundError:
        return False

    while True:
        sleep(interval)
        try:
            cur = get_size()
        except FileNotFoundError:
            return False
        if cur == prev:
            return True
        prev = cur


class ImageHandler(FileSystemEventHandler):
    def __init__(self, config, on_ready: Callable[[Path], None]):
        self.config = config
        self.on_ready = on_ready

    def _handle(self, raw_path: str) -> None:
        path = Path(raw_path)
        if not self.config.matches(path):
            return
        if wait_for_stable(path, self.config.stability_ms):
            log.info("image ready: %s", path)
            self.on_ready(path)
        else:
            log.debug("file not stable, skipped: %s", path)

    def on_created(self, event) -> None:
        if not event.is_directory:
            self._handle(event.src_path)

    def on_moved(self, event) -> None:
        if not event.is_directory:
            self._handle(event.dest_path)


def run_observer(config, on_ready: Callable[[Path], None]) -> Observer:
    observer = Observer()
    observer.schedule(ImageHandler(config, on_ready), str(config.watch_dir), recursive=False)
    observer.start()
    log.info("watching %s", config.watch_dir)
    return observer
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_watcher.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/cb_photo_loader/watcher.py tests/test_watcher.py
git commit -m "feat: add filesystem watcher with stability gate"
```

---

## Task 5: Service entry point

**Files:**
- Create: `src/cb_photo_loader/__main__.py`
- Test: manual (documented below)

**Interfaces:**
- Consumes: `load_config`, `build_clipboard`, `run_observer`.
- Produces: `main() -> None` — the console-script entry point.

- [ ] **Step 1: Implement `__main__.py`**

`src/cb_photo_loader/__main__.py`:
```python
"""Service entry point: wire config -> watcher -> clipboard, run until signalled."""

from __future__ import annotations

import logging
import signal
import threading

from cb_photo_loader.clipboard import build_clipboard
from cb_photo_loader.config import load_config
from cb_photo_loader.watcher import run_observer

log = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    config = load_config()
    clipboard = build_clipboard(config)

    if not config.watch_dir.is_dir():
        log.warning("watch dir does not exist yet: %s", config.watch_dir)

    observer = run_observer(config, clipboard.copy_image)

    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())

    try:
        stop.wait()
    finally:
        log.info("shutting down")
        observer.stop()
        observer.join()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify the full suite still passes**

Run: `.venv/bin/pytest -v`
Expected: PASS (all tests from Tasks 1–4).

- [ ] **Step 3: Manual smoke test**

Run (in one terminal):
```bash
mkdir -p /tmp/cb-test
CB_PHOTO_LOADER_WATCH_DIR=/tmp/cb-test .venv/bin/cb-photo-loader
```
In another terminal, copy a PNG in:
```bash
cp /path/to/some.png /tmp/cb-test/
```
Expected: log line `copied some.png to ... clipboard`; a desktop notification appears; pasting into a Windows app and a Linux app yields the image. Ctrl-C exits cleanly with `shutting down`.

- [ ] **Step 4: Commit**

```bash
git add src/cb_photo_loader/__main__.py
git commit -m "feat: add service entry point with signal handling"
```

---

## Task 6: Deployment artifacts & docs

**Files:**
- Create: `config.example.toml`
- Create: `cb-photo-loader.service`
- Create: `README.md`
- Create: `CLAUDE.md`

- [ ] **Step 1: Create `config.example.toml`**

```toml
# Copy to ~/.config/cb-photo-loader/config.toml and edit.
watch_dir = "/mnt/temp/phone-downloads"
extensions = ["png", "jpg", "jpeg", "gif", "bmp", "webp"]
stability_ms = 750
notifications = true
```

- [ ] **Step 2: Create the systemd unit**

`cb-photo-loader.service`:
```ini
[Unit]
Description=Clipboard photo loader — watch a directory and copy new images to the clipboard
After=graphical-session.target

[Service]
ExecStart=%h/.local/bin/cb-photo-loader
Restart=on-failure
RestartSec=2

[Install]
WantedBy=default.target
```

- [ ] **Step 3: Create `README.md`**

Include: what it does; install (`pipx install .` or `python3 -m venv` + `pip install .`); config file location and keys; the `CB_PHOTO_LOADER_WATCH_DIR` override; systemd install steps (`mkdir -p ~/.config/systemd/user && cp cb-photo-loader.service ~/.config/systemd/user/ && systemctl --user daemon-reload && systemctl --user enable --now cb-photo-loader && journalctl --user -u cb-photo-loader -f`); the `nohup ~/.local/bin/cb-photo-loader &` fallback when systemd is unavailable; system tool prerequisites (`wl-clipboard` or `xclip`, `libnotify-bin`, WSL interop for `powershell.exe`).

- [ ] **Step 4: Create `CLAUDE.md`**

Prefix exactly with:
```
# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.
```
Then cover: the dev commands (`.venv/bin/pytest`, running a single test with `::`, editable install, running the service with `CB_PHOTO_LOADER_WATCH_DIR=...`); the architecture (config → watcher → clipboard dispatcher data flow; per-backend failure isolation; the size-stability gate and *why* it exists; Windows `SetDataObject($img, $true)` persistence gotcha; WSLg uses `wl-copy`); and that backends shell out to system tools mocked in tests.

- [ ] **Step 5: Run the full suite once more**

Run: `.venv/bin/pytest -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add config.example.toml cb-photo-loader.service README.md CLAUDE.md
git commit -m "docs: add deployment artifacts, README and CLAUDE.md"
```

---

## Task 7: Publish to GitHub

**Files:** none (git operations only).

The remote already exists (empty) at `https://github.com/nickweedon/cb-photo-loader.git`.

- [ ] **Step 1: Add the remote (skip if already present)**

```bash
git remote add origin https://github.com/nickweedon/cb-photo-loader.git || git remote set-url origin https://github.com/nickweedon/cb-photo-loader.git
git remote -v
```
Expected: `origin` points at the GitHub URL.

- [ ] **Step 2: Push `main` and set upstream**

```bash
git push -u origin main
```
Expected: all commits land on GitHub; `main` tracks `origin/main`.

If the push fails on auth: HTTPS pushes need a credential helper or a Personal Access Token. The user runs `! git push -u origin main` in this session (or configures `git config --global credential.helper store` and retries) so the credential prompt is handled interactively.

- [ ] **Step 3: Verify**

```bash
git status -sb
```
Expected: `## main...origin/main` with nothing to push.

---

## Self-Review

**Spec coverage:**
- Watch configurable dir → Task 2 (`watch_dir` + env), Task 4 (`run_observer`). ✓
- Common image types, latest-wins → Task 2 (`extensions`/`matches`), Task 4 (per-event handling). ✓
- File-completion detection → Task 4 (`wait_for_stable`). ✓
- Both clipboards + persistence gotcha → Task 3 (`WindowsBackend` `SetDataObject($img,$true)`, `LinuxBackend` wl-copy/xclip). ✓
- Notifications with thumbnail → Task 3 (`Notifier` `notify-send -i <path>`). ✓
- Config file + `stability_ms` + `notifications` + env override → Task 2. ✓
- systemd `--user` service + nohup fallback → Task 6. ✓
- Packaging + entry point → Task 1. ✓
- Testing (config/watcher/clipboard, subprocess mocked) → Tasks 2–4. ✓
- GitHub publish (added per user request) → Task 7. ✓

**Placeholder scan:** Task 6 steps 3–4 describe README/CLAUDE.md contents rather than verbatim prose — acceptable for free-form docs; every key point to include is enumerated. No `TBD`/`TODO` in code steps.

**Type consistency:** `Config(watch_dir, extensions, stability_ms, notifications)` + `matches()` used identically across Tasks 2/3/4. `copy_image()`, `copy()`, `notify()`, `wait_for_stable()`, `run_observer()` signatures match between definition and call sites. `build_clipboard()` consumed by Task 5. ✓
