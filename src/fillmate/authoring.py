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
    elif "table" in entry:
        m["table"] = entry["table"]
        if entry.get("page", 0):
            m["page"] = entry["page"]
        m["xs"] = _flow([_round(v) for v in entry["xs"]])
        m["ys"] = _flow([_round(v) for v in entry["ys"]])
        m["cols"] = _flow([c if c else None for c in entry["cols"]])
        if entry.get("align"):
            m["align"] = _flow(list(entry["align"]))
        if entry.get("overflow", "repeat_page") != "repeat_page":
            m["overflow"] = entry["overflow"]
        if float(entry.get("size", 9)) != 9:
            m["size"] = _round(entry["size"])
        if float(entry.get("min_size", 6)) != 6:
            m["min_size"] = _round(entry["min_size"])
        if entry.get("font", "helv") != "helv":
            m["font"] = entry["font"]
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


def column_to_yaml(c):
    """A table column (a dict with id, label, type, formula…) as one flow-style line."""
    m = CommentedMap(id=c["id"], label=c.get("label") or c["id"])
    if c.get("formula"):
        m["formula"] = c["formula"]
    elif c.get("type", "text") != "text":
        m["type"] = c["type"]
    for key in ("format", "default", "placeholder", "help"):
        if c.get(key) not in (None, ""):
            m[key] = c[key]
    if c.get("options"):
        m["options"] = _flow(list(c["options"]))
    if c.get("required"):
        m["required"] = True
    m.fa.set_flow_style()
    return m


def save_positions(root, doc_id, entries, new_fields=(), table_columns=None):
    """Save a document's fill entries together with the fields created while placing them.

    table_columns ({table field id: [column dicts]}) replaces the columns of tables whose
    columns were edited here. One write: nothing reaches pack.yaml until the person saves,
    so leaving the editor without saving never leaves half-made fields behind.
    """
    spec = read_spec(root)
    table_columns = dict(table_columns or {})
    for sec in ("profile", "fields"):
        for f in spec.get(sec) or []:
            if f.get("id") in table_columns:
                f["columns"] = CommentedSeq(column_to_yaml(c) for c in table_columns.pop(f.get("id")))
    existing = {f.get("id") for sec in ("profile", "fields") for f in spec.get(sec) or []}
    for nf in new_fields:
        if nf["id"] in existing:
            continue
        if not NAME_RE.match(nf["id"]):
            raise PackError(f"'{nf['id']}' cannot be used as a field id.")
        section = nf.get("section", "fields")
        if spec.get(section) is None:
            spec[section] = CommentedSeq()
        m = CommentedMap(id=nf["id"], label=nf["label"])
        if nf.get("type", "text") != "text":
            m["type"] = nf["type"]
        if nf.get("options"):
            m["options"] = _flow(list(nf["options"]))
        if nf.get("type") == "table":  # a table: one column per line underneath
            m["columns"] = CommentedSeq(column_to_yaml(c) for c in table_columns.pop(nf["id"], nf.get("columns", [])))
        else:
            m.fa.set_flow_style()
        spec[section].append(m)
        existing.add(nf["id"])
    for doc in spec.get("documents") or []:
        if doc.get("id") == doc_id:
            doc["fill"] = CommentedSeq(entry_to_yaml(e) for e in entries)
            return write_spec(root, spec)
    raise PackError(f"No document '{doc_id}' in the pack.")


def field_uses(root, field_id):
    """Where pack.yaml mentions a field, other than its own definition (empty = unused)."""
    spec = read_spec(root)
    word = re.compile(rf"\b{re.escape(field_id)}\b")
    places = []
    for key, value in spec.items():
        if key in ("profile", "fields"):
            continue
        buf = io.StringIO()
        _yaml().dump({key: value}, buf)
        if word.search(buf.getvalue()):
            places.append(key)
    return places


