from fillmate.series import extend


def test_dates_skip_weekends():
    # Thursday 5 Nov 2026 → Fri, then Mon, Tue
    assert extend(["2026-11-05"], 3, "date") == ["2026-11-06", "2026-11-09", "2026-11-10"]


def test_dates_keep_rows_per_day():
    assert extend(["2026-11-05", "2026-11-05"], 4, "date") == [
        "2026-11-06", "2026-11-06", "2026-11-09", "2026-11-09"]
    # a half-finished day is completed first
    assert extend(["2026-11-05", "2026-11-05", "2026-11-06"], 2, "date") == ["2026-11-06", "2026-11-09"]


def test_dates_with_weekends_keep_calendar_days():
    assert extend(["2026-11-07"], 2, "date") == ["2026-11-08", "2026-11-09"]  # starts on a Saturday
    assert extend(["2026-11-02", "2026-11-09"], 2, "date") == ["2026-11-16", "2026-11-23"]  # weekly


def test_holidays_skipped():
    # 24 Dec 2026 is a Thursday; 25 Dec is Christmas, then the weekend, 28 Dec is Boxing Day (substitute)
    assert extend(["2026-12-24"], 1, "date", "GB") == ["2026-12-29"]


def test_numbers_and_patterns():
    assert extend(["1", "2"], 3, "number") == [3, 4, 5]
    assert extend(["2,5"], 2, "number") == ["2,5", "2,5"]
    assert extend(["09:00", "14:00"], 3, "time") == ["09:00", "14:00", "09:00"]
    assert extend(["Report", ""], 2, "text") == ["Report", "Report"]
    assert extend([], 2) == ["", ""]
