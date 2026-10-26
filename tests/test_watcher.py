import os
import time
from pathlib import Path
from types import SimpleNamespace

from watchdog.observers.polling import PollingEmitter, PollingObserver
from watchdog.utils.dirsnapshot import DirectorySnapshot

from cb_photo_loader.config import Config
from cb_photo_loader.watcher import ImageHandler, list_existing, run_observer, wait_for_stable


def _cfg():
    return Config(
        watch_dir=Path("/x"),
        extensions=frozenset({"png", "jpg"}),
        stability_ms=10,
        notifications=False,
    )


def _fake_stat(path, mtime_ns=1):
    return SimpleNamespace(st_size=10, st_mtime_ns=mtime_ns)


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
    handler = ImageHandler(_cfg(), ready.append, stat=_fake_stat)
    monkeypatch.setattr("cb_photo_loader.watcher.wait_for_stable", lambda *a, **k: True)
    handler.on_created(SimpleNamespace(is_directory=False, src_path="/x/a.png"))
    assert ready == [Path("/x/a.png")]


def test_handler_ignores_non_image(monkeypatch):
    ready = []
    handler = ImageHandler(_cfg(), ready.append, stat=_fake_stat)
    monkeypatch.setattr("cb_photo_loader.watcher.wait_for_stable", lambda *a, **k: True)
    handler.on_created(SimpleNamespace(is_directory=False, src_path="/x/a.txt"))
    assert ready == []


def test_handler_skips_unstable_file(monkeypatch):
    ready = []
    handler = ImageHandler(_cfg(), ready.append, stat=_fake_stat)
    monkeypatch.setattr("cb_photo_loader.watcher.wait_for_stable", lambda *a, **k: False)
    handler.on_created(SimpleNamespace(is_directory=False, src_path="/x/a.png"))
    assert ready == []


def test_handler_handles_moved_in_file(monkeypatch):
    ready = []
    handler = ImageHandler(_cfg(), ready.append, stat=_fake_stat)
    monkeypatch.setattr("cb_photo_loader.watcher.wait_for_stable", lambda *a, **k: True)
    handler.on_moved(SimpleNamespace(is_directory=False, src_path="/x/final.jpg.part", dest_path="/x/final.jpg"))
    assert ready == [Path("/x/final.jpg")]


def test_handler_ignores_files_present_at_startup(monkeypatch):
    # Files already in the directory at startup must never be dispatched, even
    # if watchdog reports them as created (empty first snapshot at boot).
    ready = []
    existing = {Path("/x/old.png"): (10, 1)}
    handler = ImageHandler(_cfg(), ready.append, existing, stat=_fake_stat)
    monkeypatch.setattr("cb_photo_loader.watcher.wait_for_stable", lambda *a, **k: True)
    handler.on_created(SimpleNamespace(is_directory=False, src_path="/x/old.png"))
    handler.on_created(SimpleNamespace(is_directory=False, src_path="/x/new.png"))
    assert ready == [Path("/x/new.png")]


def test_handler_dispatches_each_file_once(monkeypatch):
    ready = []
    handler = ImageHandler(_cfg(), ready.append, stat=_fake_stat)
    monkeypatch.setattr("cb_photo_loader.watcher.wait_for_stable", lambda *a, **k: True)
    ev = SimpleNamespace(is_directory=False, src_path="/x/a.png")
    handler.on_created(ev)
    handler.on_created(ev)
    assert ready == [Path("/x/a.png")]


def test_handler_ignores_spurious_delete_and_recreate(monkeypatch):
    # The observed sign-in glitch: /mnt/c briefly lists empty (or inodes change),
    # so watchdog reports every existing file deleted and then created again.
    # The files are unchanged, so nothing may be dispatched.
    ready = []
    existing = {Path("/x/a.png"): (10, 1)}
    handler = ImageHandler(_cfg(), ready.append, existing, stat=_fake_stat)
    monkeypatch.setattr("cb_photo_loader.watcher.wait_for_stable", lambda *a, **k: True)
    handler.on_deleted(SimpleNamespace(is_directory=False, src_path="/x/a.png"))
    handler.on_created(SimpleNamespace(is_directory=False, src_path="/x/a.png"))
    assert ready == []


