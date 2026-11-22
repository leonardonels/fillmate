"""Form packs: a folder with templates and a pack.yaml describing fields, filling and email.

Packs can come from other people, so loading never runs code: YAML is read with
safe_load, template strings run in a sandboxed Jinja environment, and every
template path must stay inside the pack folder.
"""
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import PackError
from .templating import make_env

SPEC_FILE = "pack.yaml"
SPEC_VERSION = 1
FIELD_TYPES = {"text", "multiline", "date", "dates", "time", "bool", "choice", "table", "signature"}
COLUMN_TYPES = {"text", "date", "time", "number", "choice", "signature"}
ALIGNS = {"left", "center", "right"}
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
MAX_ZIP_BYTES = 100 * 1024 * 1024


@dataclass
class Column:
    """One column of a table field: typed in by the person, or computed by a formula."""
    id: str
    label: str
    type: str = "text"
    default: object = None
    options: list = field(default_factory=list)
    formula: str = ""   # a Jinja expression over the row's other columns; empty = typed in
    format: str = ""    # date columns: strftime format (default: the pack's date_format)
    required: bool = False
    placeholder: str = ""
    help: str = ""


@dataclass
class Field:
    id: str
    label: str
    type: str = "text"
    default: object = None
    required: bool = False
    placeholder: str = ""
    help: str = ""
    options: list = field(default_factory=list)
    columns: list = field(default_factory=list)  # table fields only

    def column(self, column_id):
        return next((c for c in self.columns if c.id == column_id), None)


@dataclass
class Document:
    id: str
    label: str
    template: Path
    output: str
    placeholders: bool = False
    fill: list = field(default_factory=list)

    @property
    def kind(self):
        return self.template.suffix.lower().lstrip(".")


@dataclass
class Pack:
    root: Path
    id: str
    name: str
    version: str = "1"
    description: str = ""
    author: str = ""
    country: str = ""
    date_format: str = "%Y-%m-%d"
    profile: list = field(default_factory=list)
    fields: list = field(default_factory=list)
    presets: dict = field(default_factory=dict)
    computed: dict = field(default_factory=dict)
    documents: list = field(default_factory=list)
    email: dict = field(default_factory=dict)
    rules: list = field(default_factory=list)
    output_folder: str = ""

    def field(self, field_id):
        for f in self.profile + self.fields:
            if f.id == field_id:
                return f
        return None


# --- loading ------------------------------------------------------------

def _str(value, where, default=None):
    if value is None:
        if default is None:
            raise PackError(f"{where} is missing.")
        return default
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if not isinstance(value, str):
        raise PackError(f"{where} must be text.")
    return value


def _list(value, where):
    if value is None:
        return []
    if not isinstance(value, list):
        raise PackError(f"{where} must be a list.")
    return value


def _dict(value, where):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise PackError(f"{where} must be a mapping.")
    return value


def _fields(items, where, seen):
    out = []
    for i, raw in enumerate(_list(items, where)):
        w = f"{where}[{i}]"
        raw = _dict(raw, w)
        fid = _str(raw.get("id"), f"{w}.id")
        if not NAME_RE.match(fid):
            raise PackError(f"{w}.id '{fid}' must be letters, digits or _ and not start with a digit.")
        if fid in seen:
            raise PackError(f"Field id '{fid}' is used twice.")
        seen.add(fid)
        ftype = _str(raw.get("type"), f"{w}.type", "text")
        if ftype not in FIELD_TYPES:
            raise PackError(f"{w}.type '{ftype}' is not one of {', '.join(sorted(FIELD_TYPES))}.")
        options = [str(o) for o in _list(raw.get("options"), f"{w}.options")]
        if ftype == "choice" and not options:
            raise PackError(f"{w} is a choice field without options.")
        columns = _columns(raw.get("columns"), f"{w}.columns") if ftype == "table" else []
        if ftype != "table" and raw.get("columns") is not None:
            raise PackError(f"{w}: only table fields have columns.")
        out.append(Field(
            id=fid, label=_str(raw.get("label"), f"{w}.label", fid), type=ftype,
            default=raw.get("default"), required=bool(raw.get("required", False)),
            placeholder=_str(raw.get("placeholder"), f"{w}.placeholder", ""),
            help=_str(raw.get("help"), f"{w}.help", ""), options=options, columns=columns,
        ))
    return out


