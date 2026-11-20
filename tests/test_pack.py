import zipfile

import pytest
import yaml

from fillmate import PackError
from fillmate.pack import export_pack, import_pack, list_packs, load_pack, remove_pack


def edit_spec(folder, change):
    path = folder / "pack.yaml"
    spec = yaml.safe_load(path.read_text())
    change(spec)
    path.write_text(yaml.safe_dump(spec, allow_unicode=True))


def test_example_loads(example):
    assert example.id == "travel-request"
    assert [d.kind for d in example.documents] == ["pdf", "docx"]
    assert example.field("transport").options == ["Train", "Car", "Plane"]


@pytest.mark.parametrize("change, message", [
    (lambda s: s.update(fillmate=2), "fillmate: 1"),
    (lambda s: s.update(id="Bad Id"), "lowercase"),
    (lambda s: s["fields"].append({"id": "dates"}), "used twice"),
    (lambda s: s["fields"].append({"id": "x", "type": "colour"}), "not one of"),
    (lambda s: s["documents"][0].update(template="../outside.pdf"), "outside the pack"),
    (lambda s: s["documents"][0].update(template="missing.pdf"), "not found"),
    (lambda s: s["documents"][0]["fill"].append({"value": "{{ unclosed"}), "fill"),
    (lambda s: s["rules"].append({"warn_if": "x", "error_unless": "y", "message": "m"}), "exactly one"),
    (lambda s: s["presets"].update(bad={"nope": 1}), "unknown fields"),
    (lambda s: s["documents"][0]["fill"].append({"value": "x"}), "needs 'lines'"),
])
def test_invalid_specs_are_rejected(example_dir, change, message):
    edit_spec(example_dir, change)
    with pytest.raises(PackError, match=message):
        load_pack(example_dir)


def test_import_export_roundtrip(example, tmp_path):
    packs_dir = tmp_path / "packs"
    zip_path = export_pack(example, tmp_path / "shared.zip")
    installed = import_pack(zip_path, packs_dir)
    assert installed.root == packs_dir / "travel-request"
    with pytest.raises(PackError, match="already installed"):
        import_pack(zip_path, packs_dir)
    import_pack(zip_path, packs_dir, replace=True)
    packs, problems = list_packs(packs_dir)
    assert [p.id for p in packs] == ["travel-request"] and not problems
    remove_pack(packs[0], packs_dir)
    assert list_packs(packs_dir) == ([], [])


def test_import_rejects_zip_slip(tmp_path):
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as z:
        z.writestr("pack.yaml", "fillmate: 1\n")
        z.writestr("../../escaped.txt", "x")
    with pytest.raises(PackError, match="unsafe path"):
        import_pack(evil, tmp_path / "packs")
    assert not (tmp_path / "escaped.txt").exists()


def test_broken_pack_is_reported_not_fatal(example, tmp_path):
    packs_dir = tmp_path / "packs"
    import_pack(example.root, packs_dir)
    (packs_dir / "broken").mkdir()
    (packs_dir / "broken" / "pack.yaml").write_text("fillmate: 1\nid: broken\n")
    packs, problems = list_packs(packs_dir)
    assert [p.id for p in packs] == ["travel-request"]
    assert problems[0][0].name == "broken"


def test_templates_are_sandboxed(example_dir, example, profile):
    from fillmate import engine

    edit_spec(example_dir, lambda s: s["computed"].update(evil="{{ ''.__class__.__mro__ }}"))
    pack = load_pack(example_dir)
    with pytest.raises(PackError):
        engine.build_context(pack, profile, {"dates": ["2030-01-07"]})
