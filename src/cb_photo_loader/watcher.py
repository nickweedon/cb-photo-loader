"""Filesystem watcher with a file-completion (size-stability) gate."""

from __future__ import annotations

import logging
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


def run_observer(config, on_ready: Callable[[Path], None]) -> PollingObserver:
    # A PollingObserver (stat-based scanning) is required, not the default
    # inotify observer: inotify does not deliver events for files on Windows
    # drives (/mnt/c, DrvFs/9p), which is exactly where this tool watches.
    observer = PollingObserver(timeout=1.0)
    observer.schedule(ImageHandler(config, on_ready), str(config.watch_dir), recursive=False)
    observer.start()
    log.info("watching %s", config.watch_dir)
    return observer
