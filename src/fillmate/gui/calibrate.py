"""Calibration tool: place where each value goes on a PDF by drawing on the page.

Scene coordinates are PDF points (1/72 inch, origin top-left), the same units pack.yaml uses.

No modes: drag over a blank line (or double-click it) to add a box, click a box to select
and move it, right-click for check marks. Drag over a ruled table (or right-click in it) to
fill it row by row: fillmate snaps to the table's lines and guesses its columns from the header. Values are picked by field name; the
{{ template }} behind them only shows up for custom text. Fields made here are written to
the pack together with the positions, when you save, never before.
"""
import copy
import re

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QKeySequence, QPainter, QPen, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDoubleSpinBox, QFormLayout, QGraphicsPixmapItem, QGraphicsRectItem, QGraphicsScene,
    QGraphicsView, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QSpinBox, QVBoxLayout, QWidget,
)

from .. import FillmateError
from ..authoring import detect_grids, field_uses, guess_columns, pick_grid, remove_field, render_page, save_positions
from ..pack import NAME_RE, load_pack
from ..templating import slug
from . import frame as frame_mod
from .frame import Frame, is_passive
from .ink import (
    DotGrid, GlassButton, GlassGroup, GlassLayer, GlassMenu, GlassPanel, MarginNote, T, label, set_role,
)

DPI = 144
QUICK_WIDTH = 180  # points: the box a double-click makes
HINTS = ("Drag over a blank line to add a box, or double-click it.",
         "For a table, press the grid button above, or drag over the table.",
         "Drag a box to move it: arrow keys nudge it, Delete removes it.",
         "Right-click for check marks. Ctrl + wheel zooms.")
KINDS = (("Text", "text"), ("Date", "date"), ("Time", "time"), ("Number", "number"), ("Formula", "formula"),
         ("Leave empty", None))
NEW_TABLE = "__new_table__"
INSET, PANEL_W = 12, 340
TYPES = (("Text", "text"), ("Long text", "multiline"), ("Date", "date"), ("Several days", "dates"),
         ("Time", "time"))
CUSTOM = "__custom__"
PAGE_INK = "#1b2a6b"  # the page stays white in dark mode too, so boxes keep the light ink


def flush(heading):
    """A section heading in the values panel, lined up with the labels under it."""
    heading.setProperty("flush", "true")
    heading.style().unpolish(heading)
    heading.style().polish(heading)
    return heading


def template_for(f):
    """The value template that puts field `f` on the page."""
    if f["type"] == "dates":
        return f"{{{{ {f['id']} | dates }}}}"
    if f["type"] == "date":
        return f"{{{{ {f['id']} | date }}}}"
    return f"{{{{ {f['id']} }}}}"


class AreaItem(QGraphicsRectItem):
    """One text line or check mark on the page, linked to its entry. Drawn in ink: you placed it."""

    def __init__(self, rect, entry_index, line_index):
        super().__init__(rect)
        self.entry_index, self.line_index = entry_index, line_index
        self.setFlags(QGraphicsRectItem.ItemIsSelectable | QGraphicsRectItem.ItemIsMovable)

    def paint(self, painter, option, widget=None):
        ink = QColor(PAGE_INK)
        fill = QColor(ink)
        if self.isSelected():
            fill.setAlpha(26)
            pen = QPen(ink, 1.8)
        else:
            fill.setAlpha(12)
            ink.setAlpha(170)
            pen = QPen(ink, 1.2, Qt.DashLine)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(QBrush(fill))
        painter.drawRoundedRect(self.rect(), 1.5, 1.5)


