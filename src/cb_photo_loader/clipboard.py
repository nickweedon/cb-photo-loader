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