def _columns(items, where):
    items = _list(items, where)
    if not items:
        raise PackError(f"{where}: a table needs at least one column.")
    out, seen = [], set()
    for i, raw in enumerate(items):
        w = f"{where}[{i}]"
        raw = _dict(raw, w)
        cid = _str(raw.get("id"), f"{w}.id")
        if not NAME_RE.match(cid):
            raise PackError(f"{w}.id '{cid}' must be letters, digits or _ and not start with a digit.")
        if cid in seen:
            raise PackError(f"{where}: column id '{cid}' is used twice.")
        seen.add(cid)
        ctype = _str(raw.get("type"), f"{w}.type", "text")
        if ctype not in COLUMN_TYPES:
            raise PackError(f"{w}.type '{ctype}' is not one of {', '.join(sorted(COLUMN_TYPES))}.")
        options = [str(o) for o in _list(raw.get("options"), f"{w}.options")]
        if ctype == "choice" and not options:
            raise PackError(f"{w} is a choice column without options.")
        out.append(Column(
            id=cid, label=_str(raw.get("label"), f"{w}.label", cid), type=ctype, default=raw.get("default"),
            options=options, formula=_str(raw.get("formula"), f"{w}.formula", ""),
            format=_str(raw.get("format"), f"{w}.format", ""), required=bool(raw.get("required", False)),
            placeholder=_str(raw.get("placeholder"), f"{w}.placeholder", ""),
            help=_str(raw.get("help"), f"{w}.help", ""),
        ))
    return out


def _edges(value, where):
    if (not isinstance(value, list) or len(value) < 2
            or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value)):
        raise PackError(f"{where} must be a list of at least 2 numbers.")
    value = [float(v) for v in value]
    if any(b <= a for a, b in zip(value, value[1:])):
        raise PackError(f"{where} must be increasing.")
    return value


def _numbers(value, n, where):
    if (not isinstance(value, list) or len(value) != n
            or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value)):
        raise PackError(f"{where} must be a list of {n} numbers.")
    return [float(v) for v in value]


def _pdf_entry(raw, w):
    raw = _dict(raw, w)
    page = raw.get("page", 0)
    if not isinstance(page, int) or page < 0:
        raise PackError(f"{w}.page must be a page number starting from 0.")
    if "field" in raw:
        return {"field": _str(raw["field"], f"{w}.field"), "value": _str(raw.get("value"), f"{w}.value")}
    if "sign" in raw:
        box = _numbers(raw.get("box"), 4, f"{w}.box")
        if box[2] <= box[0] or box[3] <= box[1]:
            raise PackError(f"{w}.box must be [x0, y0, x1, y1] with x1 > x0 and y1 > y0.")
        return {"sign": _str(raw["sign"], f"{w}.sign"), "page": page, "box": box,
                "when": _str(raw.get("when"), f"{w}.when", "")}
    if "table" in raw:
        xs, ys = _edges(raw.get("xs"), f"{w}.xs"), _edges(raw.get("ys"), f"{w}.ys")
        cols = _list(raw.get("cols"), f"{w}.cols")
        if len(cols) != len(xs) - 1:
            raise PackError(f"{w}.cols needs one entry per grid column ({len(xs) - 1}); use ~ to leave one empty.")
        cols = [None if c in (None, "") else _str(c, f"{w}.cols") for c in cols]
        align = _list(raw.get("align"), f"{w}.align")
        if align and (len(align) != len(cols) or any(a not in ALIGNS for a in align)):
            raise PackError(f"{w}.align needs one of left/center/right per grid column.")
        overflow = _str(raw.get("overflow"), f"{w}.overflow", "repeat_page")
        if overflow not in ("repeat_page", "error"):
            raise PackError(f"{w}.overflow must be repeat_page or error.")
        return {"table": _str(raw["table"], f"{w}.table"), "page": page, "xs": xs, "ys": ys, "cols": cols,
                "align": [str(a) for a in align], "overflow": overflow,
                "font": _str(raw.get("font"), f"{w}.font", "helv"),
                "size": float(raw.get("size", 9)), "min_size": float(raw.get("min_size", 6))}
    if "mark" in raw:
        return {"mark": _str(raw["mark"], f"{w}.mark"), "page": page,
                "at": _numbers(raw.get("at"), 2, f"{w}.at"),
                "symbol": _str(raw.get("symbol"), f"{w}.symbol", "X"),
                "size": float(raw.get("size", 11))}
    lines = _list(raw.get("lines"), f"{w}.lines")
    if not lines:
        raise PackError(f"{w} needs 'lines' (text), 'field' (fillable PDF field), 'mark' (check mark), "
                        "'table' (a grid) or 'sign' (a signature).")
    return {"value": _str(raw.get("value"), f"{w}.value"), "page": page,
            "lines": [_numbers(line, 3, f"{w}.lines[{j}]") for j, line in enumerate(lines)],
            "font": _str(raw.get("font"), f"{w}.font", "helv"),
            "size": float(raw.get("size", 10)), "min_size": float(raw.get("min_size", 7))}