def test_handler_dispatches_new_file_reusing_a_name(monkeypatch):
    ready = []
    existing = {Path("/x/a.png"): (10, 1)}
    handler = ImageHandler(
        _cfg(), ready.append, existing, stat=lambda p: _fake_stat(p, mtime_ns=2)
    )
    monkeypatch.setattr("cb_photo_loader.watcher.wait_for_stable", lambda *a, **k: True)
    handler.on_deleted(SimpleNamespace(is_directory=False, src_path="/x/a.png"))
    handler.on_created(SimpleNamespace(is_directory=False, src_path="/x/a.png"))
    assert ready == [Path("/x/a.png")]


def test_list_existing_retries_until_listable(tmp_path):
    (tmp_path / "a.png").write_bytes(b"x")
    attempts = []

    def flaky_listdir(d):
        attempts.append(d)
        if len(attempts) < 3:
            raise OSError(22, "Invalid argument")
        return os.listdir(d)

    got = list_existing(tmp_path, listdir=flaky_listdir, sleep=lambda s: None)
    assert set(got) == {tmp_path / "a.png"}
    assert len(attempts) == 3


def test_run_observer_ignores_files_that_vanish_and_reappear(tmp_path, monkeypatch):
    # End to end: after startup, a poll where the directory lists empty makes
    # watchdog emit deleted then created for every file. None may be dispatched.
    (tmp_path / "old.png").write_bytes(b"x")
    real_listdir = os.scandir
    state = {"blank": False}

    def listdir(path):
        if state["blank"]:
            raise OSError(22, "Invalid argument")
        return real_listdir(path)

    real_init = PollingEmitter.__init__

    def init(self, *a, **k):
        k["listdir"] = listdir
        real_init(self, *a, **k)

    monkeypatch.setattr(PollingEmitter, "__init__", init)
    cfg = Config(
        watch_dir=tmp_path,
        extensions=frozenset({"png"}),
        stability_ms=10,
        notifications=False,
    )
    ready = []
    observer = run_observer(cfg, ready.append, poll_interval=0.1)
    try:
        time.sleep(0.3)
        state["blank"] = True
        time.sleep(0.3)
        state["blank"] = False
        time.sleep(0.3)
        (tmp_path / "new.png").write_bytes(b"y")
        deadline = time.time() + 5
        while not ready and time.time() < deadline:
            time.sleep(0.05)
        time.sleep(0.3)
    finally:
        observer.stop()
        observer.join()
    assert ready == [tmp_path / "new.png"]


def test_run_observer_does_not_fire_for_preexisting_files(tmp_path, monkeypatch):
    # End to end: even if watchdog's own first snapshot comes back empty, a file
    # already in the directory at startup is not dispatched.
    (tmp_path / "old.png").write_bytes(b"x")
    from watchdog.utils.dirsnapshot import EmptyDirectorySnapshot
    # Reproduce the boot failure: the emitter's first snapshot comes back empty
    # (watchdog's walk() swallows an EINVAL/ENOENT listing as "empty dir").
    monkeypatch.setattr(
        "watchdog.observers.polling.PollingEmitter.on_thread_start",
        lambda self: setattr(self, "_snapshot", EmptyDirectorySnapshot()),
    )
    cfg = Config(
        watch_dir=tmp_path,
        extensions=frozenset({"png"}),
        stability_ms=10,
        notifications=False,
    )
    ready = []
    observer = run_observer(cfg, ready.append, poll_interval=0.1)
    try:
        time.sleep(0.5)
        (tmp_path / "new.png").write_bytes(b"y")
        deadline = time.time() + 5
        while not ready and time.time() < deadline:
            time.sleep(0.05)
        time.sleep(0.3)
    finally:
        observer.stop()
        observer.join()
    assert ready == [tmp_path / "new.png"]
