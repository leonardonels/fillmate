"""Sandboxed Jinja environment used for every template string in a pack."""
import datetime as dt
import re
import unicodedata
from functools import lru_cache

from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment

DATE_INPUT_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%m/%d/%Y")


def slug(value, max_len=60):
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9-]+", "_", text).strip("_-")[:max_len]


def parse_date(value, extra_formats=()):
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    text = str(value).strip()
    for fmt in (*extra_formats, *DATE_INPUT_FORMATS):
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"not a date: {value!r}")


class Duration:
    """A length of time in whole minutes, written H:MM (4:30). Sums with Jinja's `sum` filter."""

    __slots__ = ("minutes",)

    def __init__(self, minutes=0):
        self.minutes = int(round(minutes))

    @classmethod
    def parse(cls, value):
        """A Duration from another Duration, a number of hours, 'H:MM' or '4,5'."""
        if isinstance(value, Duration):
            return value
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return cls(value * 60)
        text = str(value).strip()
        m = re.match(r"^(-?)(\d+):([0-5]\d)$", text)
        if m:
            minutes = int(m.group(2)) * 60 + int(m.group(3))
            return cls(-minutes if m.group(1) else minutes)
        try:
            return cls(float(text.replace(",", ".")) * 60)
        except ValueError:
            raise ValueError(f"not a duration: {value!r}") from None

    @property
    def hours(self):
        return self.minutes / 60

    def _other(self, other):
        if isinstance(other, Duration):
            return other.minutes
        if other is None or isinstance(other, str) and other == "" or other == 0:
            return 0  # blank cells (half-filled rows) count as nothing
        return NotImplemented

    def __add__(self, other):
        o = self._other(other)
        return NotImplemented if o is NotImplemented else Duration(self.minutes + o)

    __radd__ = __add__

    def __sub__(self, other):
        o = self._other(other)
        return NotImplemented if o is NotImplemented else Duration(self.minutes - o)

    def __mul__(self, k):
        return Duration(self.minutes * k) if isinstance(k, (int, float)) else NotImplemented

    __rmul__ = __mul__

    def __neg__(self):
        return Duration(-self.minutes)

    def __eq__(self, other):
        o = self._other(other)
        return NotImplemented if o is NotImplemented else self.minutes == o

    def __lt__(self, other):
        o = self._other(other)
        return NotImplemented if o is NotImplemented else self.minutes < o

    def __le__(self, other):
        o = self._other(other)
        return NotImplemented if o is NotImplemented else self.minutes <= o

    def __gt__(self, other):
        o = self._other(other)
        return NotImplemented if o is NotImplemented else self.minutes > o

    def __ge__(self, other):
        o = self._other(other)
        return NotImplemented if o is NotImplemented else self.minutes >= o

    def __hash__(self):
        return hash(self.minutes)

    def __bool__(self):
        return self.minutes != 0

    def __str__(self):
        sign, m = ("-", -self.minutes) if self.minutes < 0 else ("", self.minutes)
        return f"{sign}{m // 60}:{m % 60:02d}"

    def __repr__(self):
        return f"Duration({self})"


def _minutes_of(value):
    if isinstance(value, dt.time):
        return value.hour * 60 + value.minute
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", str(value))
    if not m:
        raise ValueError(f"not a time: {value!r}")
    return int(m.group(1)) * 60 + int(m.group(2))


def hours_between(start, end):
    """Time from start to end ('HH:MM'); an end before the start is on the next day.

    Empty when either time is missing, so half-filled rows stay blank instead of failing.
    """
    if start in (None, "") or end in (None, ""):
        return ""
    minutes = _minutes_of(end) - _minutes_of(start)
    return Duration(minutes + 24 * 60 if minutes < 0 else minutes)


def hours_filter(value, decimals=2, sep=","):
    """Decimal hours: 4:30 -> '4,5'. Trailing zeros are dropped."""
    if value in (None, ""):
        return ""
    text = f"{Duration.parse(value).hours:.{decimals}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text.replace(".", sep)


def total_filter(rows, column):
    """Sum of one column over table rows, skipping blank cells: Durations stay Durations."""
    values = [r.get(column) if isinstance(r, dict) else getattr(r, column, None) for r in rows or []]
    values = [v for v in values if v not in (None, "")]
    if not values:
        return ""
    if any(isinstance(v, Duration) for v in values):
        return sum((Duration.parse(v) for v in values), Duration())
    out = sum(float(str(v).replace(",", ".")) for v in values)
    return int(out) if out == int(out) else round(out, 6)


def days_filter(rows, column):
    """How many different days a table's date column covers (two rows on one day count once)."""
    return len({r.get(column) for r in rows or [] if r.get(column) not in (None, "")})


def hhmm_filter(value):
    return "" if value in (None, "") else str(Duration.parse(value))


@lru_cache(maxsize=64)
def _holidays(country, year):
    if not country:
        return frozenset()
    import holidays
    try:
        return frozenset(holidays.country_holidays(country, years=year).keys())
    except NotImplementedError:
        return frozenset()


def is_working_day(day, country=""):
    return day.weekday() < 5 and day not in _holidays(country, day.year)


def working_days_between(start, end, country=""):
    """Working days strictly between start and end (both excluded)."""
    start, end = parse_date(start), parse_date(end)
    n, day = 0, start + dt.timedelta(days=1)
    while day < end:
        n += is_working_day(day, country)
        day += dt.timedelta(days=1)
    return n


def make_env(pack=None):
    date_format = getattr(pack, "date_format", "%Y-%m-%d")
    country = getattr(pack, "country", "")
    env = SandboxedEnvironment(undefined=StrictUndefined, autoescape=False,
                               keep_trailing_newline=False)

    def date_filter(value, fmt=None):
        if value in (None, ""):
            return ""
        return parse_date(value).strftime(fmt or date_format)

    def dates_filter(values, sep=", ", fmt=None):
        return sep.join(date_filter(v, fmt) for v in values or [])

    def first_word(value):
        parts = str(value).split()
        return parts[0] if parts else ""

    env.filters.update(date=date_filter, dates=dates_filter, slug=slug, first_word=first_word,
                       hhmm=hhmm_filter, hours=hours_filter, total=total_filter, days=days_filter)
    env.globals.update(
        today=dt.date.today(),
        hours_between=hours_between,
        duration=Duration.parse,
        working_days_between=lambda a, b: working_days_between(a, b, country),
        is_working_day=lambda d: is_working_day(parse_date(d), country),
    )
    return env
