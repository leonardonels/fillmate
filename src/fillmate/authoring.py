"""Creating and editing packs from the app (calibration tool).

pack.yaml is edited with ruamel.yaml in round-trip mode so the author's comments and
layout survive. Every save is validated with load_pack and rolled back if it fails.
"""
import io
import re
import shutil
from pathlib import Path

import pymupdf
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from . import PackError
from .pack import ID_RE, NAME_RE, SPEC_FILE, load_pack
from .templating import slug


def _yaml():
    y = YAML()
    y.preserve_quotes = True
    y.width = 4096  # keep each fill entry on one line
    y.indent(mapping=2, sequence=4, offset=2)
    return y


def read_spec(root):
    return _yaml().load((Path(root) / SPEC_FILE).read_text(encoding="utf-8"))


def write_spec(root, spec):
    """Write pack.yaml, validate it, and restore the previous version if it is invalid."""
    path = Path(root) / SPEC_FILE
    old = path.read_text(encoding="utf-8") if path.exists() else None
    buf = io.StringIO()
    _yaml().dump(spec, buf)
    path.write_text(buf.getvalue(), encoding="utf-8")
    try:
        return load_pack(root)
    except PackError:
        if old is None:
            path.unlink()
        else:
            path.write_text(old, encoding="utf-8")
        raise


def _flow(items):
    seq = CommentedSeq(items)
    seq.fa.set_flow_style()
    return seq


def _round(v):
    v = round(float(v) * 2) / 2  # half-point steps are plenty precise
    return int(v) if v == int(v) else v


def entry_to_yaml(entry):
    """A fill entry as compact YAML: one line per entry, like a hand-written pack."""
    m = CommentedMap()
    if "field" in entry:
        m["field"] = entry["field"]
        m["value"] = entry["value"]
    elif "mark" in entry:
        m["mark"] = entry["mark"]
        if entry.get("page", 0):
            m["page"] = entry["page"]
        m["at"] = _flow([_round(v) for v in entry["at"]])
        if entry.get("symbol", "X") != "X":
            m["symbol"] = entry["symbol"]
        if float(entry.get("size", 11)) != 11:
            m["size"] = _round(entry["size"])
    else:
        m["value"] = entry["value"]
        if entry.get("page", 0):
            m["page"] = entry["page"]
        m["lines"] = _flow([_flow([_round(v) for v in line]) for line in entry["lines"]])
        if float(entry.get("size", 10)) != 10:
            m["size"] = _round(entry["size"])
        if float(entry.get("min_size", 7)) != 7:
            m["min_size"] = _round(entry["min_size"])
        if entry.get("font", "helv") != "helv":
            m["font"] = entry["font"]
    m.fa.set_flow_style()
    return m


def set_document_fill(root, doc_id, entries):
    spec = read_spec(root)
    for doc in spec.get("documents") or []:
        if doc.get("id") == doc_id:
            doc["fill"] = CommentedSeq(entry_to_yaml(e) for e in entries)
            return write_spec(root, spec)
    raise PackError(f"No document '{doc_id}' in the pack.")


def add_field(root, field_id, label, field_type="text", section="fields"):
    if not NAME_RE.match(field_id):
        raise PackError("A field id uses letters, digits and _, and does not start with a digit.")
    spec = read_spec(root)
    for sec in ("profile", "fields"):
        if any(f.get("id") == field_id for f in spec.get(sec) or []):
            raise PackError(f"A field '{field_id}' already exists.")
    if spec.get(section) is None:
        spec[section] = CommentedSeq()
    m = CommentedMap(id=field_id, label=label)
    if field_type != "text":
        m["type"] = field_type
    m.fa.set_flow_style()
    spec[section].append(m)
    return write_spec(root, spec)


# --- new pack from a PDF -------------------------------------------------

