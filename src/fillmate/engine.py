"""Turn a pack, the user's profile and the entered values into filled documents and an email draft."""
import datetime as dt
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import InputError, PackError
from .fill_docx import fill_docx
from .fill_pdf import fill_pdf
from .pack import Pack
from .templating import make_env, parse_date, slug

TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


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


def convert(f, value, pack):
    """Normalise one raw value (from the GUI, CLI or YAML) to the field's type."""
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
        text = value.strftime("%H:%M") if isinstance(value, dt.time) else str(value).strip()
        m = TIME_RE.match(text)
        if not m:
            raise InputError(f"{f.label}: '{text}' is not a time (use HH:MM).")
        return f"{int(m.group(1)):02d}:{m.group(2)}"
    if f.type == "choice":
        text = str(value)
        if text not in f.options:
            raise InputError(f"{f.label}: '{text}' is not one of {', '.join(f.options)}.")
        return text
    return str(value).strip() if f.type == "text" else str(value).strip("\n")


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
