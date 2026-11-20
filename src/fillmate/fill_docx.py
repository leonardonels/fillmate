"""Fill DOCX templates, either with {{ placeholders }} or by writing into table cells."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import docx
from docxtpl import DocxTemplate

from . import FillmateError, PackError
from .paths import cache_dir
from .templating import make_env


def set_cell(cell, text, paragraph=0, run=None):
    """Write into one run of a cell paragraph, keeping the template's formatting.

    With run=None the first run gets the text and later runs are cleared, so the
    cell shows only the new value. Line breaks in the text become line breaks.
    """
    paragraphs = cell.paragraphs
    if paragraph >= len(paragraphs):
        raise IndexError(f"paragraph {paragraph} (the cell has {len(paragraphs)})")
    p = paragraphs[paragraph]
    runs = p.runs
    if run is None:
        target = runs[0] if runs else p.add_run()
        for extra in runs[1:]:
            extra.text = ""
    else:
        if run >= len(runs):
            raise IndexError(f"run {run} (the paragraph has {len(runs)})")
        target = runs[run]
    lines = text.split("\n")
    target.text = lines[0]
    for line in lines[1:]:
        target.add_break()
        target.add_text(line)


def fill_docx(doc, values, renderer, out):
    if doc.placeholders:
        tpl = DocxTemplate(doc.template)
        env = make_env(renderer.pack)
        env.autoescape = True  # values go into XML: "&" and "<" must be escaped
        try:
            tpl.render(values, jinja_env=env)
        except Exception as e:
            raise PackError(f"documents.{doc.id}: placeholder error: {e}") from e
        tpl.save(out)
        source = out
    else:
        source = doc.template
    if doc.fill:
        d = docx.Document(source)
        for i, entry in enumerate(doc.fill):
            where = f"documents.{doc.id}.fill[{i}]"
            t, row, col = entry["cell"]
            try:
                cell = d.tables[t].cell(row, col)
                set_cell(cell, renderer.text(entry["value"], values, where), entry["paragraph"], entry["run"])
            except IndexError as e:
                raise PackError(f"{where}: no such table cell or position ({e}).") from e
        d.save(out)


# --- conversion to PDF (optional, needs LibreOffice) -------------------

def find_libreoffice():
    for name in ("soffice", "libreoffice"):
        if path := shutil.which(name):
            return path
    candidates = []
    if sys.platform == "darwin":
        candidates.append("/Applications/LibreOffice.app/Contents/MacOS/soffice")
    elif os.name == "nt":
        for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
            if base:
                candidates.append(os.path.join(base, "LibreOffice", "program", "soffice.exe"))
    return next((c for c in candidates if os.path.isfile(c)), None)


def docx_to_pdf(path: Path, outdir: Path | None = None) -> Path:
    soffice = find_libreoffice()
    if not soffice:
        raise FillmateError("LibreOffice is needed to convert DOCX documents to PDF. Install it from libreoffice.org.")
    outdir = Path(outdir or path.parent)
    outdir.mkdir(parents=True, exist_ok=True)
    # A dedicated profile lets the conversion run even while LibreOffice is open.
    profile = (cache_dir() / "lo-profile").resolve().as_uri()
    try:
        subprocess.run([soffice, f"-env:UserInstallation={profile}", "--headless", "--convert-to", "pdf",
                        "--outdir", str(outdir), str(path)], check=True, capture_output=True, timeout=120)
    except (subprocess.SubprocessError, OSError) as e:
        raise FillmateError(f"LibreOffice could not convert {path.name}: {e}") from e
    result = outdir / path.with_suffix(".pdf").name
    if not result.is_file():
        raise FillmateError(f"LibreOffice did not produce a PDF for {path.name}.")
    return result
