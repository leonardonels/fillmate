"""Fill PDF templates: fillable form fields, or text written on the page at fixed positions.

Flat PDFs (no form fields) are common for official paperwork. For those a pack gives,
for each value, one or more lines as [x_start, baseline_y, x_end] in points from the
top-left corner. Words flow across the lines and the font shrinks until the text fits.
"""
import pymupdf

from . import InputError, PackError


def wrap(text, lines, font, size):
    """Distribute words over the available lines; None if they do not fit."""
    words, out = text.split(), []
    for x0, _, x1 in lines:
        line = ""
        while words:
            candidate = f"{line} {words[0]}".strip()
            if pymupdf.get_text_length(candidate, fontname=font, fontsize=size) > x1 - x0:
                break
            line = candidate
            words.pop(0)
        out.append(line)
    return None if words else out


def put_text(page, entry, text, where):
    if not text.strip():
        return
    size, font = entry["size"], entry["font"]
    while (lines := wrap(text, entry["lines"], font, size)) is None:
        size -= 0.5
        if size < entry["min_size"]:
            raise InputError(f"Text too long for {where}: {text!r}")
    for (x0, y, _), line in zip(entry["lines"], lines):
        if line:
            page.insert_text((x0, y), line, fontname=font, fontsize=size)


def set_form_field(pdf, name, value, where):
    found = False
    for page in pdf:
        for widget in page.widgets() or []:
            if widget.field_name != name:
                continue
            found = True
            if widget.field_type == pymupdf.PDF_WIDGET_TYPE_CHECKBOX:
                widget.field_value = widget.on_state() if value.strip().lower() in (
                    "1", "true", "yes", "on", "x") else "Off"
            else:
                widget.field_value = value
            widget.update()
    if not found:
        raise PackError(f"{where}: the PDF has no form field named '{name}'.")


def fill_pdf(doc, values, renderer, out):
    pdf = pymupdf.open(doc.template)
    try:
        for i, entry in enumerate(doc.fill):
            where = f"documents.{doc.id}.fill[{i}]"
            if "field" in entry:
                set_form_field(pdf, entry["field"], renderer.text(entry["value"], values, where), where)
                continue
            if entry["page"] >= len(pdf):
                raise PackError(f"{where}: page {entry['page']} does not exist (the PDF has {len(pdf)}).")
            page = pdf[entry["page"]]
            if "mark" in entry:
                if renderer.expr(entry["mark"], values, where):
                    page.insert_text(entry["at"], entry["symbol"], fontname="helv", fontsize=entry["size"])
            else:
                put_text(page, entry, renderer.text(entry["value"], values, where), doc.label)
        pdf.save(out, garbage=3, deflate=True)
    finally:
        pdf.close()


def render_pages(path, dpi=110, max_pages=6):
    """PNG bytes for the first pages of a PDF (for previews)."""
    with pymupdf.open(path) as pdf:
        return [pdf[i].get_pixmap(dpi=dpi).tobytes("png") for i in range(min(len(pdf), max_pages))]
