"""Configuration loading: TOML file + defaults + env override."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "cb-photo-loader" / "config.toml"

_DEFAULTS = {
    "watch_dir": "/mnt/temp/phone-downloads",
    "extensions": ["png", "jpg", "jpeg", "gif", "bmp", "webp"],
    "stability_ms": 750,
    "notifications": True,
}

_ENV_WATCH_DIR = "CB_PHOTO_LOADER_WATCH_DIR"


@dataclass(frozen=True)
class Config:
    watch_dir: Path
    extensions: frozenset[str]
    stability_ms: int
    notifications: bool

    def matches(self, path: Path) -> bool:
        ext = path.suffix.lower().lstrip(".")
        return bool(ext) and ext in self.extensions


def load_config(path: Path | None = None) -> Config:
    path = path or DEFAULT_CONFIG_PATH
    data = dict(_DEFAULTS)
    if path.exists():
        with path.open("rb") as fh:
            data.update(tomllib.load(fh))

    env_dir = os.environ.get(_ENV_WATCH_DIR)
    if env_dir:
        data["watch_dir"] = env_dir

    return Config(
        watch_dir=Path(data["watch_dir"]),
        extensions=frozenset(e.lower().lstrip(".") for e in data["extensions"]),
        stability_ms=int(data["stability_ms"]),
        notifications=bool(data["notifications"]),
    )
