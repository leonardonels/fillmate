"""A table field in the form: a small spreadsheet where you only type data.

Formula columns are computed as you type (greyed, read-only). The little square at the
bottom-right of the selection is the fill handle: drag it down to continue dates, numbers
and patterns (see fillmate.series). Ctrl+D fills down, Ctrl+C / Ctrl+V copy and paste
cells (tab-separated, as Excel and Sheets do), Delete clears.
"""
import datetime as dt
import re

from PySide6.QtCore import (
    QAbstractTableModel, QItemSelection, QItemSelectionModel, QModelIndex, QRect, Qt, QTimer, Signal,
)
from PySide6.QtGui import QColor, QGuiApplication, QKeySequence, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QFormLayout, QHBoxLayout, QHeaderView, QMessageBox, QPushButton,
    QSpinBox, QTableView, QVBoxLayout, QWidget,
)

from .. import FillmateError
from ..engine import Renderer, compute_rows, convert, parse_time
from ..series import extend
from ..templating import is_working_day, parse_date, total_filter
from . import frame as frame_mod
from . import ink
from .fields import date_edit, qt_date_format
from .frame import Frame, is_passive
from .ink import T, label, set_role

HANDLE = 7          # px: the fill handle's square
VISIBLE_ROWS = 8    # rows shown in the form before it scrolls


def short_format(fmt):
    """A date format without the year (27/11 for %d/%m/%Y): the form is narrow, the year is on hover."""
    out = re.sub(r"[/.\-\s]*%[Yy]", "", fmt) if re.search(r"%[dm]", fmt) else fmt
    out = re.sub(r"^[/.\-\s]+", "", out)
    return out or fmt


def _date_text(value, fmt):
    try:
        return parse_date(value).strftime(fmt)
    except ValueError:
        return str(value)