class GridItem(AreaItem):
    """A table on the page: drag a divider to move it, an outer edge to stretch the whole grid,
    or the inside to move it. Clicking a cell picks its column for the editor."""

    GRAB = 5  # px on screen around a line that grabs it

    def __init__(self, entry, entry_index, labels, dialog):
        xs, ys = entry["xs"], entry["ys"]
        super().__init__(QRectF(xs[0], ys[0], xs[-1] - xs[0], ys[-1] - ys[0]), entry_index, None)
        self.xs, self.ys = list(xs), list(ys)
        self.labels = labels  # per grid column: what goes there ("" = left empty)
        self.dialog = dialog
        self.column = None    # highlighted grid column
        self.grab = None      # ("x" | "y", index) while a line is dragged
        self.setAcceptHoverEvents(True)

    def _tolerance(self):
        views = self.scene().views() if self.scene() else []
        return self.GRAB / (views[0].transform().m11() if views else 1)

    def _line_at(self, pos):
        tol = self._tolerance()
        inside_y = self.ys[0] - tol <= pos.y() <= self.ys[-1] + tol
        inside_x = self.xs[0] - tol <= pos.x() <= self.xs[-1] + tol
        for j, x in enumerate(self.xs):
            if inside_y and abs(pos.x() - x) <= tol:
                return "x", j
        for i, y in enumerate(self.ys):
            if inside_x and abs(pos.y() - y) <= tol:
                return "y", i
        return None

    def column_at(self, x):
        return next((j for j in range(len(self.xs) - 1) if self.xs[j] <= x < self.xs[j + 1]), None)

    def boundingRect(self):
        pad = 4
        return QRectF(self.xs[0] - pad, self.ys[0] - pad, self.xs[-1] - self.xs[0] + 2 * pad,
                      self.ys[-1] - self.ys[0] + 2 * pad)

    def shape(self):
        from PySide6.QtGui import QPainterPath
        path = QPainterPath()
        path.addRect(self.boundingRect())
        return path

    def paint(self, painter, option, widget=None):
        ink = QColor(PAGE_INK)
        selected = self.isSelected()
        if self.column is not None and selected:
            fill = QColor(ink)
            fill.setAlpha(30)
            j = self.column
            painter.fillRect(QRectF(self.xs[j], self.ys[0], self.xs[j + 1] - self.xs[j], self.ys[-1] - self.ys[0]), fill)
        fill = QColor(ink)
        fill.setAlpha(14 if selected else 8)
        painter.fillRect(QRectF(self.xs[0], self.ys[0], self.xs[-1] - self.xs[0], self.ys[-1] - self.ys[0]), fill)
        pen = QPen(ink if selected else QColor(27, 42, 107, 150), 1.6 if selected else 1.1,
                   Qt.SolidLine if selected else Qt.DashLine)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.drawRect(QRectF(self.xs[0], self.ys[0], self.xs[-1] - self.xs[0], self.ys[-1] - self.ys[0]))
        inner = QPen(QColor(27, 42, 107, 120 if selected else 70), 0.8, Qt.DashLine)
        inner.setCosmetic(True)
        painter.setPen(inner)
        for x in self.xs[1:-1]:
            painter.drawLine(QPointF(x, self.ys[0]), QPointF(x, self.ys[-1]))
        for y in self.ys[1:-1]:
            painter.drawLine(QPointF(self.xs[0], y), QPointF(self.xs[-1], y))
        # What goes in each column, written in the first row as a sample.
        f = painter.font()
        f.setPointSizeF(max(3.0, min(6.5, (self.ys[1] - self.ys[0]) * 0.36)))
        painter.setFont(f)
        metrics = painter.fontMetrics()
        for j, text in enumerate(self.labels):
            r = QRectF(self.xs[j] + 2, self.ys[0], self.xs[j + 1] - self.xs[j] - 4, self.ys[1] - self.ys[0])
            painter.setPen(QColor(27, 42, 107, 200) if text else QColor(120, 120, 128, 160))
            shown = metrics.elidedText(text, Qt.ElideRight, int(r.width())) if text else "—"
            painter.drawText(r, Qt.AlignCenter | Qt.TextSingleLine, shown)

    def hoverMoveEvent(self, event):
        hit = self._line_at(event.pos())
        self.setCursor(Qt.SplitHCursor if hit and hit[0] == "x" else Qt.SplitVCursor if hit else Qt.SizeAllCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.grab = self._line_at(event.pos())
            if self.grab is not None:
                self.setSelected(True)
                self.dialog.select_grid_column(self.entry_index, None)
                event.accept()
                return
            j = self.column_at(event.pos().x())
            super().mousePressEvent(event)
            self.dialog.select_grid_column(self.entry_index, j)
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.grab is None:
            super().mouseMoveEvent(event)
            return
        self.prepareGeometryChange()
        axis, k = self.grab
        edges = self.xs if axis == "x" else self.ys
        v = event.pos().x() if axis == "x" else event.pos().y()
        gap = 4
        if 0 < k < len(edges) - 1:  # a divider: between its neighbours
            edges[k] = max(edges[k - 1] + gap, min(v, edges[k + 1] - gap))
        else:  # an outer edge: stretch the whole grid, so even rows stay even
            far = edges[-1] if k == 0 else edges[0]
            v = min(v, far - gap * (len(edges) - 1)) if k == 0 else max(v, far + gap * (len(edges) - 1))
            old0, old1 = edges[0], edges[-1]
            new0, new1 = (v, old1) if k == 0 else (old0, v)
            scale = (new1 - new0) / (old1 - old0)
            edges[:] = [new0 + (e - old0) * scale for e in edges]
        self.update()

    def mouseReleaseEvent(self, event):
        if self.grab is not None:
            self.grab = None
            # Redrawing replaces this item: do it once its own event is over.
            index, xs, ys = self.entry_index, list(self.xs), list(self.ys)
            QTimer.singleShot(0, lambda: self.dialog.on_grid_edited(index, xs, ys))
            return
        super().mouseReleaseEvent(event)


class PageView(QGraphicsView):
    drawn = Signal(QRectF)          # a new text box
    menu = Signal(QPointF, object)  # right-click: scene point, AreaItem or None
    moved = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setFrameShape(QGraphicsView.NoFrame)
        self.setStyleSheet("background: transparent;")
        self.viewport().setAutoFillBackground(False)
        self.setBackgroundBrush(Qt.NoBrush)
        self.origin, self.rubber = None, None
        self.background = None

    def set_page(self, png):
        pm = QPixmap()
        pm.loadFromData(png, "PNG")
        if self.background is None:
            self.background = QGraphicsPixmapItem()
            self.background.setZValue(-1)
            self.scene().addItem(self.background)
        self.background.setPixmap(pm)
        self.background.setScale(72 / DPI)
        self.scene().setSceneRect(QRectF(0, 0, pm.width() * 72 / DPI, pm.height() * 72 / DPI))

    def fit_width(self):
        rect = self.scene().sceneRect()
        if rect.width():
            self.resetTransform()
            scale = (self.viewport().width() - 40) / rect.width()
            self.scale(scale, scale)

    def zoom(self, factor):
        self.scale(factor, factor)

    def wheelEvent(self, event):
        if event.modifiers() & Qt.ControlModifier:
            factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
            self.scale(factor, factor)
        else:
            super().wheelEvent(event)

    def _area_at(self, pos):
        item = self.itemAt(pos)
        return item if isinstance(item, AreaItem) else None

    def mousePressEvent(self, event):
        pos = event.position().toPoint()
        if event.button() == Qt.LeftButton and self._area_at(pos) is None:
            self.scene().clearSelection()
            self.origin = self.mapToScene(pos)
            pen = QPen(QColor(PAGE_INK), 1.2, Qt.DashLine)
            pen.setCosmetic(True)
            self.rubber = self.scene().addRect(QRectF(self.origin, self.origin), pen)
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.rubber is not None:
            self.rubber.setRect(QRectF(self.origin, self.mapToScene(event.position().toPoint())).normalized())
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.rubber is not None:
            rect = self.rubber.rect()
            self.scene().removeItem(self.rubber)
            self.rubber = None
            if rect.width() >= 8:
                self.drawn.emit(rect)
            return
        super().mouseReleaseEvent(event)
        if any(item.pos() != QPointF(0, 0) for item in self.scene().selectedItems()):
            self.moved.emit()

    def mouseDoubleClickEvent(self, event):
        pos = event.position().toPoint()
        if event.button() == Qt.LeftButton and self._area_at(pos) is None:
            p = self.mapToScene(pos)
            right = min(p.x() + QUICK_WIDTH, self.scene().sceneRect().right() - 20)
            self.drawn.emit(QRectF(QPointF(p.x(), p.y() - 9), QPointF(right, p.y() + 3)))
            return
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event):
        self.menu.emit(self.mapToScene(event.pos()), self._area_at(event.pos()))


class NewFieldDialog(QDialog):
    """A new question for the form: its name, and how it is asked. A small sheet of the same paper."""

    def __init__(self, parent, yes_no=False):
        super().__init__(parent)
        self.setWindowTitle("New field")
        self.frame = Frame(self, lambda w, pos: is_passive(w), dialog=True)
        if frame_mod.PLATFORM == "linux":
            self.setStyleSheet("QDialog { background: transparent; }")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.paper = DotGrid()
        self.paper.radius = self.frame.radius
        outer.addWidget(self.paper)
        lay = QVBoxLayout(self.paper)
        lay.setContentsMargins(24, 20, 24, 18)
        lay.setSpacing(14)
        lay.addWidget(set_role(QLabel("New yes/no field" if yes_no else "New field"), "h1"))
        form = QFormLayout()
        form.setVerticalSpacing(12)
        self.label = QLineEdit()
        self.label.setPlaceholderText("e.g. Overnight stay" if yes_no else "e.g. Destination")
        form.addRow(label("What is asked", "lbl"), self.label)
        self.type = QComboBox()
        if yes_no:
            self.type.addItem("Yes / no", "bool")
            self.type.setEnabled(False)
        else:
            for text, value in TYPES:
                self.type.addItem(text, value)
        form.addRow(label("Kind of answer", "lbl"), self.type)
        self.section = QComboBox()
        self.section.addItem("Every time the form is filled", "fields")
        self.section.addItem("Once, in your profile (e.g. your name)", "profile")
        form.addRow(label("Asked", "lbl"), self.section)
        lay.addLayout(form)
        self.problem = MarginNote(arrow=False, px=19)
        self.problem.hide()
        lay.addWidget(self.problem)
        row = QHBoxLayout()
        row.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        ok = set_role(QPushButton("Add field"), "ink")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        for b in (cancel, ok):
            b.setMinimumHeight(36)
            row.addWidget(b)
        lay.addLayout(row)
        self.edge = self.frame.overlay(self)
        self.resize(460, 280)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.edge.setGeometry(self.rect())
        self.edge.raise_()

    def showEvent(self, event):
        super().showEvent(event)
        self.frame.after_show()

    def accept(self):
        if not self.label.text().strip():
            self.problem.setText("Say what is asked, e.g. “Destination”.")
            self.problem.show()
            self.label.setFocus()
            return
        super().accept()