def remove_field(root, field_id):
    """Delete a field nothing uses (made by mistake, or no longer needed)."""
    places = field_uses(root, field_id)
    if places:
        raise PackError(f"'{field_id}' is still used in: {', '.join(places)}.")
    spec = read_spec(root)
    for sec in ("profile", "fields"):
        items = spec.get(sec) or []
        for i, f in enumerate(items):
            if f.get("id") == field_id:
                del items[i]
                return write_spec(root, spec)
    raise PackError(f"No field '{field_id}' in the pack.")


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


def _new_pack_root(packs_dir, name):
    packs_dir = Path(packs_dir)
    pack_id = slug(name, 40).lower().replace("_", "-").strip("-") or "my-pack"
    if not ID_RE.match(pack_id):
        pack_id = "pack-" + pack_id
    root, n = packs_dir / pack_id, 2
    while root.exists():
        root, n = packs_dir / f"{pack_id}-{n}", n + 1
    root.mkdir(parents=True)
    return root


def _skeleton(root, name, fields, doc):
    spec = CommentedMap()
    spec["fillmate"] = 1
    spec["id"] = root.name
    spec["name"] = name
    spec["version"] = "1.0"
    spec["description"] = ""
    spec["date_format"] = "%d/%m/%Y"
    spec["profile"] = CommentedSeq()
    spec["fields"] = fields
    spec["documents"] = CommentedSeq([doc])
    spec["email"] = CommentedMap(to=CommentedSeq(), subject=name, body="Hello,\n\nplease find the document attached.\n")
    return spec


def _label_for(name):
    return name.replace("_", " ").strip().capitalize() or name


def docx_placeholders(path, pack=None):
    """Names used as {{ placeholders }} in a Word document, minus fillmate's own helpers."""
    from docxtpl import DocxTemplate

    from .templating import make_env

    env = make_env(pack)
    try:
        names = DocxTemplate(str(path)).get_undeclared_template_variables(jinja_env=env)
    except Exception as e:
        raise PackError(f"{Path(path).name}: a placeholder is not written correctly ({e}).") from e
    return sorted(n for n in names if n not in env.globals)


def create_pack_from_docx(docx_path, packs_dir, name):
    """Make a new pack folder around a Word document. Each {{ placeholder }} in it becomes a field."""
    docx_path = Path(docx_path)
    root = _new_pack_root(packs_dir, name)
    template = "document.docx"
    shutil.copy(docx_path, root / template)
    fields = CommentedSeq()
    try:
        for ph in docx_placeholders(root / template):
            if NAME_RE.match(ph):
                f = CommentedMap(id=ph, label=_label_for(ph))
                f.fa.set_flow_style()
                fields.append(f)
        doc = CommentedMap(id="document", label=docx_path.stem, template=template,
                           output=f"{docx_path.stem}_filled", placeholders=True)
        return write_spec(root, _skeleton(root, name, fields, doc))
    except PackError:
        shutil.rmtree(root, ignore_errors=True)
        raise


def sync_docx_fields(root):
    """Add a field for every new {{ placeholder }} typed into the pack's Word documents.

    Returns the reloaded pack, or None when nothing changed.
    """
    pack = load_pack(root)
    known = {f.id for f in pack.profile + pack.fields} | set(pack.computed)
    new = []
    for d in pack.documents:
        if d.kind == "docx" and d.placeholders:
            for ph in docx_placeholders(d.template, pack):
                if ph not in known and NAME_RE.match(ph):
                    known.add(ph)
                    new.append(ph)
    if not new:
        return None
    spec = read_spec(root)
    if spec.get("fields") is None:
        spec["fields"] = CommentedSeq()
    for ph in new:
        f = CommentedMap(id=ph, label=_label_for(ph))
        f.fa.set_flow_style()
        spec["fields"].append(f)
    return write_spec(root, spec)


