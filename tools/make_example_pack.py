#!/usr/bin/env python3
"""Generate the templates of the bundled example packs (made-up company forms).

travel-request: a flat PDF (text by position) and a DOCX with {{ placeholders }}.
timesheet: a ruled grid filled as a table, with per-page totals.
Run from the repo root: python tools/make_example_pack.py
"""
from pathlib import Path

import docx
import pymupdf
from docx.shared import Pt

EXAMPLES = Path(__file__).resolve().parents[1] / "src" / "fillmate" / "examples"
OUT = EXAMPLES / "travel-request"
TIMESHEET = EXAMPLES / "timesheet"
# The timesheet grid: column edges, the header row, then GRID_ROWS rows of ROW_H points.
GRID_XS = (40, 110, 160, 210, 260, 470, 555)
GRID_TOP, HEADER_H, ROW_H, GRID_ROWS = 190, 24, 22, 20
GREY = (0.35, 0.35, 0.35)


def label(page, x, y, text, size=10, bold=False):
    page.insert_text((x, y), text, fontname="hebo" if bold else "helv", fontsize=size, color=GREY)


def rule(page, x0, x1, y):
    page.draw_line((x0, y + 3), (x1, y + 3), color=(0.6, 0.6, 0.6), width=0.6)


def box(page, x, y):
    page.draw_rect(pymupdf.Rect(x, y - 10, x + 12, y + 2), color=(0.4, 0.4, 0.4), width=0.8)


def make_pdf(path):
    pdf = pymupdf.open()
    page = pdf.new_page(width=595, height=842)
    page.insert_text((48, 80), "Business Travel Request", fontname="hebo", fontsize=20)
    label(page, 48, 100, "Example Company Ltd. · Form TR-1 · fill in and send to your manager")

    label(page, 48, 150, "Date of request")
    rule(page, 140, 260, 150)
    label(page, 48, 200, "Employee")
    rule(page, 140, 545, 200)
    label(page, 48, 230, "Employee ID")
    rule(page, 140, 260, 230)
    label(page, 280, 230, "Department")
    rule(page, 345, 545, 230)

    label(page, 48, 280, "Travel dates")
    rule(page, 140, 545, 280)
    label(page, 48, 315, "Destination")
    rule(page, 140, 545, 315)
    rule(page, 48, 545, 340)
    label(page, 48, 375, "Purpose")
    rule(page, 140, 545, 375)
    rule(page, 48, 545, 400)
    rule(page, 48, 545, 425)

    label(page, 48, 470, "Transport")
    for x, name in ((140, "Train"), (220, "Car"), (290, "Plane")):
        box(page, x, 470)
        label(page, x + 18, 470, name)
    label(page, 48, 500, "Overnight stay")
    box(page, 140, 500)
    label(page, 158, 500, "Yes")

    label(page, 48, 560, "Notes")
    rule(page, 140, 545, 560)
    rule(page, 48, 545, 585)

    label(page, 48, 680, "Employee signature")
    rule(page, 48, 260, 720)
    label(page, 335, 680, "Manager approval")
    rule(page, 335, 545, 720)
    pdf.save(path, garbage=3, deflate=True)


def make_docx(path):
    d = docx.Document()
    style = d.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)
    d.add_heading("Travel notice", level=1)
    d.add_paragraph("To the reception desk of {{ destination_short }},")
    d.add_paragraph("please note the following visit:")
    table = d.add_table(rows=0, cols=2)
    table.style = "Light Grid Accent 1"
    for key, value in (
        ("Visitor", "{{ full_name }} ({{ department }})"),
        ("Dates", "{{ dates | dates }}"),
        ("Hours", "{{ start_time }} – {{ end_time }}"),
        ("Purpose", "{{ purpose }}"),
    ):
        row = table.add_row().cells
        row[0].text, row[1].text = key, value
    d.add_paragraph("")
    d.add_paragraph("{% if notes %}Notes: {{ notes }}{% endif %}")
    d.add_paragraph("Kind regards,\n{{ full_name }}")
    d.save(path)


def make_timesheet(path):
    pdf = pymupdf.open()
    page = pdf.new_page(width=595, height=842)
    page.insert_text((40, 80), "Monthly Timesheet", fontname="hebo", fontsize=20)
    label(page, 40, 100, "Example Company Ltd. · Form TS-2 · one row per block of work")
    label(page, 40, 140, "Employee")
    rule(page, 110, 300, 140)
    label(page, 320, 140, "Month")
    rule(page, 365, 555, 140)
    label(page, 40, 165, "Project")
    rule(page, 110, 555, 165)

    xs, bottom = GRID_XS, GRID_TOP + HEADER_H + ROW_H * GRID_ROWS
    ink = (0.3, 0.3, 0.3)
    page.draw_rect(pymupdf.Rect(xs[0], GRID_TOP, xs[-1], bottom), color=ink, width=0.8)
    page.draw_rect(pymupdf.Rect(xs[0], GRID_TOP, xs[-1], GRID_TOP + HEADER_H), color=ink, fill=(0.93, 0.93, 0.93),
                   width=0.8)
    for x in xs[1:-1]:
        page.draw_line((x, GRID_TOP), (x, bottom), color=ink, width=0.6)
    for k in range(1, GRID_ROWS + 1):
        y = GRID_TOP + HEADER_H + ROW_H * k
        page.draw_line((xs[0], y), (xs[-1], y), color=ink, width=0.6)
    for (x0, x1), name in zip(zip(xs, xs[1:]), ("Date", "From", "To", "Hours", "Activity", "Signature")):
        w = pymupdf.get_text_length(name, fontname="hebo", fontsize=9)
        page.insert_text(((x0 + x1 - w) / 2, GRID_TOP + 15), name, fontname="hebo", fontsize=9)

    y = bottom + 30
    label(page, 40, y, "Days on this sheet")
    rule(page, 140, 200, y)
    label(page, 230, y, "Hours on this sheet")
    rule(page, 335, 395, y)
    label(page, 430, y, "Sheet")
    rule(page, 465, 555, y)
    label(page, 40, y + 60, "Employee signature")
    rule(page, 40, 250, y + 95)
    label(page, 335, y + 60, "Supervisor")
    rule(page, 335, 555, y + 95)
    pdf.save(path, garbage=3, deflate=True)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    make_pdf(OUT / "travel_request.pdf")
    make_docx(OUT / "travel_notice.docx")
    TIMESHEET.mkdir(parents=True, exist_ok=True)
    make_timesheet(TIMESHEET / "timesheet.pdf")
    print(f"written to {EXAMPLES}")