class CalibrateDialog(QDialog):
    def __init__(self, parent, pack, doc_id):
        super().__init__(parent)
        self.pack = pack
        self.doc = next(d for d in pack.documents if d.id == doc_id)
        self.entries = copy.deepcopy(self.doc.fill)
        self.new_fields = []  # made here; saved with the positions
        self.table_columns = {}  # table field id -> its columns as edited here (dicts)
        self.grid_column = None  # the grid column picked in the table editor
        self.grid_cache = {}     # page -> ruled grids found on it
        self.page_no, self.page_count = 0, 1
        self.current = None  # index of the selected entry
        self.setWindowTitle(f"Positions – {self.doc.label}")
        self.resize(1400, 900)
        self.setMinimumSize(1000, 640)

        # Dot-grid paper with the page on it; glass toolbar and a glass panel with the values.
        self.paper = DotGrid(self)
        self.glass = GlassLayer(self.paper)
        self.frame = Frame(self, lambda w, pos: pos.y() < INSET + 52 and is_passive(w), dialog=True)
        if frame_mod.PLATFORM == "linux":
            self.setStyleSheet("QDialog { background: transparent; }")
        self.paper.radius = self.frame.radius

        self.view = PageView(self.paper)
        self.view.drawn.connect(self.on_drawn)
        self.view.menu.connect(self.on_menu)
        self.view.moved.connect(self.on_moved)
        self.view.scene().selectionChanged.connect(self.on_scene_selection)
        for bar in (self.view.verticalScrollBar(), self.view.horizontalScrollBar()):
            bar.valueChanged.connect(self.glass.schedule)

        self.title_box = QWidget(self.paper)
        tv = QVBoxLayout(self.title_box)
        tv.setContentsMargins(0, 0, 0, 0)
        tv.setSpacing(0)
        tv.addWidget(set_role(label("Positions"), "h1"))
        self.subtitle = label("", "muted")
        tv.addWidget(self.subtitle)

        self.pages = self.glass.add(GlassGroup(self))
        self.prev_btn = self.pages.add("back", "Previous page", lambda: self.go_page(self.page_no - 1))
        self.next_btn = self.pages.add("forward", "Next page", lambda: self.go_page(self.page_no + 1))
        self.table_bar = self.glass.add(GlassGroup(self))
        self.table_bar.add("grid", "Add a table: snaps to the one on this page, or draws a grid to size",
                           self.add_table_button)
        self.zoom_bar = self.glass.add(GlassGroup(self))
        self.zoom_bar.add("minus", "Zoom out (Ctrl + wheel)", lambda: self.view.zoom(1 / 1.15))
        self.zoom_bar.add("fit", "Fit the width", self.view.fit_width)
        self.zoom_bar.add("plus", "Zoom in (Ctrl + wheel)", lambda: self.view.zoom(1.15))
        self.cancel_btn = self.glass.add(GlassButton("Cancel", None, self))
        self.cancel_btn.clicked.connect(self.reject)
        self.save_btn = self.glass.add(GlassButton("Save", None, self, tint=T["ink"]))
        self.save_btn.clicked.connect(self.save)

        self.panel = self.glass.add(GlassPanel(self, radius=24, bezel=30, blur=0.5, regular=True))
        pv = QVBoxLayout(self.panel)
        pv.setContentsMargins(12, 16, 12, 12)
        pv.setSpacing(10)
        self.win_left = self.frame.make_buttons(self, "left")
        self.win_right = self.frame.make_buttons(self, "right")
        head = QHBoxLayout()
        head.setContentsMargins(6, 0, 0, 0)
        head.addWidget(set_role(label("Values on this document"), "h1"), 1)
        if self.win_right is not None:
            head.addSpacing(self.win_right.width() + 4)  # the window buttons sit in this corner
        pv.addLayout(head)
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self.select_entry)
        pv.addWidget(self.list, 1)

        self.hints = QWidget()  # fillmate's notes, while nothing is selected
        hv = QVBoxLayout(self.hints)
        hv.setContentsMargins(6, 4, 4, 4)
        hv.setSpacing(8)
        for text in HINTS:
            hv.addWidget(MarginNote(text, arrow=False, px=19))
        pv.addWidget(self.hints)

        self.editor = QWidget()
        ef = QVBoxLayout(self.editor)
        ef.setContentsMargins(6, 4, 4, 0)
        ef.setSpacing(6)
        ef.addWidget(flush(set_role(label("Selected value"), "sec")))
        self.value_label = label("What goes here", "lbl")
        ef.addWidget(self.value_label)
        self.value = set_role(QComboBox(), "line")
        self.value.activated.connect(self.on_value_picked)
        ef.addWidget(self.value)
        self.custom_label = label("Custom text", "lbl")
        ef.addWidget(self.custom_label)
        self.custom = QLineEdit()
        self.custom.setPlaceholderText("Text, with {{ field }} where a value goes")
        self.custom.textEdited.connect(self.on_custom_edited)
        ef.addWidget(self.custom)
        size_row = QHBoxLayout()
        self.size_label = label("Text size", "lbl")
        size_row.addWidget(self.size_label, 1)
        self.size = QDoubleSpinBox()
        self.size.setRange(4, 36)
        self.size.setSingleStep(0.5)
        self.size.setDecimals(1)
        self.size.setSuffix(" pt")
        self.size.setFixedWidth(90)
        self.size.valueChanged.connect(self.on_size)
        size_row.addWidget(self.size)
        ef.addLayout(size_row)
        delete = set_role(QPushButton("Delete from the page"), "danger")
        delete.clicked.connect(self.delete_entry)
        ef.addWidget(delete)
        pv.addWidget(self.editor)
        pv.addWidget(self._table_editor())
        tidy = QPushButton("Remove unused fields…")
        tidy.setFlat(True)
        tidy.setStyleSheet(f"QPushButton {{ border: 0; background: transparent; color: {T['label2']}; "
                           "text-decoration: underline; padding: 4px 6px; }")
        tidy.setToolTip("Delete fields of this pack that nothing uses")
        tidy.clicked.connect(self.remove_unused_fields)
        pv.addWidget(tidy, 0, Qt.AlignLeft)

        self.frame.adopt(self.glass, self.win_left, self.win_right)
        self.context = self.glass.add(GlassMenu(self, width=340))  # right-click menu, glass like the others
        self.edge = self.frame.overlay(self)

        for key in (QKeySequence.Delete, QKeySequence.Backspace):
            QShortcut(key, self.view, activated=self.delete_selected_area)

        self.render()
        self.refresh_list()
        QTimer.singleShot(0, self.view.fit_width)

    def _table_editor(self):
        """Panel section for a selected table: which table, its grid, and the picked column."""
        self.tbl = QWidget()
        ef = QVBoxLayout(self.tbl)
        ef.setContentsMargins(6, 4, 4, 0)
        ef.setSpacing(6)
        ef.addWidget(flush(set_role(label("Selected table"), "sec")))
        self.tbl_field_label = label("Table", "lbl")
        ef.addWidget(self.tbl_field_label)
        self.tbl_field = set_role(QComboBox(), "line")
        self.tbl_field.activated.connect(self.on_table_picked)
        ef.addWidget(self.tbl_field)
        self.tbl_name = QLineEdit()
        self.tbl_name.setPlaceholderText("Name in the form, e.g. Hours worked")
        self.tbl_name.textEdited.connect(self.on_table_renamed)
        ef.addWidget(self.tbl_name)
        grid = QHBoxLayout()
        grid.addWidget(label("Rows", "lbl"))
        self.tbl_rows = QSpinBox()
        self.tbl_rows.setRange(1, 200)
        self.tbl_rows.valueChanged.connect(self.on_rows_count)
        grid.addWidget(self.tbl_rows)
        grid.addSpacing(10)
        grid.addWidget(label("Columns", "lbl"))
        self.tbl_cols = QSpinBox()
        self.tbl_cols.setRange(1, 30)
        self.tbl_cols.valueChanged.connect(self.on_cols_count)
        grid.addWidget(self.tbl_cols)
        grid.addStretch()
        ef.addLayout(grid)
        even = QPushButton("Even out rows")
        even.setToolTip("Give every row the same height")
        even.clicked.connect(lambda: self.on_rows_count(self.tbl_rows.value(), force=True))
        ef.addWidget(even, 0, Qt.AlignLeft)
        ef.addSpacing(4)
        ef.addWidget(flush(set_role(label("Column"), "sec")))
        self.col_pick = set_role(QComboBox(), "line")
        self.col_pick.activated.connect(lambda i: self.select_grid_column(self.current, i))
        ef.addWidget(self.col_pick)
        row = QHBoxLayout()
        self.col_kind = set_role(QComboBox(), "line")
        for text, value in KINDS:
            self.col_kind.addItem(text, value)
        self.col_kind.activated.connect(self.on_column_kind)
        row.addWidget(self.col_kind)
        self.col_label = QLineEdit()
        self.col_label.setPlaceholderText("Column name")
        self.col_label.textEdited.connect(self.on_column_label)
        row.addWidget(self.col_label, 1)
        ef.addLayout(row)
        self.col_formula = QLineEdit()
        self.col_formula.setPlaceholderText("e.g. hours_between(start, end)")
        self.col_formula.textEdited.connect(self.on_column_formula)
        ef.addWidget(self.col_formula)
        self.col_note = label("", "lbl", wrap=True)
        ef.addWidget(self.col_note)
        size_row = QHBoxLayout()
        size_row.addWidget(label("Text size", "lbl"), 1)
        self.tbl_size = QDoubleSpinBox()
        self.tbl_size.setRange(4, 24)
        self.tbl_size.setSingleStep(0.5)
        self.tbl_size.setDecimals(1)
        self.tbl_size.setSuffix(" pt")
        self.tbl_size.setFixedWidth(90)
        self.tbl_size.valueChanged.connect(self.on_size)
        size_row.addWidget(self.tbl_size)
        ef.addLayout(size_row)
        delete = set_role(QPushButton("Delete from the page"), "danger")
        delete.clicked.connect(self.delete_entry)
        ef.addWidget(delete)
        self.tbl.hide()
        return self.tbl

    def resizeEvent(self, event):
        super().resizeEvent(event)
        w, h = self.width(), self.height()
        self.paper.setGeometry(self.rect())
        self.panel.setGeometry(w - INSET - PANEL_W, INSET, PANEL_W, h - 2 * INSET)
        left = INSET + 6
        if self.win_left is not None:
            self.win_left.move(INSET + 4, INSET + 6)
            left = self.win_left.x() + self.win_left.width() + 12
        if self.win_right is not None:
            self.win_right.move(w - INSET - 12 - self.win_right.width(), INSET + 10)
        right = self.panel.x() - 12
        self.save_btn.move(right - self.save_btn.width(), INSET)
        self.cancel_btn.move(self.save_btn.x() - 10 - self.cancel_btn.width(), INSET)
        self.title_box.setGeometry(left, INSET, 320, 44)
        mid = (left + 320 + self.cancel_btn.x()) // 2
        groups = [g for g in (self.pages, self.table_bar, self.zoom_bar) if not g.isHidden()]
        total = sum(g.width() for g in groups) + 10 * (len(groups) - 1)
        x = mid - total // 2
        for g in groups:
            g.move(x, INSET)
            x += g.width() + 10
        self.view.setGeometry(0, INSET + 52, self.panel.x() - 4, h - INSET - 52)
        for wdg in (self.panel, self.pages, self.table_bar, self.zoom_bar, self.cancel_btn, self.save_btn, self.win_left,
                    self.win_right):
            if wdg is not None:
                wdg.raise_()
        self.edge.setGeometry(self.rect())
        self.edge.raise_()
        self.glass.schedule()

    def showEvent(self, event):
        super().showEvent(event)
        self.frame.after_show()

    # --- fields ---

    def fields(self):
        """Every field the pack has, plus the ones made here: {id, label, type, options}."""
        out = [{"id": f.id, "label": f.label, "type": f.type, "options": list(f.options)}
               for f in self.pack.profile + self.pack.fields]
        return out + [nf for nf in self.new_fields if nf["id"] not in {f["id"] for f in out}]

    def tables(self):
        return [f for f in self.fields() if f["type"] == "table"]

    def columns_of(self, fid):
        """The working copy of a table's columns (dicts), made on first use."""
        if fid not in self.table_columns:
            f = self.pack.field(fid)
            self.table_columns[fid] = [
                {"id": c.id, "label": c.label, "type": c.type, "formula": c.formula, "format": c.format,
                 "default": c.default, "options": list(c.options), "required": c.required,
                 "placeholder": c.placeholder, "help": c.help}
                for c in (f.columns if f is not None else [])]
        return self.table_columns[fid]

    def column(self, fid, cid):
        return next((c for c in self.columns_of(fid) if c["id"] == cid), None)

    def text_choices(self):
        out = [(f["label"], template_for(f)) for f in self.fields() if f["type"] not in ("bool", "table")]
        out += [(k.replace("_", " ").capitalize(), f"{{{{ {k} }}}}") for k in self.pack.computed]
        placed = []
        for e in self.entries:
            if "table" in e and e["table"] and e["table"] not in placed:
                placed.append(e["table"])
        for fid in placed:  # totals of the tables on this document, per sheet and overall
            name = next((f["label"] for f in self.tables() if f["id"] == fid), fid)
            cols = self.columns_of(fid)
            day = next((c["id"] for c in cols if c.get("type") == "date" and not c.get("formula")), None)
            if day:
                out.append((f"{name}: days on this sheet", f"{{{{ sheet.rows | days('{day}') }}}}"))
            for c in cols:
                if c.get("formula") or c.get("type") == "number":
                    out.append((f"{name}: total {c['label']} on this sheet", f"{{{{ sheet.rows | total('{c['id']}') }}}}"))
            out.append((f"{name}: rows on this sheet", "{{ sheet.rows | length }}"))
            out.append((f"{name}: sheet number (1 of 2)", "{{ sheet.number }} of {{ sheet.count }}"))
            if day:
                out.append((f"{name}: days in all", f"{{{{ {fid} | days('{day}') }}}}"))
            for c in cols:
                if c.get("formula") or c.get("type") == "number":
                    out.append((f"{name}: total {c['label']} in all", f"{{{{ {fid} | total('{c['id']}') }}}}"))
        return out

    def mark_choices(self):
        out = []
        for f in self.fields():
            if f["type"] == "bool":
                out.append((f"{f['label']} is yes", f["id"]))
            elif f["type"] == "choice":
                for o in f["options"]:
                    out.append((f"{f['label']} is {o}", f"{f['id']} == '{o}'"))
        return out

    def describe(self, e):
        if "table" in e:
            name = next((f["label"] for f in self.tables() if f["id"] == e["table"]), "(choose the table)")
            return f"▦  {name}   · {len(e['xs']) - 1} columns × {len(e['ys']) - 1} rows"
        if "field" in e:
            return f"Form field “{e['field']}”: {self.name_of(e['value'], self.text_choices())}"
        if "mark" in e:
            return f"✓  when {self.name_of(e['mark'], self.mark_choices()) or '…'}"
        n = len(e["lines"])
        name = self.name_of(e["value"], self.text_choices()) or "(choose what goes here)"
        return f"{name}" + (f"   · {n} lines" if n > 1 else "")

    @staticmethod
    def name_of(value, choices):
        return next((text for text, v in choices if v == value), value)

    def new_field_id(self, text):
        used = {f["id"] for f in self.fields()} | set(self.pack.computed)
        base = re.sub(r"\W+", "_", slug(text, 40)).strip("_").lower() or "field"
        if not NAME_RE.match(base):
            base = "f_" + base
        fid, n = base, 2
        while fid in used:
            fid, n = f"{base}_{n}", n + 1
        return fid

    def make_field(self, yes_no=False):
        d = NewFieldDialog(self, yes_no=yes_no)
        if not d.exec():
            return None
        f = {"id": self.new_field_id(d.label.text()), "label": d.label.text().strip(),
             "type": d.type.currentData(), "section": d.section.currentData(), "options": []}
        self.new_fields.append(f)
        return f

    def remove_unused_fields(self):
        used_here = " ".join(str(e.get("value", e.get("mark", e.get("table", "")))) for e in self.entries)
        unused = []
        for f in self.pack.profile + self.pack.fields:
            if re.search(rf"\b{re.escape(f.id)}\b", used_here):
                continue
            try:
                if not field_uses(self.pack.root, f.id):
                    unused.append(f)
            except FillmateError:
                pass
        # Fields made here and not placed (any more) were never saved: they just go.
        dropped = [nf for nf in self.new_fields if not re.search(rf"\b{nf['id']}\b", used_here)]
        self.new_fields = [nf for nf in self.new_fields if nf not in dropped]
        for nf in dropped:
            self.table_columns.pop(nf["id"], None)
        made_here = ""
        if dropped:
            made_here = ("Removed what you made here and no longer use:\n\n"
                         + "\n".join(f"• {nf['label']}" for nf in dropped))
        if not unused:
            if dropped:
                frame_mod.tell(self, "Unused fields removed", made_here)
            else:
                frame_mod.tell(self, "Nothing to remove", "Every field of this pack is used somewhere.")
            self.refresh_list()
            return
        names = "\n".join(f"• {f.label}" for f in unused)
        if frame_mod.ask(self, "Remove unused fields?",
                                (made_here + "\n\n" if made_here else "")
                                + f"These fields of the pack are not used on any document, in the email or in a "
                                f"rule:\n\n{names}\n\nRemove them from the pack?") != QMessageBox.Yes:
            self.refresh_list()
            return
        for f in unused:
            try:
                self.pack = remove_field(self.pack.root, f.id)
            except FillmateError as e:
                frame_mod.tell(self, "Remove field", str(e))
        self.refresh_list()

    # --- page ---

    def _original_columns(self, fid):
        f = self.pack.field(fid)
        return [{"id": c.id, "label": c.label, "type": c.type, "formula": c.formula, "format": c.format,
                 "default": c.default, "options": list(c.options), "required": c.required,
                 "placeholder": c.placeholder, "help": c.help} for c in (f.columns if f else [])]

    def go_page(self, index):
        index = max(0, min(index, self.page_count - 1))
        if index != self.page_no:
            self.page_no = index
            self.render()

    def show_pages(self):
        where = self.doc.label
        self.subtitle.setToolTip(f"{self.doc.label} · {self.pack.name}")
        if self.page_count > 1:
            where = f"Page {self.page_no + 1} of {self.page_count} · " + where
        self.subtitle.setText(where)
        self.pages.setVisible(self.page_count > 1)
        self.prev_btn.setEnabled(self.page_no > 0)
        self.next_btn.setEnabled(self.page_no < self.page_count - 1)

    def render(self):
        try:
            png, self.page_count = render_page(self.doc.template, self.page_no, DPI)  # the bare page
        except Exception as e:
            frame_mod.tell(self, "Cannot show the page", str(e))
            return
        self.view.set_page(png)
        self.draw_areas()
        self.show_pages()
        self.glass.schedule()

    def draw_areas(self):
        scene = self.view.scene()
        scene.blockSignals(True)
        for item in [i for i in scene.items() if isinstance(i, AreaItem)]:
            scene.removeItem(item)
        for i, e in enumerate(self.entries):
            if "field" in e or e.get("page", 0) != self.page_no:
                continue
            if "table" in e:
                labels = [(self.column(e["table"], c) or {}).get("label", c) if c else "" for c in e["cols"]]
                item = GridItem(e, i, labels, self)
                scene.addItem(item)
                item.setSelected(i == self.current)
                if i == self.current:
                    item.column = self.grid_column
                continue
            if "mark" in e:
                s = e.get("size", 11)
                x, y = e["at"]
                item = AreaItem(QRectF(x - 1, y - s * 0.75, s * 0.75, s * 0.85), i, None)
                scene.addItem(item)
                item.setSelected(i == self.current)
            else:
                s = e.get("size", 10)
                for j, (x0, y, x1) in enumerate(e["lines"]):
                    item = AreaItem(QRectF(x0, y - s * 0.8, x1 - x0, s * 1.0), i, j)
                    scene.addItem(item)
                    item.setSelected(i == self.current)
        scene.blockSignals(False)

    # --- list and editor ---

    def refresh_list(self):
        self.list.blockSignals(True)
        self.list.clear()
        for e in self.entries:
            QListWidgetItem(self.describe(e), self.list)
        self.list.setCurrentRow(self.current if self.current is not None else -1)
        self.list.blockSignals(False)
        self.show_editor()

    def show_editor(self):
        e = self.entries[self.current] if self.current is not None else None
        is_table = e is not None and "table" in e
        self.editor.setVisible(e is not None and not is_table)
        self.tbl.setVisible(is_table)
        self.hints.setVisible(e is None)
        if is_table:
            self.show_table_editor(e)
            return
        if e is None:
            return
        is_mark = "mark" in e
        self.value_label.setText("Ticked when" if is_mark else "What goes here")
        choices = self.mark_choices() if is_mark else self.text_choices()
        current = e["mark"] if is_mark else e["value"]
        self.value.blockSignals(True)
        self.value.clear()
        if not current:
            self.value.addItem("Choose…", None)
        for text, v in choices:
            self.value.addItem(text, v)
        self.value.addItem("New yes/no question…" if is_mark else "New field…", "__new__")
        self.value.addItem("Custom condition…" if is_mark else "Custom text…", CUSTOM)
        known = [v for _t, v in choices]
        if not current:
            self.value.setCurrentIndex(0)
        elif current in known:
            self.value.setCurrentIndex(self.value.findData(current))
        else:
            self.value.setCurrentIndex(self.value.findData(CUSTOM))
        self.value.blockSignals(False)
        custom = bool(current) and current not in known
        self.custom.setText(current if custom else "")
        for w in (self.custom, self.custom_label):
            w.setVisible(custom)
        self.size.blockSignals(True)
        self.size.setValue(e.get("size", 11 if is_mark else 10))
        self.size.blockSignals(False)
        for w in (self.size, self.size_label):
            w.setVisible("field" not in e)

    def set_current_value(self, value):
        e = self.entries[self.current]
        e["mark" if "mark" in e else "value"] = value
        self.changed()

    def on_value_picked(self, index):
        if self.current is None:
            return
        data = self.value.itemData(index)
        e = self.entries[self.current]
        if data == "__new__":
            f = self.make_field(yes_no="mark" in e)
            if f is None:
                self.show_editor()
                return
            self.set_current_value(f["id"] if "mark" in e else template_for(f))
        elif data == CUSTOM:
            for w in (self.custom, self.custom_label):
                w.show()
            self.custom.setFocus()
        elif data:
            self.set_current_value(data)

    def on_custom_edited(self, text):
        if self.current is not None:
            e = self.entries[self.current]
            e["mark" if "mark" in e else "value"] = text
            item = self.list.item(self.current)
            if item:
                item.setText(self.describe(e))

    def changed(self, redraw=True):
        self.refresh_list()
        if redraw:
            self.draw_areas()

    # --- page events ---

    def select_entry(self, row):
        self.current = row if 0 <= row < len(self.entries) else None
        e = self.entries[self.current] if self.current is not None else None
        if e and "field" not in e and e.get("page", 0) != self.page_no:
            self.go_page(e.get("page", 0))
        self.show_editor()
        self.draw_areas()

    def on_scene_selection(self):
        items = [i for i in self.view.scene().selectedItems() if isinstance(i, AreaItem)]
        if items and items[0].entry_index != self.current:
            self.current = items[0].entry_index
            self.list.blockSignals(True)
            self.list.setCurrentRow(self.current)
            self.list.blockSignals(False)
            self.show_editor()

    @staticmethod
    def _line(rect):
        h = rect.height()
        if h < 6:  # a thin stroke along an underline
            return 10.0, [rect.left(), rect.bottom(), rect.right()]
        size = max(6.0, min(14.0, round(h * 0.75 * 2) / 2))
        return size, [rect.left(), rect.bottom() - h * 0.2, rect.right()]

    def grids_here(self):
        """Ruled grids on this page (found once per page: it takes a moment)."""
        if self.page_no not in self.grid_cache:
            try:
                self.grid_cache[self.page_no] = detect_grids(self.doc.template, self.page_no)
            except Exception:
                self.grid_cache[self.page_no] = []
        return self.grid_cache[self.page_no]

    def free_grids(self):
        """Grids on this page that no table placed here covers yet."""
        placed = [e for e in self.entries if "table" in e and e.get("page", 0) == self.page_no]

        def covered(g):
            cx, cy = (g["bbox"][0] + g["bbox"][2]) / 2, (g["bbox"][1] + g["bbox"][3]) / 2
            return any(e["xs"][0] <= cx <= e["xs"][-1] and e["ys"][0] - 40 <= cy <= e["ys"][-1] for e in placed)
        return [g for g in self.grids_here() if not covered(g)]

    def add_table_button(self):
        free = self.free_grids()
        if free:
            self.add_table(free[0])
            return
        # No ruled table (or it is placed already): a grid to size, in the middle of what is in view.
        c = self.view.mapToScene(self.view.viewport().rect().center())
        page = self.view.sceneRect()
        w, h = min(320.0, page.width() - 40), 120.0
        x = max(page.left() + 20, min(c.x() - w / 2, page.right() - 20 - w))
        self.add_table(None, QRectF(x, c.y() - h / 2, w, h))

    def on_drawn(self, rect):
        grid = None
        free = self.free_grids()
        if rect.width() > 60 and rect.height() > 30:  # big enough to be a table
            grid = pick_grid(free, (rect.left(), rect.top(), rect.right(), rect.bottom()))
        if grid is None:  # or drawn inside a table that is not filled yet
            grid = pick_grid(free, at=(rect.center().x(), rect.center().y()))
        if grid is not None:
            m = self.context
            m.clear()
            n_cols, n_rows = len(grid["xs"]) - 1, len(grid["ys"]) - 1
            m.add(f"Fill the table ({n_cols} columns × {n_rows} rows)", "grid", lambda: self.add_table(grid))
            if rect.height() > 30:
                m.add("Draw an empty grid here", "grid", lambda: self.add_table(None, rect))
            m.add("Just a text box", "box", lambda: self.add_text_box(rect))
            m.popup(self.view.mapToGlobal(self.view.mapFromScene(rect.center())))
            return
        self.add_text_box(rect)

    def add_text_box(self, rect):
        size, line = self._line(rect)
        self.entries.append({"value": "", "page": self.page_no, "lines": [line], "font": "helv",
                             "size": size, "min_size": 7.0})
        self.current = len(self.entries) - 1
        self.changed()
        self.value.setFocus()
        self.value.showPopup()

    def add_mark(self, point):
        size = 11.0
        self.entries.append({"mark": "", "page": self.page_no, "at": [point.x() - size * 0.33, point.y() + size * 0.36],
                             "symbol": "X", "size": size})
        self.current = len(self.entries) - 1
        self.changed()
        self.value.setFocus()
        self.value.showPopup()

    def on_menu(self, point, item):
        m = self.context
        m.clear()
        if item is not None:
            self.current = item.entry_index
            self.refresh_list()
            m.add("Delete from the page", "trash", self.delete_selected_area, danger=True)
        else:
            m.add("Add a text box here", "box", lambda: self.add_text_box(
                QRectF(QPointF(point.x(), point.y() - 9), QPointF(point.x() + QUICK_WIDTH, point.y() + 3))))
            m.add("Add a check mark here", "check", lambda: self.add_mark(point))
            grid = pick_grid(self.grids_here(), at=(point.x(), point.y()))
            if grid is not None:
                n_cols, n_rows = len(grid["xs"]) - 1, len(grid["ys"]) - 1
                m.add(f"Fill this table ({n_cols} × {n_rows})", "grid", lambda: self.add_table(grid))
            else:
                m.add("Add a grid here", "grid", lambda: self.add_table(
                    None, QRectF(point.x(), point.y(), min(240.0, self.view.sceneRect().right() - 20 - point.x()), 60)))
        m.popup(self.view.mapToGlobal(self.view.mapFromScene(point)))

    # --- tables ---

    def add_table(self, grid, rect=None):
        """A table entry over a detected grid (columns guessed from its header) or an empty 1×1 grid."""
        if grid is None:
            xs, ys, headers = [rect.left(), rect.right()], [rect.top(), rect.bottom()], [""]
        else:
            xs, ys, headers = grid["xs"], grid["ys"], grid["headers"]
        placed = {e.get("table") for e in self.entries}
        free = [f for f in self.tables() if f["id"] not in placed]
        if grid is not None or not free:
            used = {f["id"] for f in self.fields()} | set(self.pack.computed)
            columns, cols = guess_columns(headers, used)
            if grid is None:
                columns, cols = [], [None]
            name = "Hours worked" if any(c.get("formula") for c in columns) else "Rows"
            fid = self.new_field_id(name)
            self.new_fields.append({"id": fid, "label": name, "type": "table", "section": "fields", "options": []})
            self.table_columns[fid] = columns
        else:  # a table of the pack that is not on this document yet
            fid = free[0]["id"]
            cols = [c["id"] for c in self.columns_of(fid)][:len(xs) - 1]
            cols += [None] * (len(xs) - 1 - len(cols))
        self.entries.append({"table": fid, "page": self.page_no, "xs": list(xs), "ys": list(ys), "cols": cols,
                             "align": [], "overflow": "repeat_page", "font": "helv", "size": 9.0, "min_size": 6.0})
        self.current = len(self.entries) - 1
        self.grid_column = 0
        self.changed()

    def table_entry(self):
        e = self.entries[self.current] if self.current is not None else None
        return e if e is not None and "table" in e else None

    def show_table_editor(self, e):
        tables = self.tables()
        self.tbl_field.blockSignals(True)
        self.tbl_field.clear()
        for f in tables:
            self.tbl_field.addItem(f["label"], f["id"])
        self.tbl_field.addItem("New table…", NEW_TABLE)
        self.tbl_field.setCurrentIndex(max(0, self.tbl_field.findData(e["table"])))
        self.tbl_field.blockSignals(False)
        new = next((nf for nf in self.new_fields if nf["id"] == e["table"]), None)
        # A table made here: its name, as the form will show it. One of the pack's: which one.
        self.tbl_name.setVisible(new is not None)
        self.tbl_field.setVisible(new is None)
        self.tbl_field_label.setText("Name in the form" if new is not None else "Table")
        if new is not None:
            self.tbl_name.setText(new["label"])
        for spin, value in ((self.tbl_rows, len(e["ys"]) - 1), (self.tbl_cols, len(e["xs"]) - 1),
                            (self.tbl_size, e.get("size", 9))):
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)
        self.col_pick.blockSignals(True)
        self.col_pick.clear()
        for j, cid in enumerate(e["cols"]):
            c = self.column(e["table"], cid) if cid else None
            self.col_pick.addItem(f"{j + 1} · {c['label'] if c else '(left empty)'}")
        j = self.grid_column if self.grid_column is not None and self.grid_column < len(e["cols"]) else 0
        self.grid_column = j
        self.col_pick.setCurrentIndex(j)
        self.col_pick.blockSignals(False)
        c = self.column(e["table"], e["cols"][j]) if e["cols"][j] else None
        kind = None if c is None else "formula" if c.get("formula") else c.get("type", "text")
        self.col_kind.setCurrentIndex(max(0, self.col_kind.findData(kind)))
        self.col_label.setVisible(c is not None)
        self.col_label.setText(c["label"] if c else "")
        self.col_formula.setVisible(kind == "formula")
        self.col_formula.setText(c.get("formula", "") if c else "")
        times = [x["id"] for x in self.columns_of(e["table"]) if x.get("type") == "time" and not x.get("formula")]
        if kind == "formula":
            hint = "A formula over this row's columns: " + ", ".join(
                x["id"] for x in self.columns_of(e["table"]) if not x.get("formula"))
            if len(times) >= 2:
                hint += f". Hours between two times: hours_between({times[0]}, {times[1]})"
            self.col_note.setText(hint)
        else:
            self.col_note.setText("Typed in the form for each row." if c else "Nothing is written in this column.")

    def select_grid_column(self, entry_index, j):
        if entry_index is None:
            return
        if entry_index != self.current:
            self.current = entry_index
            self.list.blockSignals(True)
            self.list.setCurrentRow(entry_index)
            self.list.blockSignals(False)
        if j is not None:
            self.grid_column = j
        self.show_editor()
        for item in self.view.scene().items():
            if isinstance(item, GridItem) and item.entry_index == entry_index:
                item.column = self.grid_column
                item.update()

    def on_grid_edited(self, entry_index, xs, ys):
        e = self.entries[entry_index]
        e["xs"], e["ys"] = [round(x, 2) for x in xs], [round(y, 2) for y in ys]
        self.current = entry_index
        self.changed()

    def on_table_picked(self, index):
        e = self.table_entry()
        if e is None:
            return
        fid = self.tbl_field.itemData(index)
        if fid == NEW_TABLE:
            name, ok = frame_mod.get_text(self, "New table", "Name in the form (e.g. Hours worked):", ok="Add")
            if not ok or not name.strip():
                self.show_editor()
                return
            fid = self.new_field_id(name)
            self.new_fields.append({"id": fid, "label": name.strip(), "type": "table", "section": "fields",
                                    "options": []})
            self.table_columns[fid] = []
            e["cols"] = [None] * (len(e["xs"]) - 1)
        elif fid != e["table"]:
            ids = [c["id"] for c in self.columns_of(fid)]
            e["cols"] = (ids + [None] * len(e["xs"]))[:len(e["xs"]) - 1]
        e["table"] = fid
        self.changed()

    def on_table_renamed(self, text):
        e = self.table_entry()
        new = next((nf for nf in self.new_fields if e and nf["id"] == e["table"]), None)
        if new is not None and text.strip():
            new["label"] = text.strip()
            self.tbl_field.setItemText(max(0, self.tbl_field.findData(new["id"])), new["label"])
            item = self.list.item(self.current)
            if item:
                item.setText(self.describe(e))

    def on_rows_count(self, n, force=False):
        e = self.table_entry()
        if e is None or (n == len(e["ys"]) - 1 and not force):
            return
        top, bottom = e["ys"][0], e["ys"][-1]
        e["ys"] = [round(top + (bottom - top) * k / n, 2) for k in range(n + 1)]
        self.changed()

    def on_cols_count(self, n):
        e = self.table_entry()
        if e is None:
            return
        while len(e["xs"]) - 1 < n:  # split the widest column
            j = max(range(len(e["xs"]) - 1), key=lambda k: e["xs"][k + 1] - e["xs"][k])
            e["xs"].insert(j + 1, round((e["xs"][j] + e["xs"][j + 1]) / 2, 2))
            e["cols"].insert(j + 1, None)
            if e["align"]:
                e["align"].insert(j + 1, "left")
        while len(e["xs"]) - 1 > n:  # merge the last column into the one before
            del e["xs"][-2]
            cid = e["cols"].pop()
            if e["align"]:
                e["align"].pop()
            self.drop_column(e["table"], cid)
        self.changed()

    def drop_column(self, fid, cid):
        """Forget a column nothing on the page shows any more."""
        if cid and not any("table" in x and x["table"] == fid and cid in x["cols"] for x in self.entries):
            self.table_columns[fid] = [c for c in self.columns_of(fid) if c["id"] != cid]

    def order_columns(self, fid):
        """Keep the form's columns in the order they have on the page."""
        order = [cid for x in self.entries if x.get("table") == fid for cid in x["cols"] if cid]
        cols = self.columns_of(fid)
        cols.sort(key=lambda c: order.index(c["id"]) if c["id"] in order else len(order))

    def on_column_kind(self, index):
        e = self.table_entry()
        if e is None or self.grid_column is None:
            return
        kind, j, fid = self.col_kind.itemData(index), self.grid_column, e["table"]
        cid = e["cols"][j]
        if kind is None:
            e["cols"][j] = None
            self.drop_column(fid, cid)
        else:
            c = self.column(fid, cid) if cid else None
            if c is None:
                used = {x["id"] for x in self.columns_of(fid)} | {"sheet", "loop"}
                base, n = f"col{j + 1}", 2
                new_id = base
                while new_id in used:
                    new_id, n = f"{base}_{n}", n + 1
                c = {"id": new_id, "label": f"Column {j + 1}", "type": "text"}
                self.columns_of(fid).append(c)
                e["cols"][j] = new_id
            if kind == "formula":
                if not c.get("formula"):
                    times = [x["id"] for x in self.columns_of(fid) if x.get("type") == "time" and not x.get("formula")]
                    c["formula"] = f"hours_between({times[0]}, {times[1]})" if len(times) >= 2 else ""
                c.pop("type", None)
            else:
                c["formula"] = ""
                c["type"] = kind
        self.order_columns(fid)
        self.changed()

    def on_column_label(self, text):
        e = self.table_entry()
        if e is None or self.grid_column is None or not e["cols"][self.grid_column]:
            return
        c = self.column(e["table"], e["cols"][self.grid_column])
        c["label"] = text.strip() or c["id"]
        self.col_pick.setItemText(self.grid_column, f"{self.grid_column + 1} · {c['label']}")
        self.draw_areas()

    def on_column_formula(self, text):
        e = self.table_entry()
        if e is None or self.grid_column is None or not e["cols"][self.grid_column]:
            return
        self.column(e["table"], e["cols"][self.grid_column])["formula"] = text.strip()

    def on_moved(self):
        for item in self.view.scene().selectedItems():
            if isinstance(item, AreaItem):
                self.shift(item, item.pos().x(), item.pos().y())
        self.changed()

    def shift(self, item, dx, dy):
        e = self.entries[item.entry_index]
        if "table" in e:
            e["xs"] = [x + dx for x in e["xs"]]
            e["ys"] = [y + dy for y in e["ys"]]
        elif "mark" in e:
            e["at"] = [e["at"][0] + dx, e["at"][1] + dy]
        else:
            x0, y, x1 = e["lines"][item.line_index]
            e["lines"][item.line_index] = [x0 + dx, y + dy, x1 + dx]

    def keyPressEvent(self, event):
        steps = {Qt.Key_Left: (-1, 0), Qt.Key_Right: (1, 0), Qt.Key_Up: (0, -1), Qt.Key_Down: (0, 1)}
        items = [i for i in self.view.scene().selectedItems() if isinstance(i, AreaItem)]
        if event.key() in steps and items and self.view.hasFocus():
            k = 5 if event.modifiers() & Qt.ShiftModifier else 0.5
            dx, dy = steps[event.key()]
            for item in items:
                self.shift(item, dx * k, dy * k)
            self.changed()
            return
        super().keyPressEvent(event)

    def on_size(self, value):
        if self.table_entry() is not None and self.sender() is self.size:
            return
        if self.current is not None:
            self.entries[self.current]["size"] = value
            self.draw_areas()

    def delete_entry(self):
        if self.current is None:
            return
        removed = self.entries.pop(self.current)
        if "table" in removed and removed["table"] in {nf["id"] for nf in self.new_fields} \
                and not any(e.get("table") == removed["table"] for e in self.entries):
            self.new_fields = [nf for nf in self.new_fields if nf["id"] != removed["table"]]
            self.table_columns.pop(removed["table"], None)
        self.current = None
        self.changed()

    def delete_selected_area(self):
        items = [i for i in self.view.scene().selectedItems() if isinstance(i, AreaItem)]
        if not items:
            return
        item = items[0]
        e = self.entries[item.entry_index]
        if "lines" in e and len(e["lines"]) > 1 and item.line_index is not None:
            del e["lines"][item.line_index]
            self.changed()
        else:
            self.current = item.entry_index
            self.delete_entry()

    # --- save ---

    def save(self):
        for i, e in enumerate(self.entries):
            if "table" in e:
                columns = self.columns_of(e["table"])
                bad = next((c for c in columns if "formula" in c and c.get("formula") == "" and "type" not in c), None)
                if not any(e["cols"]) or not columns or bad is not None:
                    self.list.setCurrentRow(i)
                    frame_mod.tell(self, "Table not finished", "Say what goes in at least one column of the "
                                        "table, and give every formula column its formula.")
                    return
                continue
            key = "mark" if "mark" in e else "value"
            if not str(e.get(key, "")).strip():
                self.list.setCurrentRow(i)
                frame_mod.tell(self, "Missing value", "Choose what goes in every box (or when every check "
                                    "mark is ticked), or delete it.")
                return
        text = " ".join(str(e.get("value", e.get("mark", e.get("table", "")))) for e in self.entries)
        used_new = [nf for nf in self.new_fields if re.search(rf"\b{nf['id']}\b", text)]
        new_ids = {nf["id"] for nf in self.new_fields}
        edited = {fid: cols for fid, cols in self.table_columns.items()
                  if fid in new_ids or cols != self._original_columns(fid)}
        try:
            save_positions(self.pack.root, self.doc.id, self.entries, used_new, edited)
        except FillmateError as e:
            frame_mod.tell(self, "Cannot save", str(e))
            return
        self.pack = load_pack(self.pack.root)
        self.accept()
