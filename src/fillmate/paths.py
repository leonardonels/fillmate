"""Per-user folders. Set FILLMATE_HOME to keep everything under one folder (tests, portable use)."""
import os
from pathlib import Path

import platformdirs

APP = "fillmate"


def _home():
    value = os.environ.get("FILLMATE_HOME")
    return Path(value).expanduser() if value else None


def config_dir() -> Path:
    """Settings, profile and credentials."""
    home = _home()
    return home / "config" if home else Path(platformdirs.user_config_dir(APP, appauthor=False))


def data_dir() -> Path:
    """Installed packs and saved presets."""
    home = _home()
    return home / "data" if home else Path(platformdirs.user_data_dir(APP, appauthor=False))


def cache_dir() -> Path:
    home = _home()
    return home / "cache" if home else Path(platformdirs.user_cache_dir(APP, appauthor=False))


def default_packs_dir() -> Path:
    return data_dir() / "packs"


def default_output_dir() -> Path:
    home = _home()
    return home / "output" if home else Path(platformdirs.user_documents_dir()) / "Fillmate"


def examples_dir() -> Path:
    """Example packs shipped with the app."""
    return Path(__file__).resolve().parent / "examples"


def write_private(path: Path, text: str):
    """Write a file readable only by the current user (profile data, credentials)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
