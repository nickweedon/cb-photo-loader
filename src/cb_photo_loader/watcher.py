"""Filesystem watcher with a file-completion (size-stability) gate."""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Callable

from watchdog.events import FileSystemEventHandler
from watchdog.observers.polling import PollingObserver

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


Signature = tuple[int, int]


def signature(path: Path, *, stat=os.stat) -> Signature | None:
    """(size, mtime_ns) identifies a file's content across polling glitches."""
    try:
        st = stat(path)
    except OSError:
        return None
    return (st.st_size, st.st_mtime_ns)


def list_existing(
    watch_dir: Path, *, listdir=os.listdir, stat=os.stat, sleep=time.sleep, retry_s: float = 2.0
) -> dict[Path, Signature]:
    """Return the files already in watch_dir with their signatures.

    Retries until the directory can be listed: at boot the /mnt/c DrvFs mount
    can briefly fail to list (EINVAL/ENOENT), which watchdog silently treats as
    an empty directory.
    """
    while True:
        try:
            names = listdir(watch_dir)
        except OSError as exc:
            log.warning("cannot list %s yet (%s); retrying", watch_dir, exc)
            sleep(retry_s)
            continue
        found = {}
        for name in names:
            path = watch_dir / name
            sig = signature(path, stat=stat)
            if sig is not None:
                found[path] = sig
        return found


class ImageHandler(FileSystemEventHandler):
    """Dispatch each image once per distinct content.

    watchdog's polling diff is not trustworthy on /mnt/c: around boot and
    Windows sign-in the directory can briefly list as empty, or report changed
    inodes, so every existing file is reported deleted and then created again.
    Identity is therefore (path, size, mtime), not watchdog's events: a file
    that reappears unchanged is ignored, and delete events forget nothing. A
    genuinely new file reusing a name has a new mtime and is dispatched.
    """

    def __init__(
        self,
        config,
        on_ready: Callable[[Path], None],
        existing: dict[Path, Signature] | None = None,
        *,
        stat=os.stat,
    ):
        self.config = config
        self.on_ready = on_ready
        self._seen: dict[Path, Signature] = dict(existing or {})
        self._stat = stat
        self._lock = threading.Lock()

    def _handle(self, raw_path: str) -> None:
        path = Path(raw_path)
        if not self.config.matches(path):
            return
        if not wait_for_stable(path, self.config.stability_ms):
            log.debug("file not stable, skipped: %s", path)
            return
        sig = signature(path, stat=self._stat)
        if sig is None:
            return
        with self._lock:
            if self._seen.get(path) == sig:
                log.debug("unchanged, already seen: %s", path)
                return
            self._seen[path] = sig
        log.info("image ready: %s", path)
        self.on_ready(path)

    def on_created(self, event) -> None:
        if not event.is_directory:
            self._handle(event.src_path)

    def on_moved(self, event) -> None:
        if not event.is_directory:
            self._handle(event.dest_path)


def run_observer(config, on_ready: Callable[[Path], None], *, poll_interval: float = 1.0) -> PollingObserver:
    # A PollingObserver (stat-based scanning) is required, not the default
    # inotify observer: inotify does not deliver events for files on Windows
    # drives (/mnt/c, DrvFs/9p), which is exactly where this tool watches.
    existing = list_existing(config.watch_dir)
    log.info("ignoring %d file(s) already in %s", len(existing), config.watch_dir)
    observer = PollingObserver(timeout=poll_interval)
    observer.schedule(ImageHandler(config, on_ready, existing), str(config.watch_dir), recursive=False)
    observer.start()
    log.info("watching %s", config.watch_dir)
    return observer
