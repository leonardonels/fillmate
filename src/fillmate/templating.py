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

    env.filters.update(date=date_filter, dates=dates_filter, slug=slug, first_word=first_word)
    env.globals.update(
        today=dt.date.today(),
        working_days_between=lambda a, b: working_days_between(a, b, country),
        is_working_day=lambda d: is_working_day(parse_date(d), country),
    )
    return env