def _docx_entry(raw, w):
    raw = _dict(raw, w)
    cell = raw.get("cell")
    if not (isinstance(cell, list) and len(cell) == 3 and all(isinstance(c, int) and c >= 0 for c in cell)):
        raise PackError(f"{w}.cell must be [table, row, column], counting from 0.")
    entry = {"cell": cell, "value": _str(raw.get("value"), f"{w}.value"),
             "paragraph": raw.get("paragraph", 0), "run": raw.get("run")}
    for key in ("paragraph", "run"):
        if entry[key] is not None and (not isinstance(entry[key], int) or entry[key] < 0):
            raise PackError(f"{w}.{key} must be a number starting from 0.")
    return entry


def _check_tables(fill, fields, where):
    tables = {f.id: f for f in fields if f.type == "table"}
    repeating = 0
    for j, e in enumerate(fill):
        if "table" not in e:
            continue
        w = f"{where}.fill[{j}]"
        f = tables.get(e["table"])
        if f is None:
            raise PackError(f"{w}.table: '{e['table']}' is not a table field.")
        unknown = [c for c in e["cols"] if c is not None and f.column(c) is None]
        if unknown:
            raise PackError(f"{w}.cols: {', '.join(unknown)} not a column of '{f.id}'.")
        repeating += e["overflow"] == "repeat_page"
    if repeating > 1:
        raise PackError(f"{where}: only one table per document can repeat its page; "
                        "set overflow: error on the others.")


def _check_signs(fill, fields, where):
    kinds = {f.id: f.type for f in fields}
    for j, e in enumerate(fill):
        if "sign" in e and kinds.get(e["sign"]) != "signature":
            raise PackError(f"{where}.fill[{j}].sign: '{e['sign']}' is not a signature field.")


def _inside(root, rel, where):
    path = (root / rel).resolve()
    if not path.is_relative_to(root.resolve()):
        raise PackError(f"{where} points outside the pack folder.")
    return path


