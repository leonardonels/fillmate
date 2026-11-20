import datetime as dt

import docx
import pymupdf
import pytest
import yaml

from fillmate import InputError, engine
from fillmate.pack import load_pack
from fillmate.templating import working_days_between


def pdf_words(path):
    with pymupdf.open(path) as d:
        return d[0].get_text()


def docx_text(path):
    d = docx.Document(path)
    parts = [p.text for p in d.paragraphs]
    parts += [c.text for t in d.tables for r in t.rows for c in r.cells]
    return "\n".join(parts)


def test_generate_example(example, profile, tmp_path):
    ctx = engine.build_context(example, profile, {"dates": ["01/12/2026", "2026-12-02"], "overnight": True},
                               example.presets["head-office"])
    outs = engine.generate(ctx, tmp_path / "out")
    assert [o.path.name for o in outs] == ["Travel_request_Jane_Doe_20261201.pdf", "Travel_notice_20261201.docx"]
    text = pdf_words(outs[0].path)
    for expected in ("Jane Doe", "01/12/2026, 02/12/2026", "Head office, 1 Main Street, Leeds", "Client meeting"):
        assert expected in text
    assert text.count("X") == 2  # train + overnight
    notice = docx_text(outs[1].path)
    assert "Head office," in notice and "Jane Doe (R&D)" in notice and "{{" not in notice


def test_long_text_wraps_and_shrinks(example, profile, tmp_path):
    purpose = "Workshop " * 40
    ctx = engine.build_context(example, profile, {"dates": ["2030-01-07"], "destination": "Leeds",
                                                  "purpose": purpose})
    outs = engine.generate(ctx, tmp_path, only={"request"})
    assert pdf_words(outs[0].path).count("Workshop") == 40
    ctx.values["purpose"] = "Workshop " * 400
    with pytest.raises(InputError, match="too long"):
        engine.generate(ctx, tmp_path, only={"request"})


def test_required_profile_and_fields(example, profile):
    with pytest.raises(InputError, match="profile"):
        engine.build_context(example, {}, {"dates": ["2030-01-07"], "destination": "x"})
    with pytest.raises(InputError, match="Travel dates is required"):
        engine.build_context(example, profile, {"destination": "x"})
    with pytest.raises(InputError, match="Enter a destination"):
        engine.build_context(example, profile, {"dates": ["2030-01-07"]})
    with pytest.raises(InputError, match="not a time"):
        engine.build_context(example, profile, {"dates": ["2030-01-07"], "destination": "x", "start_time": "25:00"})
    with pytest.raises(InputError, match="not one of"):
        engine.build_context(example, profile, {"dates": ["2030-01-07"], "destination": "x", "transport": "Boat"})


def test_values_override_preset_and_defaults(example, profile):
    ctx = engine.build_context(example, profile, {"dates": ["2030-01-07"], "transport": "Plane"},
                               example.presets["airport-site"])
    v = ctx.values
    assert v["transport"] == "Plane" and v["overnight"] is True
    assert v["destination_short"] == "Airport site" and v["start_time"] == "09:00"
    assert v["requested_on"] == dt.date.today()


def test_warning_rule(example, profile):
    soon = dt.date.today() + dt.timedelta(days=1)
    ctx = engine.build_context(example, profile, {"dates": [soon], "destination": "x"})
    assert ctx.warnings and "working days" in ctx.warnings[0]


def test_working_days_skip_holidays():
    # 24 Dec 2026 (Thu) -> 29 Dec (Tue): 25 Dec Christmas and 28 Dec (Boxing Day substitute) are UK holidays.
    assert working_days_between(dt.date(2026, 12, 24), dt.date(2026, 12, 29), "GB") == 0
    assert working_days_between(dt.date(2026, 12, 24), dt.date(2026, 12, 29), "") == 2
    # Italy: Easter Monday 2026 is 6 April.
    assert working_days_between(dt.date(2026, 4, 3), dt.date(2026, 4, 8), "IT") == 1


def test_docx_cell_mode_and_acroform(tmp_path, profile):
    """A pack writing into DOCX table cells and into fillable PDF fields."""
    d = docx.Document()
    t = d.add_table(rows=2, cols=2)
    t.cell(0, 0).text, t.cell(1, 0).text = "Name", "Times"
    t.cell(1, 1).paragraphs[0].add_run("From ")
    t.cell(1, 1).paragraphs[0].add_run("")
    t.cell(1, 1).paragraphs[0].add_run(" to ")
    t.cell(1, 1).paragraphs[0].add_run("")
    d.save(tmp_path / "t.docx")

    pdf = pymupdf.open()
    page = pdf.new_page()
    for name, kind, y in (("name", pymupdf.PDF_WIDGET_TYPE_TEXT, 50), ("agree", pymupdf.PDF_WIDGET_TYPE_CHECKBOX, 90)):
        w = pymupdf.Widget()
        w.field_name, w.field_type, w.rect = name, kind, pymupdf.Rect(50, y, 250, y + 20)
        page.add_widget(w)
    pdf.save(tmp_path / "f.pdf")

    (tmp_path / "pack.yaml").write_text(yaml.safe_dump({
        "fillmate": 1, "id": "cells",
        "profile": [{"id": "full_name"}],
        "fields": [{"id": "start", "type": "time", "default": "08:00"}, {"id": "agree", "type": "bool"}],
        "documents": [
            {"id": "word", "template": "t.docx", "fill": [
                {"cell": [0, 0, 1], "value": "{{ full_name }}\nsecond line"},
                {"cell": [0, 1, 1], "run": 1, "value": "{{ start }}"},
                {"cell": [0, 1, 1], "run": 3, "value": "17:00"}]},
            {"id": "form", "template": "f.pdf", "fill": [
                {"field": "name", "value": "{{ full_name }}"},
                {"field": "agree", "value": "{{ agree }}"}]},
        ],
    }))
    pack = load_pack(tmp_path)
    ctx = engine.build_context(pack, profile, {"agree": True})
    word, form = engine.generate(ctx, tmp_path / "out")
    t = docx.Document(word.path).tables[0]
    assert t.cell(0, 1).text == "Jane Doe\nsecond line"
    assert t.cell(1, 1).text == "From 08:00 to 17:00"
    with pymupdf.open(form.path) as f:
        values = {w.field_name: w.field_value for w in f[0].widgets()}
    assert values["name"] == "Jane Doe" and values["agree"] not in ("Off", "", False)


def test_output_names_are_safe(example, profile, tmp_path):
    ctx = engine.build_context(example, profile, {"dates": ["2030-01-07"], "destination": "../../etc, x"})
    folder = engine.output_folder(ctx, tmp_path)
    assert folder.parent == tmp_path and ".." not in folder.name
    assert engine.safe_filename("../a/b:c", ".pdf") == "a_b_c.pdf"
