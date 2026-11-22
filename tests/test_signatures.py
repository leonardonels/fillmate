import shutil
import zipfile

import numpy as np
import pymupdf
import pytest
import yaml

from fillmate import InputError, PackError, engine, signatures
from fillmate.pack import export_pack, load_pack
from fillmate.paths import examples_dir
from fillmate.templating import make_env

PROFILE = {"full_name": "Jane Doe", "manager_email": "boss@example.com"}


def scribble(path, shift=0.0, dpi=150):
    """A made-up handwritten line on white paper with a wide margin, saved as an image."""
    pdf = pymupdf.open()
    page = pdf.new_page(width=300, height=150)
    shape = page.new_shape()
    shape.draw_bezier((60, 90 + shift), (100, 20), (150, 140), (240, 60 - shift))
    shape.finish(color=(0.05, 0.1, 0.4), width=2.5)
    shape.commit()
    page.get_pixmap(dpi=dpi).save(path)
    return path


def pdf_with_signature(path):
    pdf = pymupdf.open()
    page = pdf.new_page(width=595, height=842)
    page.insert_text((50, 100), "Some other private text on the page", fontsize=12)
    shape = page.new_shape()
    shape.draw_bezier((320, 520), (350, 470), (400, 560), (460, 500))
    shape.finish(color=(0, 0, 0), width=2)
    shape.commit()
    pdf.save(path)
    return path


def alpha(png_path):
    pix = pymupdf.Pixmap(str(png_path))
    assert pix.alpha
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, 3], pix


@pytest.fixture
def ts(tmp_path):
    dest = tmp_path / "timesheet"
    shutil.copytree(examples_dir() / "timesheet", dest)
    return load_pack(dest)


def test_image_becomes_transparent_and_trimmed(tmp_path):
    src = scribble(tmp_path / "sig.png")
    sig = signatures.add_images([src], "My signature")
    assert sig.id == "my_signature" and sig.name == "My signature" and len(sig.files) == 1
    a, pix = alpha(sig.files[0])
    original = pymupdf.Pixmap(str(src))
    assert pix.width < original.width * 0.75 and pix.height < original.height  # the margin is gone
    assert a[0, 0] == 0 and a[-1, -1] == 0 and a.max() == 255  # paper transparent, ink opaque
    assert (sig.files[0].stat().st_mode & 0o777) == 0o600
    assert str(sig) == ""  # writes nothing in text templates
    assert make_env().from_string("[{{ s }}]").render(s=sig) == "[]"


def test_variants_names_rename_remove(tmp_path):
    a = signatures.add_images([scribble(tmp_path / "1.png"), scribble(tmp_path / "2.png", 15)], "Full")
    b = signatures.add_images([scribble(tmp_path / "3.png")], "Full")  # same name: its own id
    assert len(a.files) == 2 and b.id == "full_2"
    signatures.add_images([scribble(tmp_path / "4.png", -10)], "", to=a.id)
    assert len(signatures.get(a.id).files) == 3
    signatures.rename(b.id, "Initials")
    assert [s.name for s in signatures.list_signatures()] == ["Full", "Initials"]
    signatures.remove(a.id, variant=0)
    assert len(signatures.get(a.id).files) == 2
    signatures.remove(b.id)
    assert signatures.get(b.id) is None and not any(f.exists() for f in b.files)


def test_blank_image_and_bad_file(tmp_path):
    blank = tmp_path / "blank.png"
    pymupdf.open().new_page(width=50, height=50).get_pixmap().save(blank)
    with pytest.raises(InputError, match="no signature found"):
        signatures.add_images([blank], "x")
    (tmp_path / "bad.png").write_text("not an image")
    with pytest.raises(InputError, match="not an image"):
        signatures.add_images([tmp_path / "bad.png"], "x")


def test_cut_out_of_a_pdf(tmp_path):
    src = pdf_with_signature(tmp_path / "signed.pdf")
    sig = signatures.add_pdf(src, 0, (300, 450, 480, 580), "From PDF")
    a, pix = alpha(sig.files[0])
    assert pix.width > 600  # 600 dpi: sharp when printed
    assert sig.files[0].suffix == ".png"  # a picture of the box only, nothing else of the page comes along
    with pytest.raises(InputError, match="Nothing is drawn"):
        signatures.add_pdf(src, 0, (300, 700, 480, 800), "empty")


def images_on(page):
    return [page.get_image_bbox(img) for img in page.get_images(full=True)]


def rows(n):
    return [{"day": f"2026-11-{2 + k:02d}", "start": "09:00", "end": "13:00", "signed": "yes"} for k in range(n)]


def test_signed_timesheet(ts, tmp_path):
    signatures.add_images([scribble(tmp_path / "a.png"), scribble(tmp_path / "b.png", 20)], "Me")
    ctx = engine.build_context(ts, PROFILE, {"month": "Nov", "work": rows(3) + [
        {"day": "2026-11-09", "start": "09:00", "end": "13:00", "signed": ""}]})
    assert isinstance(ctx.values["my_signature"], signatures.Signature)  # profile default: yes
    out = engine.generate(ctx, tmp_path / "out")[0].path
    with pymupdf.open(out) as pdf:
        boxes = [pdf[0].get_image_bbox(i) for i in pdf[0].get_images(full=True)]
        xrefs = {i[0] for i in pdf[0].get_images(full=True)}
    cells = [b for b in boxes if b.y0 < 660]
    assert len(cells) == 3  # 3 signed rows, the 4th left blank
    for k, b in enumerate(sorted(cells, key=lambda r: r.y0)):  # inside the Signature cell of its row
        assert 470 <= b.x0 and b.x1 <= 555 and 214 + 22 * k <= b.y0 and b.y1 <= 236 + 22 * k
    assert any(b.y0 >= 748 and b.y1 <= 780 for b in boxes)  # the signature line at the bottom
    assert len(xrefs) == 2  # the two variants take turns


def test_unknown_signature(ts):
    with pytest.raises(InputError, match="add your signature first"):
        engine.build_context(ts, {**PROFILE, "my_signature": "someone"}, {"month": "N", "work": rows(1)})


def test_yes_without_signatures_leaves_it_blank(ts):
    ctx = engine.build_context(ts, PROFILE, {"month": "N", "work": rows(1)})
    assert ctx.values["my_signature"] == "" and ctx.values["work"][0]["signed"] == ""


def test_export_never_has_signatures(ts, tmp_path):
    signatures.add_images([scribble(tmp_path / "a.png")], "Me")
    z = export_pack(ts, tmp_path / "ts.zip")
    names = zipfile.ZipFile(z).namelist()
    assert not any(n.endswith(".png") or "signature" in n.lower() for n in names)


@pytest.mark.parametrize("entry, message", [
    ({"sign": "month", "box": [1, 2, 3, 4]}, "not a signature field"),
    ({"sign": "my_signature", "box": [10, 10, 5, 20]}, "x1 > x0"),
    ({"sign": "my_signature", "box": [1, 2]}, "list of 4 numbers"),
    ({"sign": "my_signature", "box": [1, 2, 3, 4], "when": "month =="}, "when"),
])
def test_bad_sign_entries(ts, entry, message):
    path = ts.root / "pack.yaml"
    spec = yaml.safe_load(path.read_text())
    spec["documents"][0]["fill"].append(entry)
    path.write_text(yaml.safe_dump(spec, sort_keys=False))
    with pytest.raises(PackError, match=message):
        load_pack(ts.root)
