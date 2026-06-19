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

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        captured["input"] = kw.get("input")

    monkeypatch.setattr(clip.subprocess, "run", fake_run)
    LinuxBackend().copy(img)

    assert captured["cmd"] == ["xclip", "-selection", "clipboard", "-t", "image/jpeg"]
    assert captured["input"] == b"JPG"


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
