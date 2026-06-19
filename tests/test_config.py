from pathlib import Path

from cb_photo_loader.config import Config, load_config


def test_defaults_when_file_missing(tmp_path):
    cfg = load_config(tmp_path / "nope.toml")
    assert cfg.watch_dir == Path("/mnt/temp/phone-downloads")
    assert cfg.extensions == frozenset({"png", "jpg", "jpeg", "gif", "bmp", "webp"})
    assert cfg.stability_ms == 750
    assert cfg.notifications is True


def test_parses_toml(tmp_path):
    f = tmp_path / "config.toml"
    f.write_text(
        'watch_dir = "/tmp/imgs"\n'
        'extensions = ["PNG", "jpg"]\n'
        "stability_ms = 1200\n"
        "notifications = false\n"
    )
    cfg = load_config(f)
    assert cfg.watch_dir == Path("/tmp/imgs")
    assert cfg.extensions == frozenset({"png", "jpg"})  # normalized lowercase
    assert cfg.stability_ms == 1200
    assert cfg.notifications is False


def test_env_overrides_watch_dir(tmp_path, monkeypatch):
    f = tmp_path / "config.toml"
    f.write_text('watch_dir = "/tmp/imgs"\n')
    monkeypatch.setenv("CB_PHOTO_LOADER_WATCH_DIR", "/env/dir")
    cfg = load_config(f)
    assert cfg.watch_dir == Path("/env/dir")


def test_matches_by_extension():
    cfg = Config(
        watch_dir=Path("/x"),
        extensions=frozenset({"png", "jpg"}),
        stability_ms=750,
        notifications=True,
    )
    assert cfg.matches(Path("/x/a.PNG")) is True
    assert cfg.matches(Path("/x/a.jpg")) is True
    assert cfg.matches(Path("/x/a.txt")) is False
    assert cfg.matches(Path("/x/noext")) is False
