"""User state kept outside packs: settings, profile values and saved presets.

Packs are shareable; everything personal lives here so exporting a pack never leaks it.
"""
import copy
from pathlib import Path

import yaml

from . import FillmateError
from .paths import config_dir, data_dir, default_output_dir, default_packs_dir, write_private

DEFAULT_SETTINGS = {
    "packs_dir": "",
    "output_dir": "",
    "theme": "auto",
    "last_pack": "",
    "test_mode": False,
    "test_recipients": [],
    "sender": "gmail",  # gmail | smtp | mail_app
    "smtp": {"host": "", "port": 587, "security": "starttls", "username": "", "from": ""},
    "attach_docx_as_pdf": False,
}


def _read_yaml(path: Path):
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise FillmateError(f"{path} is not valid YAML: {e}") from e
    return data if isinstance(data, dict) else {}


def _write_yaml(path: Path, data):
    write_private(path, yaml.safe_dump(data, allow_unicode=True, sort_keys=False))


def settings_path():
    return config_dir() / "settings.yaml"


def load_settings():
    s = copy.deepcopy(DEFAULT_SETTINGS)
    stored = _read_yaml(settings_path())
    smtp = stored.pop("smtp", None)
    s.update({k: v for k, v in stored.items() if k in DEFAULT_SETTINGS})
    if isinstance(smtp, dict):
        s["smtp"].update(smtp)
    return s


def save_settings(s):
    _write_yaml(settings_path(), s)


def packs_dir(s=None) -> Path:
    s = s or load_settings()
    return Path(s["packs_dir"]).expanduser() if s.get("packs_dir") else default_packs_dir()


def output_dir(s=None) -> Path:
    s = s or load_settings()
    return Path(s["output_dir"]).expanduser() if s.get("output_dir") else default_output_dir()


# --- profile ------------------------------------------------------------

def profile_path():
    return config_dir() / "profile.yaml"


def load_profile():
    return _read_yaml(profile_path())


def save_profile(values):
    clean = {k: v for k, v in values.items() if v not in (None, "")}
    _write_yaml(profile_path(), clean)


# --- presets saved by the user -----------------------------------------

def _presets_path(pack_id):
    return data_dir() / "presets" / f"{pack_id}.yaml"


def load_user_presets(pack_id):
    return {str(k): v for k, v in _read_yaml(_presets_path(pack_id)).items() if isinstance(v, dict)}


def save_user_presets(pack_id, presets):
    _write_yaml(_presets_path(pack_id), presets)
