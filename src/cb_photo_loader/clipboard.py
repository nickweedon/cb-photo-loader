"""Clipboard backends (Windows + Linux), desktop notifier, and dispatcher."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)


def _scan_proc_for_interop() -> str | None:
    """Find a WSL_INTEROP socket belonging to a live process.

    A process that has ``WSL_INTEROP`` in its environment is, by definition,
    alive (it has a ``/proc`` entry) and its value points at its own session's
    interop socket. Returns the first such value whose socket file still
    exists, or ``None``.
    """
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            data = (Path("/proc") / pid / "environ").read_bytes()
        except OSError:
            continue  # process gone or not readable by us
        for chunk in data.split(b"\x00"):
            if chunk.startswith(b"WSL_INTEROP="):
                val = chunk[len(b"WSL_INTEROP="):].decode("utf-8", "replace")
                if val and os.path.exists(val):
                    return val
    return None


def _powershell_env(extra: dict | None = None) -> dict:
    """Build the environment for a ``powershell.exe`` subprocess.

    WSL interop sockets are session-scoped, so the value inherited by a
    long-lived systemd ``--user`` service goes stale after a WSL restart and
    ``powershell.exe`` then fails to launch. Re-resolve a live ``WSL_INTEROP``
    whenever the inherited one is missing or its socket no longer exists.
    """
    env = dict(os.environ)
    current = env.get("WSL_INTEROP")
    if not current or not os.path.exists(current):
        live = _scan_proc_for_interop()
        if live:
            env["WSL_INTEROP"] = live
    if extra:
        env.update(extra)
    return env


# PowerShell that raises a Windows toast via the WinRT notification API. The
# image path, title, and body are passed in through environment variables
# (never interpolated into the script text) and XML-escaped inside PowerShell,
# so filenames containing ', &, <, >, or $ cannot break or inject into it.
_TOAST_SCRIPT = (
    "$null = [Windows.UI.Notifications.ToastNotificationManager,"
    "Windows.UI.Notifications,ContentType=WindowsRuntime];"
    "$null = [Windows.Data.Xml.Dom.XmlDocument,"
    "Windows.Data.Xml.Dom.XmlDocument,ContentType=WindowsRuntime];"
    "$img = [System.Security.SecurityElement]::Escape($env:CBPL_IMG);"
    "$title = [System.Security.SecurityElement]::Escape($env:CBPL_TITLE);"
    "$body = [System.Security.SecurityElement]::Escape($env:CBPL_BODY);"
    "$xml = \"<toast><visual><binding template='ToastImageAndText02'>"
    "<image id='1' src='$img'/><text id='1'>$title</text>"
    "<text id='2'>$body</text></binding></visual></toast>\";"
    "$doc = New-Object Windows.Data.Xml.Dom.XmlDocument;"
    "$doc.LoadXml($xml);"
    "$toast = [Windows.UI.Notifications.ToastNotification]::new($doc);"
    "$appId = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}"
    "\\WindowsPowerShell\\v1.0\\powershell.exe';"
    "[Windows.UI.Notifications.ToastNotificationManager]"
    "::CreateToastNotifier($appId).Show($toast)"
)

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
        win_path = win_path.replace("'", "''")
        script = (
            "Add-Type -AssemblyName System.Windows.Forms,System.Drawing; "
            f"$img = [System.Drawing.Image]::FromFile('{win_path}'); "
            "[System.Windows.Forms.Clipboard]::SetDataObject($img, $true)"
        )
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", script],
            check=True,
            capture_output=True,
            env=_powershell_env(),
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
    """Raises a Windows toast (with a thumbnail) via PowerShell/WinRT.

    WSLg provides no notification daemon, so notifications go to the native
    Windows notification centre instead of a Linux ``notify-send`` server.
    """

    def notify(self, image_path: Path) -> None:
        if not shutil.which("powershell.exe"):
            log.warning("powershell.exe not found; skipping notification")
            return
        win_path = subprocess.run(
            ["wslpath", "-w", str(image_path)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        # WSL only forwards Linux env vars to a Windows process if they are
        # named in WSLENV; without this the toast text/image are empty and
        # Windows shows a generic "New Notification". Append our vars to any
        # existing WSLENV so we don't clobber the user's forwarding.
        forwarded = ("CBPL_IMG", "CBPL_TITLE", "CBPL_BODY")
        wslenv = ":".join(filter(None, (os.environ.get("WSLENV", ""), *forwarded)))
        env = _powershell_env(
            {
                "CBPL_IMG": win_path,
                "CBPL_TITLE": "Image copied",
                "CBPL_BODY": image_path.name,
                "WSLENV": wslenv,
            }
        )
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", _TOAST_SCRIPT],
            check=True,
            capture_output=True,
            env=env,
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
