"""Input widgets generated from a pack's field definitions."""
import datetime as dt

from PySide6.QtCore import QDate, Qt, QTime
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDateEdit, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
    QPushButton, QTimeEdit, QToolButton, QVBoxLayout, QWidget,
)

from ..templating import parse_date

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
        self.add_btn = QPushButton("+ Add day")
        self.add_btn.clicked.connect(lambda: self.add(self.dates()[-1] + dt.timedelta(days=1)))
        self.rows.addWidget(self.add_btn)
        self.add()

    def add(self, value=None):
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(date_edit(value, self.display), 1)
        rm = QToolButton(text="✕", toolTip="Remove day")
        rm.clicked.connect(lambda: self._remove(row))
        lay.addWidget(rm)
        self.rows.insertWidget(self.rows.count() - 1, row)

    def _remove(self, row):
        if self.rows.count() > 2:
            self.rows.removeWidget(row)
            row.deleteLater()

    def dates(self):
        return [self.rows.itemAt(i).widget().findChild(QDateEdit).date().toPython()
                for i in range(self.rows.count() - 1)]

    def set_dates(self, values):
        values = [_to_date(v) for v in (values or [])] or [dt.date.today()]
        while self.rows.count() > 1:
            w = self.rows.takeAt(0).widget()
            w.deleteLater()
        for v in values:
            self.add(v)


class CheckField(QWidget):
    """A checkbox whose label wraps, so long labels never widen the form."""

    def __init__(self, text):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.box = QCheckBox()
        lay.addWidget(self.box, 0, Qt.AlignTop)
        self.label = QLabel(text)
        self.label.setTextFormat(Qt.PlainText)
        self.label.setWordWrap(True)
        self.label.mousePressEvent = lambda _e: self.box.toggle()
        lay.addWidget(self.label, 1)

    def isChecked(self):
        return self.box.isChecked()

    def setChecked(self, on):
        self.box.setChecked(on)


class FieldInput:
    """A widget for one pack field, with get/set of its value."""

    def __init__(self, f, date_format):
        self.field = f
        display = qt_date_format(date_format)
        t = f.type
        if t == "dates":
            self.widget = DateList(display)
        elif t == "date":
            self.widget = date_edit(f.default, display)
        elif t == "time":
            self.widget = QTimeEdit()
            self.widget.setDisplayFormat("HH:mm")
        elif t == "bool":
            self.widget = CheckField(f.label)
        elif t == "choice":
            self.widget = QComboBox()
            self.widget.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            self.widget.addItems(f.options)
        elif t == "multiline":
            self.widget = QPlainTextEdit()
            self.widget.setFixedHeight(70)
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
        if self.field.type == "bool":
            return ""
        return self.field.label + (" *" if self.field.required else "")

    def get(self):
        w, t = self.widget, self.field.type
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
        if t == "dates":
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


def build_form(fields, date_format):
    """A QFormLayout-backed widget and the FieldInputs it contains."""
    box = QWidget()
    form = QFormLayout(box)
    form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
    inputs = {}
    for f in fields:
        fi = FieldInput(f, date_format)
        label = QLabel(fi.row_label)
        label.setTextFormat(Qt.PlainText)  # labels come from the pack
        form.addRow(label, fi.widget)
        inputs[f.id] = fi
    return box, inputs
