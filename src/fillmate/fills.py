"""Saved forms: what was typed into a pack's form, kept in a .fillmate file (YAML).

Creating documents writes one next to them, so each output folder (each month, each trip)
remembers its values and can be opened again to change something or start the next one
from it. "Save as…" writes one anywhere, to hand over or keep. Only the form's fields are
saved: profile details and signature images stay on your computer (a signature is saved by
its name, so the same person can open the file again).
"""
import datetime as dt
from pathlib import Path

import yaml

from . import FillmateError

FORMAT = 1
SUFFIX = ".fillmate"


def plain(value):
    """A YAML-safe copy: dates as ISO text, signatures by id, lists and dicts all the way down."""
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (dt.date, dt.datetime, dt.time)):
        return value.isoformat()
    sid = getattr(value, "id", None)  # a Signature
    return sid if isinstance(sid, str) else str(value)


def save_fill(path, pack, values, preset=None):
    """Write the form's values (fields only) for `pack` to path (.fillmate added if missing)."""
    path = Path(path)
    if path.suffix != SUFFIX:
        path = path.with_name(path.name + SUFFIX)
    fields = {f.id for f in pack.fields}
    data = {
        "fillmate_fill": FORMAT,
        "pack": pack.id,
        "pack_version": pack.version,
        "saved": dt.datetime.now().replace(microsecond=0).isoformat(),
        "preset": preset or None,
        "values": plain({k: v for k, v in values.items() if k in fields}),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def load_fill(path):
    """{"pack", "values", "preset", "saved"} from a .fillmate file."""
    path = Path(path)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as e:
        raise FillmateError(f"Cannot open {path.name}: {e.strerror}.") from e
    except yaml.YAMLError as e:
        raise FillmateError(f"{path.name} is not a saved fillmate form.") from e
    if not isinstance(data, dict) or "fillmate_fill" not in data or not isinstance(data.get("values"), dict):
        raise FillmateError(f"{path.name} is not a saved fillmate form.")
    if data["fillmate_fill"] != FORMAT:
        raise FillmateError(f"{path.name} was saved by a newer fillmate: update fillmate to open it.")
    return {"pack": str(data.get("pack", "")), "values": data["values"], "preset": data.get("preset"),
            "saved": str(data.get("saved", ""))}


def for_pack(fill, pack):
    """The values of a loaded fill, checked against the pack it is opened in."""
    if fill["pack"] != pack.id:
        raise FillmateError(f"This form was filled with the pack '{fill['pack']}', not '{pack.id}'.")
    known = {f.id for f in pack.fields}
    return {k: v for k, v in fill["values"].items() if k in known}


def recent_fills(base, pack_id=None, n=8):
    """The latest saved forms in the output folders under base: [(path, folder name, saved)], newest first."""
    base = Path(base)
    if not base.is_dir():
        return []
    found = sorted(base.glob(f"*/*{SUFFIX}"), key=lambda p: p.stat().st_mtime, reverse=True)
    out = []
    for path in found:
        try:
            fill = load_fill(path)
        except FillmateError:
            continue
        if pack_id is None or fill["pack"] == pack_id:
            out.append((path, path.parent.name, fill["saved"]))
        if len(out) >= n:
            break
    return out