class RowsModel(QAbstractTableModel):
    """Rows as typed (dates kept as ISO text), plus one empty row at the end to start a new one."""

    edited = Signal()

    def __init__(self, field, pack):
        super().__init__()
        self.field, self.pack = field, pack
        self.columns = field.columns
        self.rows = []
        self.computed = []   # per row: {column id: value} for formula columns
        self.errors = {}     # (row, column index) -> message
        self.renderer = Renderer(pack)

    # --- Qt model ---

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows) + 1

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.columns)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.ToolTipRole:
            col = self.columns[section]
            return col.label + (f"  (= {col.formula})" if col.formula else "")
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal:
            return self.columns[section].label
        return str(section + 1) if section < len(self.rows) else "+"

    def flags(self, index):
        base = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        return base if self.columns[index.column()].formula else base | Qt.ItemIsEditable

    def data(self, index, role=Qt.DisplayRole):
        r, c = index.row(), index.column()
        col = self.columns[c]
        if role in (Qt.DisplayRole, Qt.EditRole):
            if r >= len(self.rows):
                return ""
            if col.formula:
                value = self.computed[r].get(col.id, "") if r < len(self.computed) else ""
                return "" if value in (None, "") else str(value)
            value = self.rows[r].get(col.id, "")
            if role == Qt.DisplayRole and col.type == "date" and value and (r, c) not in self.errors:
                return _date_text(value, short_format(col.format or self.pack.date_format))
            if role == Qt.EditRole and col.type == "date" and value and (r, c) not in self.errors:
                return _date_text(value, self.pack.date_format)
            return "" if value is None else str(value)
        if role == Qt.ForegroundRole:
            if (r, c) in self.errors:
                return QColor(T["red"])
            return QColor(T["label2"] if col.formula else T["ink"])
        if role == Qt.ToolTipRole:
            if (r, c) in self.errors:
                return self.errors[(r, c)]
            if col.type == "date" and r < len(self.rows) and self.rows[r].get(col.id):
                return _date_text(self.rows[r][col.id], "%A %d %B %Y")
            return f"= {col.formula}" if col.formula else (col.help or None)
        if role == Qt.TextAlignmentRole:
            return int((Qt.AlignLeft if col.type == "text" and not col.formula else Qt.AlignHCenter) | Qt.AlignVCenter)
        return None

    def setData(self, index, value, role=Qt.EditRole):
        if role != Qt.EditRole or not index.isValid():
            return False
        self.set_cells([(index.row(), index.column(), value)])
        return True

    # --- editing ---

    def normalise(self, col, value):
        """What is stored for a typed value: times as HH:MM, dates as ISO; anything unreadable as typed."""
        text = "" if value is None else str(value).strip()
        if not text:
            return ""
        if col.type == "time":
            return parse_time(text) or text
        if col.type == "date":
            return self.read_date(text) or text
        return text

    def read_date(self, text):
        """ISO date from what was typed: the pack's format, ISO, 2/11 (this year) or 2 (this month)."""
        try:
            return parse_date(text, (self.pack.date_format,)).isoformat()
        except ValueError:
            pass
        today = dt.date.today()
        parts = [int(p) for p in re.split(r"[/.\-\s]+", text) if p.isdigit()]
        try:
            if len(parts) == 2:
                day, month = parts if self.pack.date_format.find("%d") < self.pack.date_format.find("%m") else parts[::-1]
                return dt.date(today.year, month, day).isoformat()
            if len(parts) == 1 and re.fullmatch(r"\d{1,2}", text):
                return dt.date(today.year, today.month, parts[0]).isoformat()
        except ValueError:
            pass
        return None

    def new_row(self):
        """An empty row with the columns' defaults (e.g. the usual start and end times)."""
        return {c.id: self.normalise(c, c.default) for c in self.columns if not c.formula and c.default not in (None, "")}

    def set_cells(self, cells):
        """Write (row, column, value) cells; rows past the end are added. One update for all of them."""
        cells = [(r, c, v) for r, c, v in cells if not self.columns[c].formula]
        if not cells:
            return
        last = max(r for r, _c, _v in cells)
        if last >= len(self.rows):
            self.beginInsertRows(QModelIndex(), len(self.rows) + 1, last + 1)
            while len(self.rows) <= last:
                self.rows.append(self.new_row())
            self.endInsertRows()
        for r, c, v in cells:
            col = self.columns[c]
            self.rows[r][col.id] = self.normalise(col, v)
        self.trim()
        self.refresh()

    def blank(self, row):
        """True when a row holds nothing but the columns' defaults."""
        return all(row.get(c.id, "") in ("", self.normalise(c, c.default)) for c in self.columns if not c.formula)

    def trim(self):
        """Drop blank rows at the end (the extra row is always there for a new one)."""
        n = len(self.rows)
        while n and self.blank(self.rows[n - 1]):
            n -= 1
        if n < len(self.rows):
            self.beginRemoveRows(QModelIndex(), n + 1, len(self.rows))
            del self.rows[n:]
            self.endRemoveRows()

    def remove_rows(self, first, count):
        first, last = first, min(first + count, len(self.rows)) - 1
        if last < first:
            return
        self.beginRemoveRows(QModelIndex(), first, last)
        del self.rows[first:last + 1]
        self.endRemoveRows()
        self.refresh()

    def insert_row(self, at):
        at = min(at, len(self.rows))
        self.beginInsertRows(QModelIndex(), at, at)
        self.rows.insert(at, self.new_row())
        self.endInsertRows()
        self.refresh()

    def set_rows(self, rows):
        self.beginResetModel()
        self.rows = [{c.id: self.normalise(c, r.get(c.id)) for c in self.columns if not c.formula}
                     for r in rows or [] if isinstance(r, dict)]
        self.endResetModel()
        self.refresh(emit=False)

    def get_rows(self):
        inputs = [c.id for c in self.columns if not c.formula]
        return [{k: r.get(k, "") for k in inputs} for r in self.rows if not self.blank(r)]

    def refresh(self, emit=True):
        """Check every cell and recompute the formula columns."""
        self.errors, typed = {}, []
        for r, row in enumerate(self.rows):
            out = {}
            for c, col in enumerate(self.columns):
                if col.formula:
                    continue
                value = row.get(col.id, "")
                if value in (None, ""):
                    out[col.id] = ""
                    continue
                try:
                    out[col.id] = convert(col, value, self.pack)
                except FillmateError as e:
                    self.errors[(r, c)] = str(e)
                    out[col.id] = ""
            typed.append(out)
        try:
            self.computed = compute_rows(self.field, typed, self.renderer, strict=False)
        except Exception:
            self.computed = typed
        if self.rows or emit:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.rows), len(self.columns) - 1))
            self.headerDataChanged.emit(Qt.Vertical, 0, len(self.rows))
        if emit:
            self.edited.emit()

    # --- summary ---

    def totals(self):
        """(label, total) for formula and number columns with something in them."""
        out = []
        for col in self.columns:
            if col.formula or col.type == "number":
                total = total_filter(self.computed, col.id)
                if total != "":
                    out.append((col.label, total))
        return out


