"""What the fill handle puts in the cells it is dragged over (no Qt here, so it is easy to test).

- Dates continue: one row per day, or the same number of rows per day as the selection has
  (Mon, Mon → Tue, Tue, …). Working days are skipped over weekends and holidays when the
  selection only has working days.
- Numbers continue their step (1, 2 → 3, 4); a single number is repeated.
- Anything else (times, text, choices) repeats the selection as a pattern
  (09:00, 14:00 → 09:00, 14:00, …).
"""
import datetime as dt

from .engine import parse_number
from .templating import is_working_day, parse_date


def _next_day(day, working, country):
    day += dt.timedelta(days=1)
    while working and not is_working_day(day, country):
        day += dt.timedelta(days=1)
    return day


def _dates(values, n, country):
    days = [parse_date(v) for v in values]
    groups = []  # runs of the same day
    for d in days:
        if groups and groups[-1][0] == d:
            groups[-1][1] += 1
        else:
            groups.append([d, 1])
    per_day = groups[0][1]
    working = all(is_working_day(d, country) for d in days)
    steady = all(size == per_day for _d, size in groups[:-1]) and groups[-1][1] <= per_day
    one_by_one = all(b[0] == _next_day(a[0], working, country) for a, b in zip(groups, groups[1:]))
    if not (steady and one_by_one):  # some other pattern: keep the step between the last two days
        step = days[-1] - days[-2] if len(days) > 1 else dt.timedelta(days=1)
        if step == dt.timedelta(0):
            step = dt.timedelta(days=1)
        return [(days[-1] + step * (k + 1)).isoformat() for k in range(n)]
    out, day, used = [], groups[-1][0], groups[-1][1]
    for _ in range(n):
        if used == per_day:
            day, used = _next_day(day, working, country), 0
        out.append(day.isoformat())
        used += 1
    return out


def extend(values, n, kind="text", country=""):
    """The next n values after `values` (the selected cells, top to bottom) for a column of `kind`."""
    values = [v for v in values if v not in (None, "")]
    if not values or n <= 0:
        return [""] * max(n, 0)
    if kind == "date":
        try:
            return _dates(values, n, country)
        except ValueError:
            pass
    if kind == "number":
        nums = [parse_number(v) for v in values]
        if len(nums) > 1 and None not in nums:
            step = nums[-1] - nums[-2]
            out = [nums[-1] + step * (k + 1) for k in range(n)]
            return [int(x) if float(x).is_integer() else round(x, 6) for x in out]
    return [values[k % len(values)] for k in range(n)]
