import datetime as dt
import shutil

import pymupdf
import pytest
import yaml

from fillmate import InputError, PackError, cli, engine, store
from fillmate.pack import load_pack
from fillmate.paths import examples_dir
from fillmate.templating import Duration, hours_between, make_env

PROFILE = {"full_name": "Jane Doe", "manager_email": "boss@example.com"}


@pytest.fixture
def ts_dir(tmp_path):
    dest = tmp_path / "timesheet"
    shutil.copytree(examples_dir() / "timesheet", dest)
    return dest


@pytest.fixture
def ts(ts_dir):
    return load_pack(ts_dir)


def rows(n, start=dt.date(2026, 11, 2)):
    """Two blocks a day: 09:00–13:00 and 14:00–18:30 (8:30 a day)."""
    out = []
    for k in range(n):
        day = start + dt.timedelta(days=k // 2)
        out.append({"day": day.isoformat(), "start": "9" if k % 2 == 0 else "14:00",
                    "end": "13" if k % 2 == 0 else "18.30", "activity": "Work"})
    return out


def edit_spec(root, change):
    path = root / "pack.yaml"
    spec = yaml.safe_load(path.read_text())
    change(spec)
    path.write_text(yaml.safe_dump(spec, sort_keys=False))


def table_entry(spec):
    return next(e for e in spec["documents"][0]["fill"] if "table" in e)


# --- durations -------------------------------------------------------------

def test_duration_arithmetic_and_format():
    a, b = Duration.parse("4:30"), Duration.parse(3.5)
    assert str(a + b) == "8:00" and str(a - b) == "1:00" and str(b - a) == "-1:00"
    assert sum([a, b]) == Duration(480)
    assert str(Duration.parse("4,25")) == "4:15"
    assert a > b and a + 0 == a and 0 + a == a
    assert str(hours_between("22:00", "02:30")) == "4:30"  # past midnight
    assert hours_between("", "13:00") == ""
    env = make_env()
    assert env.from_string("{{ d | hours }}").render(d=Duration.parse("4:30")) == "4,5"
    assert env.from_string("{{ d | hours(1, '.') }}").render(d=Duration.parse("1:20")) == "1.3"
    assert env.from_string("{{ rows | total('h') }}").render(
        rows=[{"h": Duration(60)}, {"h": ""}, {"h": Duration(30)}]) == "1:30"
    assert env.from_string("{{ rows | total('n') }}").render(rows=[{"n": 2}, {"n": "1,5"}]) == "3.5"
    assert env.from_string("{{ rows | days('d') }}").render(rows=[{"d": 1}, {"d": 1}, {"d": 2}]) == "2"


# --- loading ---------------------------------------------------------------

def test_example_loads(ts):
    work = ts.field("work")
    assert work.type == "table" and [c.id for c in work.columns] == ["day", "start", "end", "hours", "activity"]
    assert work.column("hours").formula
    entry = next(e for e in ts.documents[0].fill if "table" in e)
    assert len(entry["ys"]) == 21 and entry["cols"][-1] is None and entry["overflow"] == "repeat_page"


@pytest.mark.parametrize("change, message", [
    (lambda s: table_entry(s).update(cols=["day", "start"]), "one entry per grid column"),
    (lambda s: table_entry(s).update(cols=["day", "start", "end", "hours", "nope", None]), "nope not a column"),
    (lambda s: table_entry(s).update(xs=[40, 30, 160, 210, 260, 470, 555]), "increasing"),
    (lambda s: table_entry(s).update(table="month"), "not a table field"),
    (lambda s: table_entry(s).update(align=["middle"] * 6), "left/center/right"),
    (lambda s: s["documents"][0]["fill"].append(dict(table_entry(s))), "only one table per document"),
    (lambda s: s["fields"][2].update(columns=[]), "at least one column"),
    (lambda s: s["fields"][2]["columns"][3].update(formula="hours_between(start,"), "formula"),
    (lambda s: s["fields"][0].update(columns=[{"id": "x"}]), "only table fields have columns"),
    (lambda s: s["profile"].append({"id": "t", "type": "table", "columns": [{"id": "x"}]}), "belong in fields"),
])
def test_invalid_tables(ts_dir, change, message):
    edit_spec(ts_dir, change)
    with pytest.raises(PackError, match=message):
        load_pack(ts_dir)


# --- values ----------------------------------------------------------------

def test_rows_are_typed_and_computed(ts):
    given = rows(3) + [{"day": "", "start": "", "end": "", "activity": ""}]
    ctx = engine.build_context(ts, PROFILE, {"month": "Nov", "work": given})
    work = ctx.values["work"]
    assert len(work) == 3  # the empty row is dropped
    assert work[0]["day"] == dt.date(2026, 11, 2) and work[0]["start"] == "09:00" and work[1]["end"] == "18:30"
    assert [str(r["hours"]) for r in work] == ["4:00", "4:30", "4:00"]
    assert ctx.values["total_hours"] == "12:30"


def test_half_filled_row_stays_blank(ts):
    ctx = engine.build_context(ts, PROFILE, {"month": "Nov", "work": [{"day": "2026-11-02", "start": "09:00"}]})
    assert ctx.values["work"][0]["hours"] == ""


def test_row_errors_name_the_row(ts):
    bad = rows(2)
    bad[1]["end"] = "25:00"
    with pytest.raises(InputError, match="row 2: To: '25:00' is not a time"):
        engine.build_context(ts, PROFILE, {"month": "Nov", "work": bad})
    with pytest.raises(InputError, match="Hours worked is required"):
        engine.build_context(ts, PROFILE, {"month": "Nov", "work": []})


def test_formula_error_on_complete_row(ts_dir):
    edit_spec(ts_dir, lambda s: s["fields"][2]["columns"][3].update(formula="start / 2"))
    with pytest.raises(PackError, match="row 1: the formula of Hours failed"):
        engine.build_context(load_pack(ts_dir), PROFILE, {"month": "Nov", "work": rows(1)})


# --- PDF -------------------------------------------------------------------

def test_one_sheet(ts, tmp_path):
    ctx = engine.build_context(ts, PROFILE, {"month": "November 2026", "work": rows(4)})
    out = engine.generate(ctx, tmp_path)[0].path
    with pymupdf.open(out) as pdf:
        assert len(pdf) == 1
        text = pdf[0].get_text()
    for expected in ("02/11", "03/11", "09:00", "18:30", "4:30", "17:00", "1 of 1", "Jane Doe"):
        assert expected in text


def test_overflow_repeats_the_page(ts, tmp_path):
    ctx = engine.build_context(ts, PROFILE, {"month": "November 2026", "work": rows(25)})
    out = engine.generate(ctx, tmp_path)[0].path
    with pymupdf.open(out) as pdf:
        assert len(pdf) == 2
        first, second = pdf[0].get_text(), pdf[1].get_text()
    assert "85:00" in first and "1 of 2" in first and "02/11" in first and "12/11" not in first
    assert "21:00" in second and "2 of 2" in second and "12/11" in second and "02/11" not in second
    assert "Jane Doe" in second  # the rest of the page is repeated too


def test_overflow_error(ts_dir, tmp_path):
    edit_spec(ts_dir, lambda s: table_entry(s).update(overflow="error"))
    pack = load_pack(ts_dir)
    ctx = engine.build_context(pack, PROFILE, {"month": "Nov", "work": rows(21)})
    with pytest.raises(InputError, match="21 rows, but the page only has room for 20"):
        engine.generate(ctx, tmp_path)


def test_long_cell_shrinks_and_fails(ts, tmp_path):
    long = rows(1)
    long[0]["activity"] = "Meeting " * 6
    ctx = engine.build_context(ts, PROFILE, {"month": "Nov", "work": long})
    engine.generate(ctx, tmp_path)
    long[0]["activity"] = "Meeting " * 60
    ctx = engine.build_context(ts, PROFILE, {"month": "Nov", "work": long})
    with pytest.raises(InputError, match="row 1, Activity"):
        engine.generate(ctx, tmp_path)


# --- CSV, CLI and drafts ------------------------------------------------------

def test_rows_from_csv(ts):
    text = "Date;From;To;Activity;Other\n02/11/2026;9:00;13:00;Report;x\n\n"
    assert engine.rows_from_csv(ts.field("work"), text) == [
        {"day": "02/11/2026", "start": "9:00", "end": "13:00", "activity": "Report"}]
    with pytest.raises(InputError, match="names none of the columns"):
        engine.rows_from_csv(ts.field("work"), "a,b\n1,2\n")


def test_cli_fill_with_csv_and_draft(ts_dir, tmp_path):
    store.save_profile(PROFILE)
    csv_file = tmp_path / "rows.csv"
    csv_file.write_text("day,start,end\n2026-11-02,09:00,12:00\n")
    out = tmp_path / "out"
    cli.main(["fill", str(ts_dir), "--set", "month=Nov", "--set", f"work=@{csv_file}", "--no-mail",
              "--out", str(out)])
    with pymupdf.open(next(out.glob("*.pdf"))) as pdf:
        assert "3:00" in pdf[0].get_text()
    store.save_draft("timesheet", {"work": [{"day": dt.date(2026, 11, 3), "start": "08:00", "end": "10:00"}]})
    out2 = tmp_path / "out2"
    cli.main(["fill", str(ts_dir), "--set", "month=Nov", "--no-mail", "--out", str(out2)])
    with pymupdf.open(next(out2.glob("*.pdf"))) as pdf:
        assert "03/11" in pdf[0].get_text() and "2:00" in pdf[0].get_text()


def test_draft_round_trip():
    store.save_draft("p", {"work": [{"day": dt.date(2026, 11, 2), "start": "09:00"}], "empty": []})
    assert store.load_draft("p") == {"work": [{"day": "2026-11-02", "start": "09:00"}]}
    store.save_draft("p", {"work": []})
    assert store.load_draft("p") == {}


# --- authoring (calibration) ------------------------------------------------

def test_detect_grid_on_example():
    from fillmate.authoring import detect_grid
    template = examples_dir() / "timesheet" / "timesheet.pdf"
    grid = detect_grid(template, 0, (30, 180, 560, 660))
    assert grid["xs"] == [40, 110, 160, 210, 260, 470, 555]
    assert grid["ys"][0] == 214 and len(grid["ys"]) == 21  # the header row is left out
    assert grid["headers"] == ["Date", "From", "To", "Hours", "Activity", "Signature"]
    assert detect_grid(template, 0, at=(300, 400))["ys"] == grid["ys"]
    assert detect_grid(template, 0, (30, 20, 560, 170)) is None  # no grid there
    assert detect_grid(template, 0, at=(300, 100)) is None


def test_guess_columns():
    from fillmate.authoring import guess_columns
    columns, cols = guess_columns(["Data", "dalle", "alle", "ore effettive", "Argomento trattato in Azienda",
                                   "Firma Studente"])
    assert cols == ["data", "dalle", "alle", "ore_effettive", "argomento_trattato_in", None]
    kinds = {c["id"]: c.get("formula") or c["type"] for c in columns}
    assert kinds == {"data": "date", "dalle": "time", "alle": "time", "ore_effettive": "hours_between(dalle, alle)",
                     "argomento_trattato_in": "text"}
    columns, cols = guess_columns(["In", "Out", "Total"])
    assert cols[0] != "in" and columns[2]["formula"] == f"hours_between({cols[0]}, {cols[1]})"
    columns, _ = guess_columns(["Qty", "Hours"])
    assert columns[1]["type"] == "number"  # no times to compute it from


def test_save_new_table_from_calibration(tmp_path):
    from fillmate.authoring import create_pack_from_pdf, detect_grid, guess_columns, save_positions
    pack = create_pack_from_pdf(examples_dir() / "timesheet" / "timesheet.pdf", tmp_path / "packs", "TS")
    grid = detect_grid(pack.documents[0].template, 0, (30, 180, 560, 660))
    columns, cols = guess_columns(grid["headers"])
    entry = {"table": "work", "page": 0, "xs": grid["xs"], "ys": grid["ys"], "cols": cols, "align": [],
             "overflow": "repeat_page", "font": "helv", "size": 9.0, "min_size": 6.0}
    total = {"value": "{{ sheet.rows | total('hours') }}", "page": 0, "lines": [[337, 682, 395]],
             "font": "helv", "size": 10, "min_size": 7}
    pack = save_positions(pack.root, "form", [entry, total],
                          [{"id": "work", "label": "Work", "type": "table", "section": "fields"}], {"work": columns})
    work = pack.field("work")
    assert [c.id for c in work.columns] == ["date", "from", "to", "hours", "activity"]
    text = (pack.root / "pack.yaml").read_text()
    assert "- {id: hours, label: Hours, formula: 'hours_between(from, to)'}" in text
    # editing the columns of an existing table later
    columns[4]["label"] = "What I did"
    pack = save_positions(pack.root, "form", [entry, total], [], {"work": columns})
    assert pack.field("work").column("activity").label == "What I did"
    ctx = engine.build_context(pack, {}, {"work": [{"date": "2026-11-02", "from": "9", "to": "12"}]})
    out = engine.generate(ctx, tmp_path / "out")[0].path
    with pymupdf.open(out) as pdf:
        assert "3:00" in pdf[0].get_text()
