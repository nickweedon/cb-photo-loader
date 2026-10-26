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


def list_existing(watch_dir: Path, *, listdir=os.listdir, sleep=time.sleep, retry_s: float = 2.0) -> set[Path]:
    """Return the files already in watch_dir, retrying until it can be listed.

    At boot the /mnt/c DrvFs mount can briefly fail to list (EINVAL/ENOENT).
    watchdog silently treats that as an empty directory, so its first snapshot
    is empty and the next poll reports every pre-existing file as created. This
    listing is what lets the handler ignore them, so it must not give up early.
    """
    while True:
        try:
            return {watch_dir / name for name in listdir(watch_dir)}
        except OSError as exc:
            log.warning("cannot list %s yet (%s); retrying", watch_dir, exc)
            sleep(retry_s)


class ImageHandler(FileSystemEventHandler):
    def __init__(self, config, on_ready: Callable[[Path], None], existing: set[Path] | None = None):
        self.config = config
        self.on_ready = on_ready
        # Paths already present (seeded at startup) or already dispatched. A
        # path is dispatched at most once until it is deleted or moved away.
        self._seen: set[Path] = set(existing or ())
        self._lock = threading.Lock()

    def _handle(self, raw_path: str) -> None:
        path = Path(raw_path)
        if not self.config.matches(path):
            return
        with self._lock:
            if path in self._seen:
                log.debug("already seen, skipped: %s", path)
                return
            self._seen.add(path)
        if wait_for_stable(path, self.config.stability_ms):
            log.info("image ready: %s", path)
            self.on_ready(path)
        else:
            log.debug("file not stable, skipped: %s", path)
            with self._lock:
                self._seen.discard(path)

    def _forget(self, raw_path: str) -> None:
        with self._lock:
            self._seen.discard(Path(raw_path))

    def on_created(self, event) -> None:
        if not event.is_directory:
            self._handle(event.src_path)

    def on_moved(self, event) -> None:
        if not event.is_directory:
            self._forget(event.src_path)
            self._handle(event.dest_path)

    def on_deleted(self, event) -> None:
        if not event.is_directory:
            self._forget(event.src_path)


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