class TableView(QTableView):
    """The grid, with Excel's fill handle and keyboard shortcuts."""

    def __init__(self, model, parent=None):
        super().__init__(parent)
        self.setModel(model)
        self.drag = None  # (top, bottom, left, right, target row) while the fill handle is dragged
        self.setSelectionMode(QAbstractItemView.ContiguousSelection)
        self.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed
                             | QAbstractItemView.AnyKeyPressed)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setMouseTracking(True)
        self.verticalHeader().setDefaultSectionSize(28)
        self.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.verticalHeader().setMinimumWidth(26)
        h = self.horizontalHeader()
        h.setHighlightSections(False)
        h.setTextElideMode(Qt.ElideRight)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        h.setMinimumSectionSize(36)
        self.setWordWrap(False)
        self.size_columns()

    def size_columns(self):
        """Dates, times and numbers as wide as what goes in them; the first text column takes the rest.
        Headings get their full width only when it all fits (shortened with … otherwise, in full on hover)."""
        h = self.horizontalHeader()
        fm, hm = self.fontMetrics(), h.fontMetrics()
        columns = self.model().columns
        stretch = next((i for i, c in enumerate(columns) if c.type == "text" and not c.formula), None)
        content, full = {}, {}
        for i, col in enumerate(columns):
            if i == stretch:
                continue
            sample = {"date": dt.date(2000, 12, 28).strftime(short_format(col.format or self.model().pack.date_format)),
                      "time": "00:00"}.get(col.type, "00:00")
            content[i] = max(40, fm.horizontalAdvance(sample) + 14)
            full[i] = max(content[i], hm.horizontalAdvance(col.label) + 12)
        room = self.viewport().width() - (110 if stretch is not None else 0)
        widths = full if sum(full.values()) <= room else content
        for i, w in widths.items():
            h.resizeSection(i, w)
        if stretch is not None:
            h.setSectionResizeMode(stretch, QHeaderView.Stretch)
        else:
            h.setStretchLastSection(True)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.size_columns()

    # --- selection ---

    def block(self):
        """(top, bottom, left, right) of the selection, or None."""
        idx = self.selectionModel().selectedIndexes()
        if not idx:
            return None
        rows, cols = [i.row() for i in idx], [i.column() for i in idx]
        return min(rows), max(rows), min(cols), max(cols)

    def handle_rect(self):
        b = self.block()
        if b is None:
            return None
        top, bottom, left, right = b
        rect = self.visualRect(self.model().index(bottom, right))
        if not rect.isValid():
            return None
        return QRect(rect.right() - HANDLE // 2, rect.bottom() - HANDLE // 2, HANDLE, HANDLE)

    # --- painting ---

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self.viewport())
        p.setRenderHint(QPainter.Antialiasing)
        b = self.block()
        if b is not None and not self.state() == QAbstractItemView.EditingState:
            top, bottom, left, right = b
            if self.drag is not None:
                bottom = max(bottom, self.drag[4])
            r = self.visualRect(self.model().index(top, left)).united(
                self.visualRect(self.model().index(min(bottom, self.model().rowCount() - 1), right)))
            extra = bottom - (self.model().rowCount() - 1)
            if extra > 0:  # dragged past the last row: the rows it will add
                r.setBottom(r.bottom() + extra * self.rowHeight(0))
            pen = QPen(QColor(T["ink"]), 2 if self.drag is None else 1.5,
                       Qt.SolidLine if self.drag is None else Qt.DashLine)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawRect(r.adjusted(0, 0, -1, -1))
            handle = self.handle_rect()
            if handle is not None and self.drag is None:
                p.setPen(QPen(QColor(T["paper"]), 1.5))
                p.setBrush(QColor(T["ink"]))
                p.drawRect(handle)
        p.end()

    # --- the fill handle ---

    def mousePressEvent(self, event):
        handle = self.handle_rect()
        if event.button() == Qt.LeftButton and handle is not None and \
                handle.adjusted(-3, -3, 3, 3).contains(event.position().toPoint()):
            top, bottom, left, right = self.block()
            self.drag = (top, bottom, left, right, bottom)
            self.viewport().update()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint()
        if self.drag is not None:
            self.drag = (*self.drag[:4], max(self.row_under(pos.y()), self.drag[1]))
            if pos.y() > self.viewport().height() - 10:
                self.verticalScrollBar().setValue(self.verticalScrollBar().value() + 14)
            self.viewport().update()
            return
        handle = self.handle_rect()
        near = handle is not None and handle.adjusted(-3, -3, 3, 3).contains(pos)
        self.viewport().setCursor(Qt.CrossCursor if near else Qt.ArrowCursor)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.drag is not None:
            top, bottom, left, right, target = self.drag
            self.drag = None
            if target > bottom:
                self.fill(top, bottom, left, right, target)
            self.viewport().update()
            return
        super().mouseReleaseEvent(event)

    def row_under(self, y):
        """The row at viewport height y, counting rows that do not exist yet below the last one."""
        row = self.rowAt(y)
        if row >= 0:
            return row
        last = self.model().rowCount() - 1
        bottom = self.visualRect(self.model().index(last, 0)).bottom()
        if y <= bottom:
            return 0
        return last + 1 + int((y - bottom) // max(1, self.rowHeight(0)))

    def fill(self, top, bottom, left, right, target):
        """Continue the cells top..bottom down to `target`, column by column."""
        model = self.model()
        cells = []
        for c in range(left, right + 1):
            col = model.columns[c]
            if col.formula:
                continue
            source = [model.rows[r].get(col.id, "") if r < len(model.rows) else "" for r in range(top, bottom + 1)]
            for k, v in enumerate(extend(source, target - bottom, col.type, model.pack.country)):
                cells.append((bottom + 1 + k, c, v))
        model.set_cells(cells)
        target = min(target, model.rowCount() - 1)
        self.selectionModel().select(QItemSelection(model.index(top, left), model.index(target, right)),
                                     QItemSelectionModel.ClearAndSelect)

    # --- keyboard ---

    def keyPressEvent(self, event):
        b = self.block()
        model = self.model()
        if event.matches(QKeySequence.Copy) and b:
            top, bottom, left, right = b
            text = "\n".join("\t".join(model.data(model.index(r, c), Qt.DisplayRole) for c in range(left, right + 1))
                             for r in range(top, bottom + 1))
            QGuiApplication.clipboard().setText(text)
            return
        if event.matches(QKeySequence.Paste):
            self.paste(QGuiApplication.clipboard().text())
            return
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace) and b and self.state() != QAbstractItemView.EditingState:
            top, bottom, left, right = b
            model.set_cells([(r, c, "") for r in range(top, min(bottom, len(model.rows) - 1) + 1)
                             for c in range(left, right + 1)])
            return
        if event.key() == Qt.Key_D and event.modifiers() & Qt.ControlModifier:
            self.fill_down()
            return
        super().keyPressEvent(event)

    def paste(self, text):
        model = self.model()
        start = self.currentIndex()
        r0 = start.row() if start.isValid() else len(model.rows)
        c0 = start.column() if start.isValid() else 0
        lines = [line for line in text.replace("\r\n", "\n").rstrip("\n").split("\n")]
        editable = [i for i, col in enumerate(model.columns) if not col.formula and i >= c0]
        cells = []
        for dr, line in enumerate(lines):
            for k, value in enumerate(line.split("\t")):
                if k < len(editable):
                    cells.append((r0 + dr, editable[k], value))
        model.set_cells(cells)

    def contextMenuEvent(self, event):
        model = self.model()
        row = self.rowAt(event.pos().y())
        m = ink.menu(self)
        if 0 <= row < len(model.rows):
            m.addAction("Insert a row above", lambda: model.insert_row(row))
            b = self.block()
            if b and b[0] <= row <= b[1]:
                first, last = b[0], min(b[1], len(model.rows) - 1)
            else:
                first, last = row, row
            n = last - first + 1
            m.addAction("Delete this row" if n == 1 else f"Delete {n} rows", lambda: model.remove_rows(first, n))
        m.addAction("Fill down (Ctrl+D)", self.fill_down)
        m.exec(event.globalPos())

    def fill_down(self):
        """Copy the selection's first row into the rest of it, as Excel's Ctrl+D does."""
        b = self.block()
        model = self.model()
        if not b or b[0] >= len(model.rows):
            return
        top, bottom, left, right = b
        model.set_cells([(r, c, model.rows[top].get(model.columns[c].id, ""))
                         for r in range(top + 1, bottom + 1) for c in range(left, right + 1)])


