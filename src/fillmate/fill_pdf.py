"""Fill PDF templates: fillable form fields, or text written on the page at fixed positions.

Flat PDFs (no form fields) are common for official paperwork. For those a pack gives,
for each value, one or more lines as [x_start, baseline_y, x_end] in points from the
top-left corner. Words flow across the lines and the font shrinks until the text fits.

Tables are a grid (column edges `xs`, row edges `ys`) filled row by row. When a table has
more rows than the grid, its page is repeated; every entry on that page is drawn on each
copy, with `sheet` (that copy's rows, its number and the count) among the values.
"""
import datetime as dt

import pymupdf

from . import InputError, PackError
from .templating import parse_date

CELL_PAD = 2.0
LINE_GAP = 1.15


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


def fit(text, lines, font, size, min_size, where):
    """(size, words per line) at the largest size from `size` down that fits."""
    while (out := wrap(text, lines, font, size)) is None:
        size -= 0.5
        if size < min_size:
            raise InputError(f"Text too long for {where}: {text!r}")
    return size, out


def put_text(page, entry, text, where):
    if not text.strip():
        return
    font = entry["font"]
    size, lines = fit(text, entry["lines"], font, entry["size"], entry["min_size"], where)
    for (x0, y, _), line in zip(entry["lines"], lines):
        if line:
            page.insert_text((x0, y), line, fontname=font, fontsize=size)


def cell_lines(x0, top, x1, bottom, size):
    """Text lines [x_start, baseline, x_end] that fit in one cell, centred vertically."""
    h = bottom - top
    n = max(1, int((h - CELL_PAD) // (size * LINE_GAP)))
    block = size * 0.72 + (n - 1) * size * LINE_GAP  # cap height of the first line + the gaps
    first = top + (h - block) / 2 + size * 0.72
    return [[x0 + CELL_PAD, first + k * size * LINE_GAP, x1 - CELL_PAD] for k in range(n)]


def put_cell(page, rect, text, align, font, size, min_size, where):
    text = str(text).strip()
    if not text:
        return
    x0, top, x1, bottom = rect
    lines = cell_lines(x0, top, x1, bottom, size)
    size, words = fit(text, lines, font, size, min_size, where)
    for (lx0, y, lx1), line in zip(lines, words):
        if not line:
            continue
        x = lx0
        if align != "left":
            spare = (lx1 - lx0) - pymupdf.get_text_length(line, fontname=font, fontsize=size)
            x += spare if align == "right" else spare / 2
        page.insert_text((x, y), line, fontname=font, fontsize=size)


def cell_text(column, value, date_format):
    if value in (None, ""):
        return ""
    if column is not None and column.type == "date" or isinstance(value, dt.date):
        try:
            return parse_date(value).strftime(column.format if column and column.format else date_format)
        except ValueError:
            return str(value)
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def put_table(page, entry, field, rows, date_format, where):
    xs, ys = entry["xs"], entry["ys"]
    for i, row in enumerate(rows):
        for j, cid in enumerate(entry["cols"]):
            if cid is None:
                continue
            column = field.column(cid)
            align = entry["align"][j] if entry["align"] else ("left" if column.type == "text" and not column.formula else "center")
            put_cell(page, (xs[j], ys[i], xs[j + 1], ys[i + 1]), cell_text(column, row.get(cid), date_format),
                     align, entry["font"], entry["size"], entry["min_size"], f"{where}, row {i + 1}, {column.label}")


def sheets_of(rows, capacity):
    """Rows split into pages of `capacity` (at least one page, even when empty)."""
    return [rows[k:k + capacity] for k in range(0, len(rows), capacity)] or [[]]


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
    pack = renderer.pack
    pdf = pymupdf.open(doc.template)
    try:
        for i, entry in enumerate(doc.fill):
            if "field" not in entry and entry["page"] >= len(pdf):
                raise PackError(f"documents.{doc.id}.fill[{i}]: page {entry['page']} does not exist "
                                f"(the PDF has {len(pdf)}).")
        tables = [e for e in doc.fill if "table" in e]
        for e in tables:
            n, capacity = len(values.get(e["table"]) or []), len(e["ys"]) - 1
            if e["overflow"] == "error" and n > capacity:
                raise InputError(f"{doc.label}: {pack.field(e['table']).label} has {n} rows, "
                                 f"but the page only has room for {capacity}.")
        repeat = next((e for e in tables if e["overflow"] == "repeat_page"), None)
        main = repeat or (tables[0] if tables else None)
        all_rows = list(values.get(main["table"]) or []) if main else []
        if repeat:
            sheets = sheets_of(all_rows, len(repeat["ys"]) - 1)
            base = repeat["page"]
            for _ in range(len(sheets) - 1):  # copies of the blank page, right after it
                pdf.fullcopy_page(base, base + 1 if base + 1 < len(pdf) else -1)
        else:
            sheets, base = [all_rows], -1
        extra = len(sheets) - 1

        def targets(page_no):
            """(page index, sheet values) for each copy an entry on page_no is drawn on."""
            if page_no == base:
                return [(base + k, {"rows": rows, "number": k + 1, "count": len(sheets)})
                        for k, rows in enumerate(sheets)]
            shifted = page_no + extra if repeat and page_no > base else page_no
            return [(shifted, {"rows": all_rows, "number": 1, "count": len(sheets)})]

        for i, entry in enumerate(doc.fill):
            where = f"documents.{doc.id}.fill[{i}]"
            if "field" in entry:
                set_form_field(pdf, entry["field"], renderer.text(entry["value"], values, where), where)
                continue
            for page_no, sheet in targets(entry["page"]):
                page = pdf[page_no]
                vals = {**values, "sheet": sheet} if main else values
                if "table" in entry:
                    rows = sheet["rows"] if entry is repeat else (values.get(entry["table"]) or [])
                    if entry is not repeat and page_no != targets(entry["page"])[0][0]:
                        continue  # a table that does not repeat is drawn once
                    put_table(page, entry, pack.field(entry["table"]), rows, pack.date_format, doc.label)
                elif "mark" in entry:
                    if renderer.expr(entry["mark"], vals, where):
                        page.insert_text(entry["at"], entry["symbol"], fontname="helv", fontsize=entry["size"])
                else:
                    put_text(page, entry, renderer.text(entry["value"], vals, where), doc.label)
        pdf.save(out, garbage=3, deflate=True)
    finally:
        pdf.close()


def render_pages(path, dpi=110, max_pages=6):
    """PNG bytes for the first pages of a PDF (for previews)."""
    with pymupdf.open(path) as pdf:
        return [pdf[i].get_pixmap(dpi=dpi).tobytes("png") for i in range(min(len(pdf), max_pages))]
