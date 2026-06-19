from pathlib import Path
from types import SimpleNamespace

from watchdog.observers.polling import PollingObserver

from cb_photo_loader.config import Config
from cb_photo_loader.watcher import ImageHandler, run_observer, wait_for_stable


def _cfg():
    return Config(
        watch_dir=Path("/x"),
        extensions=frozenset({"png", "jpg"}),
        stability_ms=10,
        notifications=False,
    )


def test_run_observer_uses_polling_observer(tmp_path):
    # inotify does not fire on /mnt/c (DrvFs/9p) Windows mounts, which is the
    # whole point of this tool — so run_observer must use a PollingObserver.
    cfg = Config(
        watch_dir=tmp_path,
        extensions=frozenset({"png"}),
        stability_ms=10,
        notifications=False,
    )
    observer = run_observer(cfg, lambda p: None)
    try:
        assert isinstance(observer, PollingObserver)
        assert observer.is_alive()
    finally:
        observer.stop()
        observer.join()


def test_wait_for_stable_settles():
    sizes = iter([100, 200, 200])
    ok = wait_for_stable(Path("x"), 10, sleep=lambda s: None, get_size=lambda: next(sizes))
    assert ok is True


def test_wait_for_stable_returns_false_when_deleted():
    def boom():
        raise FileNotFoundError

    assert wait_for_stable(Path("x"), 10, sleep=lambda s: None, get_size=boom) is False


def test_wait_for_stable_returns_false_when_deleted_midloop():
    calls = iter([100])

    def get_size():
        try:
            return next(calls)
        except StopIteration:
            raise FileNotFoundError

    assert wait_for_stable(Path("x"), 10, sleep=lambda s: None, get_size=get_size) is False


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
