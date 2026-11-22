"""Turn a pack, the user's profile and the entered values into filled documents and an email draft."""
import csv
import datetime as dt
import io
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import InputError, PackError, signatures
from .fill_docx import fill_docx
from .fill_pdf import fill_pdf
from .pack import Pack
from .templating import make_env, parse_date, slug

TIME_RE = re.compile(r"^([01]?\d|2[0-3])(?:[:.h]?([0-5]\d))?$")


@dataclass
class Context:
    pack: Pack
    values: dict
    warnings: list = field(default_factory=list)


@dataclass
class Output:
    document_id: str
    label: str
    path: Path
    kind: str  # "pdf" or "docx"


class Renderer:
    def __init__(self, pack):
        self.pack = pack
        self.env = make_env(pack)

    def text(self, source, values, where):
        try:
            return self.env.from_string(source).render(values)
        except Exception as e:
            raise PackError(f"{where}: {e}") from e

    def expr(self, source, values, where):
        try:
            return self.env.compile_expression(source)(**values)
        except Exception as e:
            raise PackError(f"{where}: {e}") from e


# --- values -------------------------------------------------------------

def _empty(value):
    return value is None or value == "" or value == []


def parse_time(value):
    """'HH:MM' from 9, 930, 9.30, 9h30 or 9:30; None if it is not a time."""
    text = value.strftime("%H:%M") if isinstance(value, dt.time) else str(value).strip().lower()
    if re.fullmatch(r"\d{3}", text):
        text = "0" + text
    m = TIME_RE.match(text)
    return f"{int(m.group(1)):02d}:{m.group(2) or '00'}" if m else None


YES = {"1", "true", "yes", "y", "on", "x", "✓", "✔", "si", "sì", "ja", "oui"}
NO = {"", "0", "false", "no", "n", "off", "none"}


def convert_signature(f, value):
    """A Signature from its id, or yes (your first signature); empty or no means not signed."""
    if isinstance(value, signatures.Signature):
        return value
    if value is None or value is False:
        return ""
    text = "yes" if value is True else str(value).strip()
    if text.lower() in NO:
        return ""
    sig = signatures.first() if text.lower() in YES else signatures.get(text)
    if sig is None and text.lower() in YES and not getattr(f, "required", False):
        return ""  # "sign if I have a signature": nothing to draw yet
    if sig is None:
        if not signatures.list_signatures():
            raise InputError(f"{f.label}: add your signature first, in Settings → Profile → Signatures.")
        raise InputError(f"{f.label}: there is no signature '{text}' in your profile.")
    return sig


def parse_number(value):
    """A number from '4,5', '4.5' or 4; None if it is not one."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    try:
        n = float(str(value).strip().replace(" ", "").replace(",", "."))
    except ValueError:
        return None
    return int(n) if n.is_integer() else n


def convert(f, value, pack):
    """Normalise one raw value (from the GUI, CLI or YAML) to the field's type."""
    if f.type == "table":
        return convert_rows(f, value, pack)
    if f.type == "signature":
        return convert_signature(f, value)
    if f.type == "bool":
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "y", "on", "x")
        return bool(value)
    if _empty(value):
        if f.type == "date" and f.default == "today":
            return dt.date.today()
        return [] if f.type == "dates" else ""
    try:
        if f.type == "date":
            return dt.date.today() if value == "today" else parse_date(value, (pack.date_format,))
        if f.type == "dates":
            items = value.split(",") if isinstance(value, str) else list(value)
            return sorted({parse_date(v, (pack.date_format,)) for v in items if str(v).strip()})
    except ValueError as e:
        raise InputError(f"{f.label}: {e}") from e
    if f.type == "time":
        text = parse_time(value)
        if text is None:
            raise InputError(f"{f.label}: '{value}' is not a time (use HH:MM).")
        return text
    if f.type == "number":
        n = parse_number(value)
        if n is None:
            raise InputError(f"{f.label}: '{value}' is not a number.")
        return n
    if f.type == "choice":
        text = str(value)
        if text not in f.options:
            raise InputError(f"{f.label}: '{text}' is not one of {', '.join(f.options)}.")
        return text
    return str(value).strip() if f.type == "text" else str(value).strip("\n")


