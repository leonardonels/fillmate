"""Positions saved with their new fields, removing unused fields, packs made from Word documents."""
import docx
import pytest

from fillmate import PackError
from fillmate.authoring import (
    add_document, create_pack_from_docx, create_pack_from_pdf, docx_placeholders, field_uses, remove_document,
    remove_field, save_positions, sync_docx_fields,
)
from fillmate.pack import load_pack


@pytest.fixture
def flat_pdf(tmp_path):
    import pymupdf

    path = tmp_path / "flat.pdf"
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((72, 72), "Name: ________")
        pdf.save(path)
    return path


def word(tmp_path, *paragraphs):
    path = tmp_path / "letter.docx"
    d = docx.Document()
    for text in paragraphs:
        d.add_paragraph(text)
    d.save(path)
    return path


def test_new_fields_are_written_only_with_the_positions(flat_pdf, tmp_path):
    pack = create_pack_from_pdf(flat_pdf, tmp_path / "packs", "Flat")
    entry = {"value": "{{ visitor }}", "page": 0, "lines": [[100, 72, 300]], "size": 10}
    pack = save_positions(pack.root, "form", [entry], [{"id": "visitor", "label": "Visitor", "type": "text"}])
    assert [f.id for f in pack.fields] == ["visitor"]
    assert pack.documents[0].fill[0]["value"] == "{{ visitor }}"
    # saving again with the same new field does not duplicate it
    pack = save_positions(pack.root, "form", [entry], [{"id": "visitor", "label": "Visitor", "type": "text"}])
    assert [f.id for f in pack.fields] == ["visitor"]


def test_unused_field_can_be_removed_used_one_cannot(flat_pdf, tmp_path):
    pack = create_pack_from_pdf(flat_pdf, tmp_path / "packs", "Flat")
    entry = {"value": "{{ visitor }}", "page": 0, "lines": [[100, 72, 300]], "size": 10}
    pack = save_positions(pack.root, "form", [entry], [{"id": "visitor", "label": "Visitor"},
                                                       {"id": "data", "label": "Data"}])
    assert field_uses(pack.root, "data") == []
    pack = remove_field(pack.root, "data")
    assert [f.id for f in pack.fields] == ["visitor"]
    with pytest.raises(PackError):
        remove_field(pack.root, "visitor")


def test_pack_from_word_document(tmp_path):
    src = word(tmp_path, "Dear {{ manager_name }},", "I travel to {{ destination }} on {{ day }}.")
    assert docx_placeholders(src) == ["day", "destination", "manager_name"]
    pack = create_pack_from_docx(src, tmp_path / "packs", "Letter")
    assert {f.id: f.label for f in pack.fields} == {"day": "Day", "destination": "Destination",
                                                    "manager_name": "Manager name"}
    assert pack.documents[0].kind == "docx" and pack.documents[0].placeholders


def test_new_placeholders_become_fields(tmp_path):
    pack = create_pack_from_docx(word(tmp_path, "Hello"), tmp_path / "packs", "Letter")
    assert pack.fields == [] and sync_docx_fields(pack.root) is None
    d = docx.Document(pack.documents[0].template)
    d.add_paragraph("See you in {{ city }}.")
    d.save(pack.documents[0].template)
    pack = sync_docx_fields(pack.root)
    assert [f.id for f in pack.fields] == ["city"]
    assert load_pack(pack.root).fields[0].label == "City"


def test_add_and_remove_documents(flat_pdf, tmp_path):
    pack = create_pack_from_pdf(flat_pdf, tmp_path / "packs", "Flat")
    pack = add_document(pack.root, word(tmp_path, "Trip to {{ city }}"))
    assert [d.kind for d in pack.documents] == ["pdf", "docx"]
    assert [f.id for f in pack.fields] == ["city"]
    added = pack.documents[1]
    assert added.template.is_file()
    pack = remove_document(pack.root, added.id)
    assert [d.id for d in pack.documents] == ["form"]
    assert not added.template.exists()
    with pytest.raises(PackError):
        remove_document(pack.root, "form")  # the last document stays: remove the pack instead
