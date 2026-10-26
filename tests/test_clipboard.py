from pathlib import Path

import pytest

import cb_photo_loader.clipboard as clip
from cb_photo_loader.clipboard import (
    Clipboard,
    LinuxBackend,
    Notifier,
    WindowsBackend,
)

_real_interop_session = clip._interop_session


@pytest.fixture(autouse=True)
def _desktop_session(monkeypatch):
    # Never launch a real session probe: treat every interop socket as being on
    # the desktop unless a test says otherwise.
    monkeypatch.setattr(clip, "_interop_session", lambda sock: 1)


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


def test_windows_backend_also_places_text_path_and_file_drop(monkeypatch):
    # Native-Windows terminals (VS Code, mintty, conhost) consume Ctrl+V as a
    # bracketed *text* paste and never deliver the keystroke to Claude Code's
    # image handler. A bitmap-only clipboard therefore pastes as nothing. We
    # additionally place the file path as text (CF_UNICODETEXT) and a file-drop
    # list (CF_HDROP) so the text-paste carries the path and Claude loads it.
    calls = []

    def fake_run(cmd, **kw):
        calls.append((cmd, kw))

        class R:
            stdout = "C:\\imgs\\a.png\n"

        return R()

    monkeypatch.setattr(clip.subprocess, "run", fake_run)
    WindowsBackend().copy(Path("/mnt/c/imgs/a.png"))

    ps = calls[1][0]
    joined = " ".join(ps)
    # All three formats must be written through a single DataObject so they
    # coexist (separate Set* calls would replace one another).
    assert "DataObject" in joined
    assert "SetImage" in joined
    assert "SetText" in joined
    assert "SetFileDropList" in joined
    # Still a persisted clipboard write so it survives the powershell exit.
    assert "SetDataObject" in joined and "$true" in joined
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


def test_notifier_fires_windows_toast(monkeypatch):
    calls = []

    def fake_run(cmd, **kw):
        calls.append((cmd, kw))

        class R:
            stdout = "C:\\imgs\\photo.png\n"

        return R()

    monkeypatch.setattr(clip.shutil, "which", lambda t: "/c/powershell.exe" if t == "powershell.exe" else None)
    monkeypatch.setattr(clip.subprocess, "run", fake_run)
    Notifier().notify(Path("/mnt/c/imgs/photo.png"))

    # First call translates the path; second fires the toast.
    assert calls[0][0] == ["wslpath", "-w", "/mnt/c/imgs/photo.png"]
    toast_cmd, toast_kw = calls[1]
    assert toast_cmd[0] == "powershell.exe"
    assert "ToastNotification" in " ".join(toast_cmd)
    # Dynamic values are handed off via env, never interpolated into the script.
    env = toast_kw["env"]
    assert env["CBPL_IMG"] == "C:\\imgs\\photo.png"
    assert env["CBPL_BODY"] == "photo.png"
    assert env["CBPL_TITLE"] == "Image copied"
    # WSLENV must name the vars or WSL won't forward them to the Windows
    # process, and the toast shows a generic "New Notification".
    forwarded = env["WSLENV"].split(":")
    assert {"CBPL_IMG", "CBPL_TITLE", "CBPL_BODY"} <= set(forwarded)


def test_notifier_skips_when_powershell_absent(monkeypatch):
    monkeypatch.setattr(clip.shutil, "which", lambda t: None)
    ran = []
    monkeypatch.setattr(clip.subprocess, "run", lambda *a, **k: ran.append(a))
    Notifier().notify(Path("/mnt/c/imgs/photo.png"))
    assert ran == []  # no subprocess invoked when powershell.exe is unavailable


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


def test_windows_backend_escapes_single_quotes(monkeypatch):
    calls = []

    def fake_run(cmd, **kw):
        calls.append((cmd, kw))

        class R:
            stdout = "C:\\Users\\Nick\\Hannah's photo.png\n"

        return R()

    monkeypatch.setattr(clip.subprocess, "run", fake_run)
    WindowsBackend().copy(Path("/mnt/c/Users/Nick/Hannah's photo.png"))

    ps = calls[1][0]
    joined = " ".join(ps)
    assert "Hannah''s photo.png" in joined
    assert "Hannah's photo.png" not in joined.replace("Hannah''s photo.png", "")


def test_powershell_env_keeps_valid_current_interop(monkeypatch):
    monkeypatch.setattr(clip.os, "environ", {"WSL_INTEROP": "/run/WSL/123_interop"})
    monkeypatch.setattr(clip.os.path, "exists", lambda p: True)
    monkeypatch.setattr(clip, "_interop_session", lambda sock: 1)
    # _scan must not be consulted when the inherited socket is live and interactive.
    monkeypatch.setattr(clip, "_scan_proc_for_interop", lambda: "/run/WSL/should_not_use")
    env = clip._powershell_env()
    assert env["WSL_INTEROP"] == "/run/WSL/123_interop"


