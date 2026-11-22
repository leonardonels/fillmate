import datetime as dt
import os
import shutil

import pymupdf
import pytest
import yaml

from fillmate import FillmateError, cli, engine, fills, store
from fillmate.pack import load_pack
from fillmate.paths import examples_dir

PROFILE = {"full_name": "Jane Doe", "manager_email": "boss@example.com"}


@pytest.fixture
def ts_dir(tmp_path):
    dest = tmp_path / "timesheet"
    shutil.copytree(examples_dir() / "timesheet", dest)
    return dest


@pytest.fixture
def ts(ts_dir):
    return load_pack(ts_dir)


def work(n=2):
    return [{"day": dt.date(2026, 11, 2 + k), "start": "09:00", "end": "13:00", "activity": "Report"}
            for k in range(n)]


def test_round_trip(ts, tmp_path):
    path = fills.save_fill(tmp_path / "november", ts, {"month": "November 2026", "work": work(),
                                                       "full_name": "not saved: profile"}, "office")
    assert path.name == "november.fillmate"
    data = yaml.safe_load(path.read_text())
    assert data["pack"] == "timesheet" and data["preset"] == "office" and "full_name" not in data["values"]
    loaded = fills.load_fill(path)
    values = fills.for_pack(loaded, ts)
    assert values["work"][0] == {"day": "2026-11-02", "start": "09:00", "end": "13:00", "activity": "Report"}
    ctx = engine.build_context(ts, PROFILE, values)
    assert ctx.values["total_hours"] == "8:00"


def test_wrong_pack_and_bad_files(ts, example, tmp_path):
    path = fills.save_fill(tmp_path / "x.fillmate", ts, {"month": "N"})
    with pytest.raises(FillmateError, match="filled with the pack 'timesheet'"):
        fills.for_pack(fills.load_fill(path), example)
    (tmp_path / "bad.fillmate").write_text("just: text\n")
    with pytest.raises(FillmateError, match="not a saved fillmate form"):
        fills.load_fill(tmp_path / "bad.fillmate")
    (tmp_path / "new.fillmate").write_text("fillmate_fill: 9\nvalues: {}\n")
    with pytest.raises(FillmateError, match="newer fillmate"):
        fills.load_fill(tmp_path / "new.fillmate")


def test_recent_fills(ts, example, tmp_path):
    base = tmp_path / "out"
    old = fills.save_fill(base / "October-2026" / "timesheet.fillmate", ts, {"month": "October 2026"})
    new = fills.save_fill(base / "November-2026" / "timesheet.fillmate", ts, {"month": "November 2026"})
    fills.save_fill(base / "Leeds" / "travel-request.fillmate", example, {"destination": "Leeds"})
    os.utime(old, (1_000_000, 1_000_000))
    recent = fills.recent_fills(base, "timesheet")
    assert [r[0] for r in recent] == [new, old] and recent[0][1] == "November-2026"
    assert len(fills.recent_fills(base)) == 3 and fills.recent_fills(tmp_path / "none") == []


def test_csv_round_trip(ts):
    f = ts.field("work")
    ctx = engine.build_context(ts, PROFILE, {"month": "N", "work": work(2)})
    text = engine.rows_to_csv(f, ctx.values["work"], ts.date_format)
    lines = text.splitlines()
    assert lines[0] == "Date,From,To,Hours,Activity,Signature"
    assert lines[1] == "02/11/2026,09:00,13:00,4:00,Report,"
    back = engine.build_context(ts, PROFILE, {"month": "N", "work": engine.rows_from_csv(f, text)})
    assert back.values["work"] == ctx.values["work"]


def test_cli_saves_and_reopens(ts_dir, tmp_path, capsys):
    store.save_profile(PROFILE)
    store.save_draft("timesheet", {"work": [{"day": "2026-11-02", "start": "09:00", "end": "17:00"}]})
    out = tmp_path / "out"
    cli.main(["fill", str(ts_dir), "--set", "month=November 2026", "--no-mail", "--out", str(out)])
    saved = out / "timesheet.fillmate"
    assert saved.is_file() and "--from" in capsys.readouterr().out
    # December, from November's form: same rows, another month
    out2 = tmp_path / "out2"
    cli.main(["fill", str(ts_dir), "--from", str(saved), "--set", "month=December 2026", "--no-mail",
              "--out", str(out2)])
    with pymupdf.open(next(out2.glob("*.pdf"))) as pdf:
        text = pdf[0].get_text()
    assert "December 2026" in text and "8:00" in text
    assert fills.load_fill(out2 / "timesheet.fillmate")["values"]["month"] == "December 2026"