def add_document(root, path):
    """Copy a PDF or Word document into a pack as one more document to fill."""
    root, path = Path(root), Path(path)
    kind = path.suffix.lower()
    if kind not in (".pdf", ".docx"):
        raise PackError("Add a PDF or a Word (.docx) document.")
    spec = read_spec(root)
    docs = spec.get("documents") or CommentedSeq()
    ids = {d.get("id") for d in docs}
    templates = {d.get("template") for d in docs}
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", path.stem).strip("_").lower() or "document"
    doc_id, n = stem, 2
    while doc_id in ids or not ID_RE.match(doc_id):
        doc_id, n = f"{stem}-{n}" if ID_RE.match(stem) else f"document-{n}", n + 1
    name, n = f"{stem}{kind}", 2
    while name in templates or (root / name).exists():
        name, n = f"{stem}_{n}{kind}", n + 1
    shutil.copy(path, root / name)
    doc = CommentedMap(id=doc_id, label=path.stem, template=name, output=f"{path.stem}_filled")
    if kind == ".docx":
        doc["placeholders"] = True
    docs.append(doc)
    spec["documents"] = docs
    try:
        pack = write_spec(root, spec)
    except PackError:
        (root / name).unlink(missing_ok=True)
        raise
    return sync_docx_fields(root) or pack if kind == ".docx" else pack


def remove_document(root, doc_id):
    """Take a document out of a pack and delete its template file (unless another document uses it)."""
    root = Path(root)
    spec = read_spec(root)
    docs = spec.get("documents") or []
    if len(docs) <= 1:
        raise PackError("A pack needs at least one document. Remove the whole pack instead.")
    for i, d in enumerate(docs):
        if d.get("id") == doc_id:
            template = d.get("template")
            del docs[i]
            pack = write_spec(root, spec)
            if template and all(x.get("template") != template for x in docs):
                target = (root / template).resolve()
                if target.parent == root.resolve() and target.is_file():
                    target.unlink()
            return pack
    raise PackError(f"No document '{doc_id}' in the pack.")


def create_pack_from_pdf(pdf_path, packs_dir, name):
    """Make a new pack folder around a PDF. Fillable fields become pack fields automatically."""
    pdf_path = Path(pdf_path)
    root = _new_pack_root(packs_dir, name)
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

    doc = CommentedMap(id="form", label=pdf_path.stem, template=template, output=f"{pdf_path.stem}_filled", fill=fill)
    try:
        return write_spec(root, _skeleton(root, name, fields, doc))
    except PackError:
        shutil.rmtree(root, ignore_errors=True)
        raise


# --- tables ------------------------------------------------------------------

def detect_grids(pdf_path, page_no):
    """Every ruled grid on a page, largest first: {"bbox", "xs", "ys", "headers"}.

    xs are column edges and ys the edges of the rows to fill; rows at the top that already
    have text are the header, left out of ys, and their text is in "headers".
    """
    with pymupdf.open(pdf_path) as pdf:
        page = pdf[page_no]
        try:
            found = page.find_tables().tables
        except Exception:
            return []
        out = []
        for table in found:
            texts = table.extract()
            widest = max(table.rows, key=lambda r: sum(c is not None for c in r.cells))
            cells = [c for c in widest.cells if c is not None]
            xs = [cells[0][0]] + [c[2] for c in cells]
            edges = [r.bbox[1] for r in table.rows] + [table.rows[-1].bbox[3]]
            header = 0
            while header < len(texts) - 1 and any((t or "").strip() for t in texts[header]):
                header += 1
            if len(xs) < 2 or len(edges) - header < 2:
                continue
            names = [""] * (len(xs) - 1)
            for row in texts[:header]:
                for j, t in enumerate(row[:len(names)]):
                    if t and t.strip():
                        names[j] = " ".join(f"{names[j]} {t}".split())
            out.append({"bbox": tuple(round(v, 1) for v in table.bbox), "xs": [round(x, 1) for x in xs],
                        "ys": [round(y, 1) for y in edges[header:]], "headers": names})
    out.sort(key=lambda g: -(g["bbox"][2] - g["bbox"][0]) * (g["bbox"][3] - g["bbox"][1]))
    return out