def load_pack(root) -> Pack:
    root = Path(root)
    spec_path = root / SPEC_FILE
    if not spec_path.is_file():
        raise PackError(f"No {SPEC_FILE} in {root}.")
    try:
        raw = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise PackError(f"{SPEC_FILE} is not valid YAML: {e}") from e
    raw = _dict(raw, SPEC_FILE)
    version = raw.get("fillmate")
    if version != SPEC_VERSION:
        raise PackError(f"{SPEC_FILE} must start with 'fillmate: {SPEC_VERSION}' (found {version!r}).")
    pack_id = _str(raw.get("id"), "id")
    if not ID_RE.match(pack_id):
        raise PackError(f"Pack id '{pack_id}' must be lowercase letters, digits, '-' or '_'.")

    seen = set()
    profile = _fields(raw.get("profile"), "profile", seen)
    if any(f.type == "table" for f in profile):
        raise PackError("profile: tables belong in fields, not in the profile.")
    fields = _fields(raw.get("fields"), "fields", seen)

    computed = {}
    for key, value in _dict(raw.get("computed"), "computed").items():
        if not NAME_RE.match(str(key)) or key in seen:
            raise PackError(f"computed.{key}: invalid name or clashes with a field.")
        computed[key] = _str(value, f"computed.{key}")

    presets = {}
    for name, values in _dict(raw.get("presets"), "presets").items():
        values = _dict(values, f"presets.{name}")
        unknown = [k for k in values if k not in {f.id for f in fields}]
        if unknown:
            raise PackError(f"presets.{name} sets unknown fields: {', '.join(unknown)}.")
        presets[str(name)] = values

    documents, doc_ids = [], set()
    for i, d in enumerate(_list(raw.get("documents"), "documents")):
        w = f"documents[{i}]"
        d = _dict(d, w)
        doc_id = _str(d.get("id"), f"{w}.id")
        if not ID_RE.match(doc_id) or doc_id in doc_ids:
            raise PackError(f"{w}.id '{doc_id}' is invalid or used twice.")
        doc_ids.add(doc_id)
        template = _inside(root, _str(d.get("template"), f"{w}.template"), f"{w}.template")
        if not template.is_file():
            raise PackError(f"{w}.template: file {template.name} not found in the pack.")
        kind = template.suffix.lower()
        if kind not in (".pdf", ".docx"):
            raise PackError(f"{w}.template must be a .pdf or .docx file.")
        parse = _pdf_entry if kind == ".pdf" else _docx_entry
        fill = [parse(e, f"{w}.fill[{j}]") for j, e in enumerate(_list(d.get("fill"), f"{w}.fill"))]
        _check_tables(fill, fields, w)
        _check_signs(fill, profile + fields, w)
        placeholders = bool(d.get("placeholders", False))
        if placeholders and kind != ".docx":
            raise PackError(f"{w}: 'placeholders' only works with .docx templates.")
        documents.append(Document(
            id=doc_id, label=_str(d.get("label"), f"{w}.label", doc_id), template=template,
            output=_str(d.get("output"), f"{w}.output", template.stem), placeholders=placeholders, fill=fill))
    if not documents:
        raise PackError("The pack has no documents.")

    email = _dict(raw.get("email"), "email")
    for key in ("to", "cc", "bcc"):
        value = email.get(key)
        email[key] = [value] if isinstance(value, str) else [_str(v, f"email.{key}") for v in _list(value, f"email.{key}")]
    for key in ("subject", "body"):
        email[key] = _str(email.get(key), f"email.{key}", "")

    rules = []
    for i, r in enumerate(_list(raw.get("rules"), "rules")):
        w = f"rules[{i}]"
        r = _dict(r, w)
        kinds = [k for k in ("error_unless", "warn_if") if k in r]
        if len(kinds) != 1:
            raise PackError(f"{w} needs exactly one of 'error_unless' or 'warn_if'.")
        rules.append({"kind": kinds[0], "expr": _str(r[kinds[0]], f"{w}.{kinds[0]}"),
                      "message": _str(r.get("message"), f"{w}.message")})

    pack = Pack(
        root=root, id=pack_id, name=_str(raw.get("name"), "name", pack_id),
        version=_str(raw.get("version"), "version", "1"),
        description=_str(raw.get("description"), "description", ""),
        author=_str(raw.get("author"), "author", ""),
        country=_str(raw.get("country"), "country", ""),
        date_format=_str(raw.get("date_format"), "date_format", "%Y-%m-%d"),
        profile=profile, fields=fields, presets=presets, computed=computed, documents=documents,
        email=email, rules=rules, output_folder=_str(raw.get("output_folder"), "output_folder", ""),
    )
    _check_templates(pack)
    return pack


