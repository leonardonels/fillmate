"""Your signatures: images kept with the profile, drawn on documents where a pack asks for one.

They are personal like the profile, so they live in the config folder (readable only by you)
and are never part of a pack or a pack export. A signature has one or more variants (the
same signature written a few times); table rows go through them in turn.

Every variant is a PNG with a transparent background and no margin, made from a photo or
scan (white becomes transparent) or cut out of a PDF page at 600 dpi. A PDF cut-out is
rasterised on purpose: a vector crop would carry the rest of the page along, hidden.
"""
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pymupdf
import yaml

from . import FillmateError, InputError
from .paths import config_dir, write_private

INDEX = "signatures.yaml"
MAX_WIDTH = 2000   # px: larger photos are scaled down
PDF_DPI = 600
WHITE, INK = 225, 150  # luminance: lighter is background, darker is fully ink


@dataclass
class Signature:
    id: str
    name: str
    files: list = field(default_factory=list)  # Paths of the variants

    def __str__(self):
        return ""  # in text templates a signature writes nothing (it is drawn, not typed)

    def __bool__(self):
        return True

    def variant(self, n):
        return self.files[n % len(self.files)]


def folder() -> Path:
    return config_dir() / "signatures"


_cache = {"key": None, "data": {}}


def _index():
    path = folder() / INDEX
    try:
        key = (str(path), path.stat().st_mtime_ns)
    except OSError:
        return {}
    if _cache["key"] != key:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as e:
            raise FillmateError(f"{path} is not valid YAML: {e}") from e
        _cache.update(key=key, data=data if isinstance(data, dict) else {})
    return _cache["data"]


def _write_index(data):
    write_private(folder() / INDEX, yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
    _cache["key"] = None


def list_signatures():
    out = []
    for sid, entry in _index().items():
        files = [folder() / f for f in (entry or {}).get("files", []) if (folder() / f).is_file()]
        if files:
            out.append(Signature(str(sid), str(entry.get("name") or sid), files))
    return out


def get(sid):
    return next((s for s in list_signatures() if s.id == str(sid)), None)


def first():
    sigs = list_signatures()
    return sigs[0] if sigs else None


def _new_id(name, used):
    base = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "signature"
    sid, n = base, 2
    while sid in used:
        sid, n = f"{base}_{n}", n + 1
    return sid


# --- cleaning images ---------------------------------------------------------

def clean(pix):
    """PNG bytes: white made transparent, the empty margin trimmed. None if nothing is drawn."""
    if pix.colorspace is None or pix.colorspace.n != 3:
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
    while pix.width > MAX_WIDTH:
        pix.shrink(1)
    a = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n).astype(np.float32)
    rgb = a[:, :, :3]
    alpha = a[:, :, 3] if pix.n == 4 else np.full(a.shape[:2], 255, np.float32)
    lum = rgb @ np.array([0.299, 0.587, 0.114], np.float32)
    ink = np.clip((WHITE - lum) / (WHITE - INK), 0, 1)
    alpha = alpha * ink
    rows, cols = np.where(alpha.max(axis=1) > 24)[0], np.where(alpha.max(axis=0) > 24)[0]
    if not len(rows) or not len(cols):
        return None
    pad = 4
    y0, y1 = max(rows[0] - pad, 0), min(rows[-1] + pad + 1, pix.height)
    x0, x1 = max(cols[0] - pad, 0), min(cols[-1] + pad + 1, pix.width)
    out = np.dstack([rgb, alpha])[y0:y1, x0:x1].round().astype(np.uint8)
    h, w = out.shape[:2]
    return pymupdf.Pixmap(pymupdf.csRGB, w, h, np.ascontiguousarray(out).tobytes(), 1).tobytes("png")


def _from_image(path):
    try:
        pix = pymupdf.Pixmap(str(path))
    except Exception as e:
        raise InputError(f"{Path(path).name} is not an image fillmate can read ({e}).") from e
    png = clean(pix)
    if png is None:
        raise InputError(f"{Path(path).name}: no signature found (the image is empty or all white).")
    return png


def _from_pdf(path, page_no, rect):
    try:
        with pymupdf.open(str(path)) as pdf:
            pix = pdf[page_no].get_pixmap(dpi=PDF_DPI, clip=pymupdf.Rect(*rect), alpha=False)
    except Exception as e:
        raise InputError(f"{Path(path).name}: cannot read that part of the PDF ({e}).") from e
    png = clean(pix)
    if png is None:
        raise InputError("Nothing is drawn in that box: draw it around the signature.")
    return png


def _store(pngs, name, sid=None):
    data = dict(_index())
    if sid is None:
        sid = _new_id(name, set(data))
        data[sid] = {"name": name.strip() or sid, "files": []}
    files = list(data[sid].get("files", []))
    n = len(files)
    for png in pngs:
        n += 1
        fname = f"{sid}_{n}.png"
        while (folder() / fname).exists():
            n += 1
            fname = f"{sid}_{n}.png"
        write_private(folder() / fname, png)
        files.append(fname)
    data[sid] = {**data[sid], "files": files}
    _write_index(data)
    return get(sid)


def add_images(paths, name, to=None):
    """A new signature (or more variants of signature `to`) from one or more photos or scans."""
    pngs = [_from_image(p) for p in paths]
    if not pngs:
        raise InputError("Choose at least one image.")
    return _store(pngs, name, to)


def add_pdf(path, page_no, rect, name, to=None):
    """A signature cut out of a PDF page: rect is (x0, y0, x1, y1) in points."""
    return _store([_from_pdf(path, page_no, rect)], name, to)


def rename(sid, name):
    data = dict(_index())
    if sid in data and name.strip():
        data[sid] = {**data[sid], "name": name.strip()}
        _write_index(data)


def remove(sid, variant=None):
    """Delete a signature, or only one of its variants (by index)."""
    data = dict(_index())
    entry = data.get(sid)
    if not entry:
        return
    files = list(entry.get("files", []))
    gone = files if variant is None else [files[variant]] if 0 <= variant < len(files) else []
    for f in gone:
        (folder() / f).unlink(missing_ok=True)
    rest = [f for f in files if f not in gone]
    if rest:
        data[sid] = {**entry, "files": rest}
    else:
        del data[sid]
    _write_index(data)


# --- drawing -----------------------------------------------------------------

def draw(page, rect, signature, variant=0, pad=1.0):
    """Draw a signature inside rect (x0, y0, x1, y1), as large as fits, centred."""
    x0, y0, x1, y1 = rect
    box = pymupdf.Rect(x0 + pad, y0 + pad, x1 - pad, y1 - pad)
    if box.is_empty:
        return
    path = signature.variant(variant)
    pix = pymupdf.Pixmap(str(path))
    scale = min(box.width / pix.width, box.height / pix.height)
    w, h = pix.width * scale, pix.height * scale
    target = pymupdf.Rect(box.x0 + (box.width - w) / 2, box.y0 + (box.height - h) / 2,
                          box.x0 + (box.width + w) / 2, box.y0 + (box.height + h) / 2)
    page.insert_image(target, filename=str(path))
