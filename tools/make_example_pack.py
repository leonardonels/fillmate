#!/usr/bin/env python3
"""Generate the templates of the bundled example pack (a made-up business travel request).

The PDF is deliberately flat (no form fields) to show text positioning; the DOCX uses
{{ placeholders }}. Run from the repo root: python tools/make_example_pack.py
"""
from pathlib import Path

import docx
import pymupdf
from docx.shared import Pt

OUT = Path(__file__).resolve().parents[1] / "src" / "fillmate" / "examples" / "travel-request"
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


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    make_pdf(OUT / "travel_request.pdf")
    make_docx(OUT / "travel_notice.docx")
    print(f"written to {OUT}")
