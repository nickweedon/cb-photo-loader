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