def _check_templates(pack):
    """Report template syntax errors when the pack is loaded, not halfway through filling it."""
    env = make_env(pack)

    def check(source, where, expression=False):
        try:
            env.compile_expression(source) if expression else env.from_string(source)
        except Exception as e:
            raise PackError(f"{where}: {e}") from e

    for key, value in pack.computed.items():
        check(value, f"computed.{key}")
    for f in pack.fields + pack.profile:
        for c in f.columns:
            if c.formula:
                check(c.formula, f"{f.id}.columns.{c.id}.formula", expression=True)
    for d in pack.documents:
        check(d.output, f"documents.{d.id}.output")
        for j, e in enumerate(d.fill):
            if "mark" in e:
                check(e["mark"], f"documents.{d.id}.fill[{j}].mark", expression=True)
            elif "table" in e:
                pass
            elif "sign" in e:
                if e["when"]:
                    check(e["when"], f"documents.{d.id}.fill[{j}].when", expression=True)
            else:
                check(e["value"], f"documents.{d.id}.fill[{j}].value")
    for key in ("subject", "body"):
        check(pack.email[key], f"email.{key}")
    for key in ("to", "cc", "bcc"):
        for v in pack.email[key]:
            check(v, f"email.{key}")
    for i, r in enumerate(pack.rules):
        check(r["expr"], f"rules[{i}]", expression=True)
        check(r["message"], f"rules[{i}].message")
    if pack.output_folder:
        check(pack.output_folder, "output_folder")


# --- installed packs ----------------------------------------------------

def list_packs(packs_dir):
    """Return (packs, problems): valid packs sorted by name, and (folder, error) for broken ones."""
    packs, problems = [], []
    packs_dir = Path(packs_dir)
    if not packs_dir.is_dir():
        return packs, problems
    for sub in sorted(p for p in packs_dir.iterdir() if p.is_dir() and not p.name.startswith(".")):
        try:
            packs.append(load_pack(sub))
        except PackError as e:
            problems.append((sub, str(e)))
    packs.sort(key=lambda p: p.name.lower())
    return packs, problems


def _extract_zip(src, dest):
    with zipfile.ZipFile(src) as z:
        total = 0
        for info in z.infolist():
            total += info.file_size
            if total > MAX_ZIP_BYTES:
                raise PackError("The zip is too large to be a form pack.")
            target = (dest / info.filename).resolve()
            if not target.is_relative_to(dest.resolve()):
                raise PackError(f"The zip contains an unsafe path: {info.filename}")
        z.extractall(dest)


def _find_spec_root(folder):
    if (folder / SPEC_FILE).is_file():
        return folder
    subs = [p for p in folder.iterdir() if p.is_dir() and not p.name.startswith(("_", "."))]
    if len(subs) == 1 and (subs[0] / SPEC_FILE).is_file():
        return subs[0]
    raise PackError(f"No {SPEC_FILE} found. A pack is a folder (or zip) containing {SPEC_FILE}.")


def import_pack(src, packs_dir, replace=False) -> Pack:
    """Validate a pack folder or .zip and copy it into packs_dir/<id>."""
    src, packs_dir = Path(src), Path(packs_dir)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        if src.is_file():
            if not zipfile.is_zipfile(src):
                raise PackError(f"{src.name} is not a zip file.")
            _extract_zip(src, tmp / "zip")
            root = _find_spec_root(tmp / "zip")
        elif src.is_dir():
            root = _find_spec_root(src)
        else:
            raise PackError(f"{src} does not exist.")
        pack = load_pack(root)
        target = packs_dir / pack.id
        if target.exists():
            if root.resolve() == target.resolve():
                return pack
            if not replace:
                raise PackError(f"A pack with id '{pack.id}' is already installed.")
            shutil.rmtree(target)
        packs_dir.mkdir(parents=True, exist_ok=True)
        shutil.copytree(root, target, ignore=shutil.ignore_patterns("__pycache__", ".*"))
    return load_pack(target)


def export_pack(pack: Pack, dest) -> Path:
    """Zip the pack folder. Profile data and saved presets live elsewhere and are never included."""
    dest = Path(dest)
    if dest.suffix.lower() != ".zip":
        dest = dest.with_suffix(".zip")
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(pack.root.rglob("*")):
            rel = path.relative_to(pack.root)
            if path.is_file() and not any(part.startswith(".") or part == "__pycache__" for part in rel.parts):
                z.write(path, Path(pack.id) / rel)
    return dest


def remove_pack(pack: Pack, packs_dir):
    root = pack.root.resolve()
    if root.parent != Path(packs_dir).resolve():
        raise PackError("Only packs inside the packs folder can be removed.")
    shutil.rmtree(root)
