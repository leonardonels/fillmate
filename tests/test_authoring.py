import pymupdf
import pytest

from fillmate import PackError, engine
from fillmate.authoring import (
    add_field, create_pack_from_pdf, render_page, sample_text, set_document_fill,
)
from fillmate.pack import load_pack


def flat_pdf(path):
    pdf = pymupdf.open()
    page = pdf.new_page(width=595, height=842)
    page.insert_text((48, 100), "Name ____________________", fontsize=11)
    pdf.save(path)
    return path


def form_pdf(path):
    pdf = pymupdf.open()
    page = pdf.new_page()
    for name, kind, y in (("Full Name", pymupdf.PDF_WIDGET_TYPE_TEXT, 50),
                          ("2nd line", pymupdf.PDF_WIDGET_TYPE_TEXT, 80),
                          ("Agree", pymupdf.PDF_WIDGET_TYPE_CHECKBOX, 110)):
        w = pymupdf.Widget()
        w.field_name, w.field_type, w.rect = name, kind, pymupdf.Rect(50, y, 250, y + 20)
        page.add_widget(w)
    pdf.save(path)
    return path


def test_new_pack_from_flat_pdf_then_calibrate(tmp_path):
    pack = create_pack_from_pdf(flat_pdf(tmp_path / "in.pdf"), tmp_path / "packs", "My Form")
    assert pack.id == "my-form" and pack.documents[0].fill == []
    pack = add_field(pack.root, "name", "Name")
    pack = set_document_fill(pack.root, "form", [
        {"value": "{{ name }}", "page": 0, "lines": [[90.25, 100.1, 300.0]], "font": "helv", "size": 11, "min_size": 7}])
    entry = pack.documents[0].fill[0]
    assert entry["lines"] == [[90.0, 100.0, 300.0]] and entry["size"] == 11  # rounded to half points
    ctx = engine.build_context(pack, {}, {"name": "Ada Lovelace"})
    (out,) = engine.generate(ctx, tmp_path / "out")
    with pymupdf.open(out.path) as d:
        assert "Ada Lovelace" in d[0].get_text()
    # a second pack with the same name gets a new folder
    assert create_pack_from_pdf(tmp_path / "in.pdf", tmp_path / "packs", "My Form").id == "my-form-2"


def test_new_pack_from_fillable_pdf_maps_fields(tmp_path):
    pack = create_pack_from_pdf(form_pdf(tmp_path / "f.pdf"), tmp_path / "packs", "Fillable")
    assert [(f.id, f.type) for f in pack.fields] == [("full_name", "text"), ("f_2nd_line", "text"), ("agree", "bool")]
    ctx = engine.build_context(pack, {}, {"full_name": "Ada", "agree": True})
    (out,) = engine.generate(ctx, tmp_path / "out")
    with pymupdf.open(out.path) as d:
        values = {w.field_name: w.field_value for w in d[0].widgets()}
    assert values["Full Name"] == "Ada" and values["Agree"] not in ("Off", "")


def test_edits_keep_comments_and_roll_back_invalid(example_dir):
    before = (example_dir / "pack.yaml").read_text()
    assert "# Check marks drawn when the expression is true." in before
    pack = load_pack(example_dir)
    entries = pack.documents[0].fill
    entries[0]["size"] = 9
    set_document_fill(example_dir, "request", entries)
    after = (example_dir / "pack.yaml").read_text()
    assert "# Derived values, usable everywhere below." in after
    assert load_pack(example_dir).documents[0].fill[0]["size"] == 9

    with pytest.raises(PackError):
        set_document_fill(example_dir, "request", [{"value": "{{ broken", "lines": [[1, 2, 3]]}])
    assert (example_dir / "pack.yaml").read_text() == after  # restored
    with pytest.raises(PackError, match="already exists"):
        add_field(example_dir, "purpose", "Purpose")


def test_sample_text_and_render(example):
    labels = {"full_name": "Full name"}
    assert sample_text("{{ full_name }} – {{ dates | dates }}{% if x %}!{% endif %}", labels) == "Full name – dates!"
    png, pages = render_page(example.documents[0].template, 0, 72, example.documents[0].fill, labels)
    assert png.startswith(b"\x89PNG") and pages == 1