class PaperDialog(QDialog):
    """A dialog on fillmate's dotted paper, with its title as a heading."""

    def __init__(self, parent, title):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.frame = Frame(self, lambda w, pos: is_passive(w), dialog=True)
        if frame_mod.PLATFORM == "linux":
            self.setStyleSheet("QDialog { background: transparent; }")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        paper = ink.DotGrid()
        paper.radius = self.frame.radius
        outer.addWidget(paper)
        self.body = QVBoxLayout(paper)
        self.body.setContentsMargins(24, 20, 24, 18)
        self.body.setSpacing(12)
        self.body.addWidget(set_role(label(title), "h1"))
        self.edge = None

    def buttons(self, *buttons):
        row = QHBoxLayout()
        row.addStretch()
        for b in buttons:
            b.setMinimumHeight(36)
            row.addWidget(b)
        self.body.addLayout(row)
        self.edge = self.frame.overlay(self)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.edge is not None:
            self.edge.setGeometry(self.rect())
            self.edge.raise_()

    def showEvent(self, event):
        super().showEvent(event)
        self.frame.after_show()


class AddDaysDialog(PaperDialog):
    """One row (or more) per day over a range, with the columns' usual values."""

    def __init__(self, parent, model, start):
        super().__init__(parent, "Add days")
        form = QFormLayout()
        form.setVerticalSpacing(12)
        display = qt_date_format(model.pack.date_format)
        month_end = (start.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)
        self.first = date_edit(start, display)
        self.last = date_edit(month_end, display)
        form.addRow(label("From", "lbl"), self.first)
        form.addRow(label("To", "lbl"), self.last)
        self.per_day = QSpinBox()
        self.per_day.setRange(1, 4)
        self.per_day.setToolTip("e.g. 2 for a morning and an afternoon row")
        form.addRow(label("Rows per day", "lbl"), self.per_day)
        self.working = QCheckBox("Only working days")
        self.working.setChecked(True)
        if model.pack.country:
            self.working.setToolTip(f"Skips weekends and public holidays ({model.pack.country})")
        form.addRow(self.working)
        self.body.addLayout(form)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        ok = set_role(QPushButton("Add"), "ink")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        self.buttons(cancel, ok)
        self.resize(380, 320)

    def days(self, country):
        day, last = self.first.date().toPython(), self.last.date().toPython()
        out = []
        while day <= last:
            if not self.working.isChecked() or is_working_day(day, country):
                out += [day] * self.per_day.value()
            day += dt.timedelta(days=1)
        return out


