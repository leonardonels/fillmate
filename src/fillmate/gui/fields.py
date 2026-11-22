"""Input widgets generated from a pack's field definitions."""
import datetime as dt

from PySide6.QtCore import QDate, QSize, Qt, QTime
from PySide6.QtWidgets import (
    QComboBox, QDateEdit, QHBoxLayout, QLineEdit, QPlainTextEdit, QPushButton, QTimeEdit,
    QToolButton, QVBoxLayout, QWidget,
)

from ..templating import parse_date
from . import ink
from .ink import CircleChoice, TickChoice, icon, label, set_role

TICK_MAX = 4  # choices with more options than this stay a drop-down

STRFTIME_TO_QT = {"%d": "dd", "%m": "MM", "%Y": "yyyy", "%y": "yy", "%b": "MMM", "%B": "MMMM",
                  "%a": "ddd", "%A": "dddd", "%e": "d"}


def qt_date_format(fmt):
    out, i = "", 0
    while i < len(fmt):
        token = fmt[i:i + 2]
        if token in STRFTIME_TO_QT:
            out += STRFTIME_TO_QT[token]
            i += 2
        elif fmt[i] == "%":
            return "yyyy-MM-dd"  # unsupported directive: fall back to ISO
        else:
            out += f"'{fmt[i]}'" if fmt[i].isalpha() else fmt[i]
            i += 1
    return out or "yyyy-MM-dd"


def _to_date(value):
    if value in (None, "", "today"):
        return dt.date.today()
    try:
        return parse_date(value)
    except ValueError:
        return dt.date.today()


def date_edit(value, display):
    w = QDateEdit(QDate(_to_date(value)))
    w.setDisplayFormat(display)
    w.setCalendarPopup(True)
    w.calendarWidget().setFirstDayOfWeek(Qt.Monday)
    return w


class DateList(QWidget):
    """One or more days, with add and remove buttons."""

    def __init__(self, display):
        super().__init__()
        self.display = display
        self.rows = QVBoxLayout(self)
        self.rows.setContentsMargins(0, 0, 0, 0)
        self.rows.setSpacing(2)
        self.add_btn = set_role(QPushButton("+ Add day"), "add")
        self.add_btn.clicked.connect(lambda: self.add(self.dates()[-1] + dt.timedelta(days=1)))
        self.rows.addWidget(self.add_btn, 0, Qt.AlignLeft)
        self.add()

    def add(self, value=None):
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(date_edit(value, self.display), 1)
        rm = QToolButton(toolTip="Remove day")
        rm.setIcon(icon("close", None, 14, 2))
        rm.setIconSize(QSize(14, 14))
        rm.setAccessibleName("Remove day")
        rm.clicked.connect(lambda: self._remove(row))
        lay.addWidget(rm)
        self.rows.insertWidget(self.rows.count() - 1, row)

    def _remove(self, row):
        if self.rows.count() > 2:
            self.rows.removeWidget(row)
            ink.discard(row)

    def dates(self):
        return [self.rows.itemAt(i).widget().findChild(QDateEdit).date().toPython()
                for i in range(self.rows.count() - 1)]

    def set_dates(self, values):
        values = [_to_date(v) for v in (values or [])] or [dt.date.today()]
        while self.rows.count() > 1:
            w = self.rows.takeAt(0).widget()
            ink.discard(w)
        for v in values:
            self.add(v)


class FieldInput:
    """A widget for one pack field, with get/set of its value."""

    def __init__(self, f, date_format, pack=None):
        self.field = f
        display = qt_date_format(date_format)
        t = f.type
        if t == "table":
            from .table import TableInput  # imports this module: loaded only when a pack has a table
            self.widget = TableInput(f, pack)
        elif t == "signature":
            from .signatures_ui import SignaturePicker
            self.widget = SignaturePicker()
            self.widget.setAccessibleName(f.label)
        elif t == "dates":
            self.widget = DateList(display)
        elif t == "date":
            self.widget = date_edit(f.default, display)
        elif t == "time":
            self.widget = QTimeEdit()
            self.widget.setDisplayFormat("HH:mm")
        elif t == "bool":
            self.widget = CircleChoice()  # a yes/no that ends up on the form: circle one
            self.widget.setAccessibleName(f.label)
        elif t == "choice" and len(f.options) <= TICK_MAX:
            self.widget = TickChoice()  # becomes tick boxes on the form
            self.widget.setAccessibleName(f.label)
            self.widget.addItems(f.options)
        elif t == "choice":
            self.widget = QComboBox()
            self.widget.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            self.widget.addItems(f.options)
        elif t == "multiline":
            self.widget = QPlainTextEdit()
            self.widget.setFixedHeight(64)
            self.widget.setPlaceholderText(f.placeholder)
            self.widget.setTabChangesFocus(True)
        else:
            self.widget = QLineEdit()
            self.widget.setPlaceholderText(f.placeholder)
        if f.help:
            self.widget.setToolTip(f.help)
        self.set(f.default)

    @property
    def row_label(self):
        return self.field.label + (" *" if self.field.required else "")

    def get(self):
        w, t = self.widget, self.field.type
        if t in ("table", "signature"):
            return w.get()
        if t == "dates":
            return w.dates()
        if t == "date":
            return w.date().toPython()
        if t == "time":
            return w.time().toString("HH:mm")
        if t == "bool":
            return w.isChecked()
        if t == "choice":
            return w.currentText()
        if t == "multiline":
            return w.toPlainText().strip()
        return w.text().strip()

    def set(self, value):
        w, t = self.widget, self.field.type
        if t in ("table", "signature"):
            w.set(value)
        elif t == "dates":
            if value:
                w.set_dates(value if isinstance(value, list) else str(value).split(","))
        elif t == "date":
            w.setDate(QDate(_to_date(value)))
        elif t == "time":
            text = str(value or "00:00")
            try:
                h, m = (int(x) for x in text.split(":")[:2])
            except ValueError:
                h, m = 0, 0
            w.setTime(QTime(h, m))
        elif t == "bool":
            w.setChecked(value is True or str(value).lower() in ("1", "true", "yes", "on"))
        elif t == "choice":
            if value is not None and w.findText(str(value)) >= 0:
                w.setCurrentText(str(value))
        elif t == "multiline":
            w.setPlainText("" if value is None else str(value))
        else:
            w.setText("" if value is None else str(value))
            w.setCursorPosition(0)


def build_form(fields, date_format, pack=None):
    """The fields as rows (small label above the value; yes/no beside it) and their FieldInputs."""
    box = QWidget()
    lay = QVBoxLayout(box)
    lay.setContentsMargins(16, 8, 16, 12)
    lay.setSpacing(12)
    inputs = {}
    for f in fields:
        fi = FieldInput(f, date_format, pack)
        name = label(fi.row_label)  # plain text: labels come from the pack
        if f.type == "bool":
            name.setStyleSheet("font-size: 15px;")
            name.setWordWrap(True)
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            h.addWidget(name, 1)
            h.addWidget(fi.widget, 0)
            fi.widget.setFixedWidth(120)
            lay.addWidget(row)
        else:
            name.setProperty("role", "lbl")
            name.setWordWrap(True)
            row = QWidget()
            v = QVBoxLayout(row)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(2)
            v.addWidget(name)
            v.addWidget(fi.widget)
            lay.addWidget(row)
        name.setBuddy(fi.widget)
        inputs[f.id] = fi
    return box, inputs