def pdf_form_fields(path):
    """(name, type) of the fillable fields of a PDF, in page order, without duplicates."""
    out, seen = [], set()
    with pymupdf.open(path) as pdf:
        for page in pdf:
            for w in page.widgets() or []:
                if w.field_name and w.field_name not in seen:
                    seen.add(w.field_name)
                    kind = "bool" if w.field_type == pymupdf.PDF_WIDGET_TYPE_CHECKBOX else "text"
                    out.append((w.field_name, kind))
    return out


def _field_id(name, used):
    base = re.sub(r"\W+", "_", slug(name, 40)).strip("_").lower() or "field"
    if base[0].isdigit():
        base = "f_" + base
    fid, n = base, 2
    while fid in used:
        fid, n = f"{base}_{n}", n + 1
    used.add(fid)
    return fid


def create_pack_from_pdf(pdf_path, packs_dir, name):
    """Make a new pack folder around a PDF. Fillable fields become pack fields automatically."""
    pdf_path, packs_dir = Path(pdf_path), Path(packs_dir)
    pack_id = slug(name, 40).lower().replace("_", "-").strip("-") or "my-pack"
    if not ID_RE.match(pack_id):
        pack_id = "pack-" + pack_id
    root, n = packs_dir / pack_id, 2
    while root.exists():
        root, n = packs_dir / f"{pack_id}-{n}", n + 1
    root.mkdir(parents=True)
    template = "form.pdf"
    shutil.copy(pdf_path, root / template)

    used, fields, fill = set(), CommentedSeq(), CommentedSeq()
    for wname, kind in pdf_form_fields(root / template):
        fid = _field_id(wname, used)
        f = CommentedMap(id=fid, label=wname)
        if kind == "bool":
            f["type"] = "bool"
        f.fa.set_flow_style()
        fields.append(f)
        fill.append(entry_to_yaml({"field": wname, "value": f"{{{{ {fid} }}}}"}))

    spec = CommentedMap()
    spec["fillmate"] = 1
    spec["id"] = root.name
    spec["name"] = name
    spec["version"] = "1.0"
    spec["description"] = ""
    spec["date_format"] = "%d/%m/%Y"
    spec["profile"] = CommentedSeq()
    spec["fields"] = fields
    doc = CommentedMap(id="form", label=pdf_path.stem, template=template, output=f"{pdf_path.stem}_filled", fill=fill)
    spec["documents"] = CommentedSeq([doc])
    spec["email"] = CommentedMap(to=CommentedSeq(), subject=name, body="Hello,\n\nplease find the document attached.\n")
    try:
        return write_spec(root, spec)
    except PackError:
        shutil.rmtree(root, ignore_errors=True)
        raise


# --- sample rendering ------------------------------------------------------

TAG_RE = re.compile(r"\{\{(.*?)\}\}")
BLOCK_RE = re.compile(r"\{%.*?%\}")


def sample_text(template, labels):
    """Readable stand-in for a value template: {{ full_name }} -> "Full name"."""
    def repl(m):
        ident = re.match(r"\s*([A-Za-z_]\w*)", m.group(1))
        return labels.get(ident.group(1), ident.group(1)) if ident else "…"
    return BLOCK_RE.sub("", TAG_RE.sub(repl, template)).strip() or "Sample"


def render_page(pdf_path, page_no, dpi, entries=(), labels=None):
    """PNG of one page; with entries, sample values are drawn so the layout can be checked."""
    from .fill_pdf import put_text

    with pymupdf.open(pdf_path) as pdf:
        page = pdf[page_no]
        for e in entries:
            if e.get("page", 0) != page_no or "field" in e:
                continue
            if "mark" in e:
                page.insert_text(e["at"], e.get("symbol", "X"), fontname="helv", fontsize=e.get("size", 11),
                                 color=(0.1, 0.35, 0.9))
            else:
                full = {"font": "helv", "size": 10.0, "min_size": 7.0, **e}
                try:
                    put_text(page, full, sample_text(e["value"], labels or {}), "sample")
                except Exception:
                    pass  # too long for the area: the editor flags it separately
        return page.get_pixmap(dpi=dpi).tobytes("png"), len(pdf)