class ExpandDialog(PaperDialog):
    """The same rows in a large window: edits show up in the form too."""

    def __init__(self, parent, table):
        super().__init__(parent, table.field.label)
        view = TableView(table.model)
        self.body.addWidget(view, 1)
        summary = label(table.summary.text(), "lbl")
        table.model.edited.connect(lambda: summary.setText(table.summary.text()))
        self.body.addWidget(summary)
        done = set_role(QPushButton("Done"), "ink")
        done.clicked.connect(self.accept)
        self.buttons(done)
        self.resize(1000, 700)
        QTimer.singleShot(0, view.setFocus)


class TableInput(QWidget):
    """The widget a table field gets in the form."""

    changed = Signal()

    def __init__(self, field, pack):
        super().__init__()
        self.field, self.pack = field, pack
        self.model = RowsModel(field, pack)
        self.model.edited.connect(self._edited)
        self.model.modelReset.connect(self._update_summary)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 2, 0, 0)
        v.setSpacing(6)
        self.view = TableView(self.model)
        self.view.setAccessibleName(field.label)
        v.addWidget(self.view)
        row = QHBoxLayout()
        row.setSpacing(6)
        date_cols = [c for c in field.columns if c.type == "date" and not c.formula]
        if date_cols:
            add = set_role(QPushButton("+ Add days…"), "add")
            add.clicked.connect(self.add_days)
            row.addWidget(add)
        expand = QPushButton("Expand")
        expand.setToolTip("Edit the table in a larger window")
        expand.clicked.connect(self.expand)
        row.addWidget(expand)
        row.addStretch()
        clear = QPushButton("Clear")
        clear.setToolTip("Remove every row (for a new month)")
        clear.clicked.connect(self.clear)
        row.addWidget(clear)
        v.addLayout(row)
        self.summary = label("", "lbl", wrap=True)
        v.addWidget(self.summary)
        self.capacity = self._capacity()
        self._fit_height()
        self._update_summary()

    def _capacity(self):
        """Rows per sheet of the PDF table that repeats its page (for 'n sheets' in the summary)."""
        for d in self.pack.documents:
            for e in d.fill:
                if e.get("table") == self.field.id and e.get("overflow") == "repeat_page":
                    return len(e["ys"]) - 1
        return None

    def _fit_height(self):
        rows = min(VISIBLE_ROWS, self.model.rowCount())
        h = self.view.horizontalHeader().height() or 28
        self.view.setFixedHeight(h + 28 * max(rows, 3) + 4)

    def _edited(self):
        self._fit_height()
        self._update_summary()
        self.changed.emit()

    def _update_summary(self):
        n = len(self.model.get_rows())
        parts = [f"{n} row{'s' if n != 1 else ''}"]
        if self.capacity and n > self.capacity:
            parts.append(f"{-(-n // self.capacity)} sheets")
        for name, total in self.model.totals():
            parts.append(f"{name} {total}")
        if self.model.errors:
            parts.append(f"{len(self.model.errors)} to fix (in red)")
        self.summary.setText(" · ".join(parts) if n else "Type in the first row, or add days.")

    def get(self):
        return self.model.get_rows()

    def set(self, value):
        self.model.set_rows(value if isinstance(value, list) else [])
        self._fit_height()
        self._update_summary()

    def add_days(self):
        col = next(c for c in self.field.columns if c.type == "date" and not c.formula)
        dates = [r.get(col.id) for r in self.model.rows if r.get(col.id)]
        try:
            start = parse_date(dates[-1]) + dt.timedelta(days=1) if dates else dt.date.today().replace(day=1)
        except ValueError:
            start = dt.date.today().replace(day=1)
        d = AddDaysDialog(self.window(), self.model, start)
        if not d.exec():
            return
        days = d.days(self.pack.country)
        if not days:
            return
        c = self.field.columns.index(col)
        base = len(self.model.rows)
        self.model.set_cells([(base + k, c, day.isoformat()) for k, day in enumerate(days)])
        self.view.scrollTo(self.model.index(base, c))

    def clear(self):
        if not self.model.rows:
            return
        if frame_mod.ask(self.window(), "Clear the table?",
                         f"Remove all {len(self.model.rows)} rows of {self.field.label}?") != QMessageBox.Yes:
            return
        self.model.set_rows([])
        self._edited()

    def expand(self):
        ExpandDialog(self.window(), self).exec()
        self._update_summary()