def test_powershell_env_resolves_live_interop_when_stale(monkeypatch):
    monkeypatch.setattr(clip.os, "environ", {"WSL_INTEROP": "/run/WSL/dead_interop"})
    monkeypatch.setattr(clip.os.path, "exists", lambda p: False)  # inherited socket gone
    monkeypatch.setattr(clip, "_scan_proc_for_interop", lambda: "/run/WSL/live_interop")
    env = clip._powershell_env()
    assert env["WSL_INTEROP"] == "/run/WSL/live_interop"


def test_powershell_env_merges_extra(monkeypatch):
    monkeypatch.setattr(clip.os, "environ", {"WSL_INTEROP": "/run/WSL/123_interop"})
    monkeypatch.setattr(clip.os.path, "exists", lambda p: True)
    monkeypatch.setattr(clip, "_interop_session", lambda sock: 1)
    env = clip._powershell_env({"CBPL_BODY": "x.png"})
    assert env["CBPL_BODY"] == "x.png"
    assert env["WSL_INTEROP"] == "/run/WSL/123_interop"


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


def test_powershell_env_replaces_session0_interop(monkeypatch):
    # WSL started by the boot-time S4U task: its interop socket launches
    # Windows processes in session 0, which has no desktop, so a clipboard
    # write there is invisible. An inherited session-0 socket must be replaced.
    monkeypatch.setattr(clip.os, "environ", {"WSL_INTEROP": "/run/WSL/boot_interop"})
    monkeypatch.setattr(clip.os.path, "exists", lambda p: True)
    monkeypatch.setattr(clip, "_interop_session", lambda sock: 0)
    monkeypatch.setattr(clip, "_scan_proc_for_interop", lambda: "/run/WSL/desktop_interop")
    env = clip._powershell_env()
    assert env["WSL_INTEROP"] == "/run/WSL/desktop_interop"


def test_scan_proc_skips_session0_sockets(monkeypatch):
    monkeypatch.setattr(
        clip, "_live_interop_sockets", lambda: ["/run/WSL/1682_interop", "/run/WSL/1754_interop"]
    )
    sessions = {"/run/WSL/1682_interop": 0, "/run/WSL/1754_interop": 1}
    monkeypatch.setattr(clip, "_interop_session", sessions.get)
    assert clip._scan_proc_for_interop() == "/run/WSL/1754_interop"


def test_scan_proc_returns_none_when_only_session0(monkeypatch):
    # Nobody signed in yet: better to fail loudly than to "copy" into session 0.
    monkeypatch.setattr(clip, "_live_interop_sockets", lambda: ["/run/WSL/1682_interop"])
    monkeypatch.setattr(clip, "_interop_session", lambda sock: 0)
    assert clip._scan_proc_for_interop() is None


def test_interop_session_probes_once_per_socket(monkeypatch):
    calls = []

    def fake_run(cmd, **kw):
        calls.append(kw["env"]["WSL_INTEROP"])

        class R:
            returncode = 0
            stdout = "1\r\n"

        return R()

    monkeypatch.setattr(clip.subprocess, "run", fake_run)
    monkeypatch.setattr(clip, "_socket_key", lambda sock: (sock, 7))
    clip._SESSION_CACHE.clear()
    assert _real_interop_session("/run/WSL/9_interop") == 1
    assert _real_interop_session("/run/WSL/9_interop") == 1
    assert calls == ["/run/WSL/9_interop"]


def test_powershell_env_raises_when_no_desktop_session(monkeypatch):
    monkeypatch.setattr(clip.os, "environ", {})
    monkeypatch.setattr(clip, "_scan_proc_for_interop", lambda: None)
    with pytest.raises(RuntimeError, match="desktop session"):
        clip._powershell_env()


def test_interop_session_probe_asks_for_own_session_id(monkeypatch):
    # Regression: the probe once shipped as "(Get-Process -Id ).SessionId"
    # after $PID was shell-expanded away, so every probe failed.
    cmds = []

    def fake_run(cmd, **kw):
        cmds.append(cmd)

        class R:
            returncode = 0
            stdout = "1\r\n"

        return R()

    monkeypatch.setattr(clip.subprocess, "run", fake_run)
    monkeypatch.setattr(clip, "_socket_key", lambda sock: (sock, 8))
    clip._SESSION_CACHE.clear()
    _real_interop_session("/run/WSL/10_interop")
    assert "(Get-Process -Id $PID).SessionId" in cmds[0]