def pick_grid(grids, rect=None, at=None):
    """The grid around point `at`, or the one overlapping rect the most (at least half of it)."""
    if at is not None:
        x, y = at
        return next((g for g in grids if g["bbox"][0] <= x <= g["bbox"][2] and g["bbox"][1] <= y <= g["bbox"][3]),
                    None)
    if rect is None:
        return grids[0] if grids else None
    best, best_area = None, 0.0
    for g in grids:
        r = pymupdf.Rect(g["bbox"]) & pymupdf.Rect(*rect)
        area = 0.0 if r.is_empty else r.get_area()
        if area > best_area:
            best, best_area = g, area
    grid_area = pymupdf.Rect(best["bbox"]).get_area() if best else 0
    return best if best and best_area >= 0.5 * min(grid_area, pymupdf.Rect(*rect).get_area()) else None


def detect_grid(pdf_path, page_no, rect=None, at=None):
    """The ruled grid inside rect (x0, y0, x1, y1), or around point `at`, or None. See detect_grids."""
    return pick_grid(detect_grids(pdf_path, page_no), rect, at)


KIND_WORDS = {  # header words (several languages) -> what the column probably holds
    "skip": ("signature", "sign", "firma", "unterschrift", "visto", "visa", "signé"),
    "date": ("date", "data", "day", "giorno", "datum", "tag", "jour", "fecha", "día", "dia"),
    "start": ("from", "start", "dalle", "inizio", "entrata", "ingresso", "von", "beginn", "début", "desde",
              "entrada"),
    "end": ("to", "end", "alle", "fine", "uscita", "bis", "ende", "fin", "hasta", "salida"),
    "hours": ("hours", "ore", "hrs", "total", "totale", "stunden", "heures", "horas", "durata", "duration"),
}
SHORT_WORDS = {"in": "start", "out": "end", "h": "hours"}  # only when they are the whole header
# Names a formula cannot use for a column (Jinja operators and values, and what fillmate adds)
RESERVED = {"and", "or", "not", "in", "is", "if", "else", "true", "false", "none", "loop", "sheet", "today"}


def _kind_of(header):
    words = re.findall(r"[^\W\d_]+", header.lower())
    if len(words) == 1 and words[0] in SHORT_WORDS:
        return SHORT_WORDS[words[0]]
    if len(words) > 3:  # a sentence ("Argomento trattato in Azienda") is text, whatever words it has
        return "skip" if any(w in KIND_WORDS["skip"] for w in words) else "text"
    for kind in ("skip", "date", "hours", "start", "end"):
        if any(w in KIND_WORDS[kind] for w in words):
            return kind
    return "text"


def guess_columns(headers, used=()):
    """Columns for a new table from its header texts: (columns, cols for the grid entry).

    Dates and times are recognised by name; an hours column between two times gets the
    formula hours_between(start, end); a signature column is left empty.
    """
    used = set(used) | RESERVED
    columns, cols, kinds = [], [], [_kind_of(h) for h in headers]
    for j, (header, kind) in enumerate(zip(headers, kinds)):
        if kind == "skip":
            cols.append(None)
            continue
        name = header.strip() or f"Column {j + 1}"
        cid = _field_id(" ".join(name.split()[:3]), used)
        c = {"id": cid, "label": name[:1].upper() + name[1:], "type": "text"}
        if kind == "date":
            c["type"] = "date"
        elif kind in ("start", "end"):
            c["type"] = "time"
        c["kind"] = kind
        columns.append(c)
        cols.append(cid)
    starts = [c["id"] for c in columns if c["kind"] == "start"]
    ends = [c["id"] for c in columns if c["kind"] == "end"]
    for c in columns:
        if c.pop("kind") == "hours":
            if starts and ends:
                c["formula"] = f"hours_between({starts[0]}, {ends[0]})"
                c.pop("type")
            else:
                c["type"] = "number"
    return columns, cols


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