def convert_rows(f, value, pack):
    """A table's rows as a list of dicts, typed column by column. Empty rows are dropped.

    Formula columns are left out here; compute_rows fills them in.
    """
    if _empty(value):
        return []
    if not isinstance(value, list) or not all(isinstance(r, dict) for r in value):
        raise InputError(f"{f.label}: expected a list of rows.")
    inputs = [c for c in f.columns if not c.formula]
    rows = []
    for raw in value:
        if all(_empty(raw.get(c.id)) for c in inputs):
            continue
        n = len(rows) + 1
        row = {}
        for c in inputs:
            cell = raw.get(c.id)
            if _empty(cell):
                if c.required:
                    raise InputError(f"{f.label}, row {n}: {c.label} is required.")
                row[c.id] = ""
                continue
            try:
                row[c.id] = convert(c, cell, pack)
            except InputError as e:
                raise InputError(f"{f.label}, row {n}: {e}") from e
        rows.append(row)
    return rows


def rows_from_csv(f, text):
    """Rows for table field `f` from CSV text whose header names the columns (id or label).

    The delimiter (comma, semicolon or tab) is detected; unknown columns are ignored.
    """
    try:
        dialect = csv.Sniffer().sniff(text[:2048], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    header = next(reader, None)
    if not header:
        return []
    names = {}
    for c in f.columns:
        names[c.id.lower()] = c.id
        names[c.label.strip().lower()] = c.id
    ids = [names.get(h.strip().lower()) for h in header]
    if not any(ids):
        raise InputError(f"{f.label}: the CSV header names none of the columns "
                         f"({', '.join(c.label for c in f.columns)}).")
    return [{cid: cell.strip() for cid, cell in zip(ids, line) if cid} for line in reader if any(line)]


def rows_to_csv(f, rows, date_format):
    """CSV text for a table's rows (typed and computed): a header of labels, dates in date_format,
    durations as H:MM, signatures as ✓. rows_from_csv reads it back."""
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow([c.label for c in f.columns])
    for row in rows:
        line = []
        for c in f.columns:
            v = row.get(c.id, "")
            if isinstance(v, signatures.Signature):
                v = "✓"
            elif isinstance(v, dt.date) or (c.type == "date" and v):
                try:
                    v = parse_date(v).strftime(date_format)
                except ValueError:
                    pass
            line.append("" if v is None else str(v))
        w.writerow(line)
    return out.getvalue()


def compute_rows(f, rows, renderer, values=None, strict=True):
    """Fill in the formula columns of each row, in column order.

    A formula sees the row's columns (and the form's other values). When it fails on a row
    with blank cells the result is blank; on a complete row it is an error (or blank, when
    not strict: the form computes live while rows are being typed).
    """
    values = values or {}
    formulas = [c for c in f.columns if c.formula]
    if not formulas:
        return rows
    compiled = [(c, renderer.env.compile_expression(c.formula)) for c in formulas]
    for n, row in enumerate(rows, 1):
        for c, fn in compiled:
            try:
                result = fn(**{**values, **row})
            except Exception as e:
                if strict and all(not _empty(row.get(x.id)) for x in f.columns
                                  if not x.formula and x.type != "signature"):
                    raise PackError(f"{f.label}, row {n}: the formula of {c.label} failed: {e}") from e
                result = ""
            row[c.id] = "" if result is None else result
    return rows


def build_context(pack: Pack, profile: dict, values: dict, preset: dict | None = None) -> Context:
    """Merge profile, preset, defaults and entered values; evaluate computed values and rules.

    Values that are None fall back to the preset, then to the field default.
    """
    preset = preset or {}
    ctx = {}
    missing_profile = []
    for f in pack.profile:
        value = convert(f, profile.get(f.id, f.default), pack)
        if f.required and _empty(value):
            missing_profile.append(f.label)
        ctx[f.id] = value
    if missing_profile:
        raise InputError("Fill in your profile in Settings first: " + ", ".join(missing_profile) + ".")

    for f in pack.fields:
        raw = values.get(f.id)
        if raw is None:
            raw = preset.get(f.id, f.default)
        value = convert(f, raw, pack)
        if f.required and _empty(value):
            raise InputError(f"{f.label} is required.")
        ctx[f.id] = value

    r = Renderer(pack)
    for f in pack.fields:
        if f.type == "table":
            compute_rows(f, ctx[f.id], r, ctx)
    for key, source in pack.computed.items():
        ctx[key] = r.text(source, ctx, f"computed.{key}")

    warnings = []
    for i, rule in enumerate(pack.rules):
        result = r.expr(rule["expr"], ctx, f"rules[{i}]")
        if rule["kind"] == "error_unless" and not result:
            raise InputError(r.text(rule["message"], ctx, f"rules[{i}].message"))
        if rule["kind"] == "warn_if" and result:
            warnings.append(r.text(rule["message"], ctx, f"rules[{i}].message"))
    return Context(pack=pack, values=ctx, warnings=warnings)


# --- documents ----------------------------------------------------------

def safe_filename(name, suffix):
    stem = re.sub(r"[^A-Za-z0-9._ -]+", "_", name).strip(" ._") or "document"
    stem = stem[:120]
    return stem if stem.lower().endswith(suffix) else stem + suffix


def output_folder(ctx: Context, base: Path) -> Path:
    pack = ctx.pack
    name = ""
    if pack.output_folder:
        name = Renderer(pack).text(pack.output_folder, ctx.values, "output_folder")
    name = slug(name, 80) or f"{dt.date.today():%Y-%m-%d}_{pack.id}"
    return Path(base) / name


def generate(ctx: Context, out_dir: Path, only=None) -> list[Output]:
    """Fill every document (or only the ids in `only`) into out_dir."""
    pack, r = ctx.pack, Renderer(ctx.pack)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    outputs = []
    for doc in pack.documents:
        if only and doc.id not in only:
            continue
        suffix = doc.template.suffix.lower()
        name = safe_filename(r.text(doc.output, ctx.values, f"documents.{doc.id}.output"), suffix)
        path = out_dir / name
        if doc.kind == "pdf":
            fill_pdf(doc, ctx.values, r, path)
        else:
            fill_docx(doc, ctx.values, r, path)
        outputs.append(Output(doc.id, doc.label, path, doc.kind))
    return outputs


# --- email --------------------------------------------------------------

def split_addresses(values):
    out = []
    for v in values:
        out += [a.strip() for a in re.split(r"[,;\n]", v) if a.strip()]
    return out


def draft_email(ctx: Context, test_mode=False, test_recipients=()):
    """Recipients, subject and body from the pack's email section.

    In test mode the real recipients are replaced by test_recipients and listed
    at the top of the body, so the test email shows who would have received it.
    """
    pack, r = ctx.pack, Renderer(ctx.pack)
    rcpt = {k: split_addresses(r.text(v, ctx.values, f"email.{k}") for v in pack.email[k])
            for k in ("to", "cc", "bcc")}
    subject = r.text(pack.email["subject"], ctx.values, "email.subject").strip()
    body = r.text(pack.email["body"], ctx.values, "email.body").rstrip("\n") + "\n"
    if test_mode:
        real = ", ".join(rcpt["to"] + rcpt["cc"] + rcpt["bcc"]) or "none"
        rcpt = {"to": list(test_recipients), "cc": [], "bcc": []}
        subject = f"[TEST] {subject}"
        body = f"[TEST - real recipients: {real}]\n\n{body}"
    return {**rcpt, "subject": subject, "body": body}
