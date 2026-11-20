"""Calibration tool: place where each value goes on a PDF by drawing on the page.

Scene coordinates are PDF points (1/72 inch, origin top-left), the same units pack.yaml uses.
"""
import copy

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QKeySequence, QPainter, QPen, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
    QGraphicsPixmapItem, QGraphicsRectItem, QGraphicsScene, QGraphicsView, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QSpinBox, QSplitter,
    QToolButton, QVBoxLayout, QWidget,
)

from .. import FillmateError
from ..authoring import add_field, render_page, set_document_fill
from ..pack import FIELD_TYPES, load_pack
from ..templating import slug

DPI = 144
TEXT_COLOR = QColor(47, 85, 212)
MARK_COLOR = QColor(34, 181, 115)
MODES = (("select", "Select / move", "Click an area to select it, drag to move it. Arrow keys nudge it "
          "(Shift = bigger steps), Delete removes it."),
         ("text", "New text", "Drag a box over the blank line where a value goes."),
         ("line", "Add line", "Drag a box for the next line of the selected text: long values continue on it."),
         ("mark", "Check mark", "Click the middle of a box that should get an X."))


class AreaItem(QGraphicsRectItem):
    """One text line or check mark on the page, linked to its entry."""

    def __init__(self, rect, entry_index, line_index, color):
        super().__init__(rect)
        self.entry_index, self.line_index = entry_index, line_index
        self.setFlags(QGraphicsRectItem.ItemIsSelectable | QGraphicsRectItem.ItemIsMovable)
        self.setPen(QPen(color, 0.8))
        fill = QColor(color)
        fill.setAlpha(45)
        self.setBrush(QBrush(fill))

    def paint(self, painter, option, widget=None):
        painter.setPen(QPen(QColor(230, 80, 40), 1.6) if self.isSelected() else self.pen())
        painter.setBrush(self.brush())
        painter.drawRect(self.rect())


class PageView(QGraphicsView):
    drawn = Signal(QRectF)
    clicked = Signal(QPointF)
    moved = Signal()

    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setBackgroundBrush(QColor(120, 120, 120))
        self.mode = "select"
        self.origin, self.rubber = None, None
        self.background = None

    def set_mode(self, mode):
        self.mode = mode
        self.setDragMode(QGraphicsView.NoDrag)
        self.viewport().setCursor(Qt.ArrowCursor if mode == "select" else Qt.CrossCursor)

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
            scale = (self.viewport().width() - 20) / rect.width()
            self.scale(scale, scale)

    def wheelEvent(self, event):
        if event.modifiers() & Qt.ControlModifier:
            factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
            self.scale(factor, factor)
        else:
            super().wheelEvent(event)

    def mousePressEvent(self, event):
        if self.mode in ("text", "line") and event.button() == Qt.LeftButton:
            self.origin = self.mapToScene(event.position().toPoint())
            self.rubber = self.scene().addRect(QRectF(self.origin, self.origin), QPen(QColor(230, 80, 40), 0.8,
                                                                                      Qt.DashLine))
            return
        if self.mode == "mark" and event.button() == Qt.LeftButton:
            self.clicked.emit(self.mapToScene(event.position().toPoint()))
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


class NewFieldDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("New field")
        form = QFormLayout(self)
        self.label = QLineEdit()
        self.label.setPlaceholderText("e.g. Destination")
        self.ident = QLineEdit()
        self.ident.setPlaceholderText("used in templates, e.g. destination")
        self.label.textChanged.connect(lambda t: self.ident.setText(slug(t, 40).lower()))
        self.type = QComboBox()
        self.type.addItems(sorted(FIELD_TYPES))
        self.type.setCurrentText("text")
        self.section = QComboBox()
        self.section.addItem("Asked every time", "fields")
        self.section.addItem("Profile (entered once)", "profile")
        form.addRow("Label", self.label)
        form.addRow("Id", self.ident)
        form.addRow("Type", self.type)
        form.addRow("Kind", self.section)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)


class CalibrateDialog(QDialog):
    def __init__(self, parent, pack, doc_id):
        super().__init__(parent)
        self.pack = pack
        self.doc = next(d for d in pack.documents if d.id == doc_id)
        self.entries = copy.deepcopy(self.doc.fill)
        self.page_no, self.page_count = 0, 1
        self.current = None  # index of the selected entry
        self.setWindowTitle(f"Positions – {self.doc.label}")
        self.resize(1400, 900)

        self.view = PageView()
        self.view.drawn.connect(self.on_drawn)
        self.view.clicked.connect(self.on_clicked)
        self.view.moved.connect(self.on_moved)
        self.view.scene().selectionChanged.connect(self.on_scene_selection)

        left = QWidget()
        lv = QVBoxLayout(left)
        bar = QHBoxLayout()
        self.mode_group = QButtonGroup(self)
        for i, (key, text, _) in enumerate(MODES):
            b = QToolButton(text=text, checkable=True)
            b.setStyleSheet("QToolButton:checked { background: palette(highlight); color: palette(highlighted-text); "
                            "border-radius: 3px; }")
            b.setProperty("mode", key)
            self.mode_group.addButton(b, i)
            bar.addWidget(b)
        self.mode_group.idToggled.connect(lambda i, on: on and self.set_mode(MODES[i][0]))
        bar.addStretch()
        self.page_spin = QSpinBox()
        self.page_spin.setPrefix("Page ")
        self.page_spin.valueChanged.connect(self.set_page)
        bar.addWidget(self.page_spin)
        fit = QToolButton(text="Fit width")
        fit.clicked.connect(self.view.fit_width)
        bar.addWidget(fit)
        self.sample = QCheckBox("Show sample text")
        self.sample.setChecked(True)
        self.sample.toggled.connect(self.schedule_render)
        bar.addWidget(self.sample)
        lv.addLayout(bar)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        lv.addWidget(self.hint)
        lv.addWidget(self.view, 1)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.addWidget(QLabel("Values on this document"))
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self.select_entry)
        rv.addWidget(self.list, 1)
        self.editor = QGroupBox("Selected value")
        ef = QFormLayout(self.editor)
        self.value = QComboBox()
        self.value.setEditable(True)
        self.value.setInsertPolicy(QComboBox.NoInsert)
        self.value.lineEdit().setPlaceholderText("{{ field_id }} or fixed text")
        self.value.editTextChanged.connect(self.on_value_edited)
        value_row = QHBoxLayout()
        value_row.addWidget(self.value, 1)
        new_field = QPushButton("New field…")
        new_field.clicked.connect(self.new_field)
        value_row.addWidget(new_field)
        self.value_label = QLabel("Value")
        ef.addRow(self.value_label, value_row)
        self.size = QDoubleSpinBox()
        self.size.setRange(4, 36)
        self.size.setSingleStep(0.5)
        self.size.valueChanged.connect(self.on_size)
        ef.addRow("Font size", self.size)
        self.symbol = QLineEdit()
        self.symbol.setMaxLength(3)
        self.symbol.textEdited.connect(self.on_symbol)
        self.symbol_label = QLabel("Symbol")
        ef.addRow(self.symbol_label, self.symbol)
        self.coords = QLabel()
        self.coords.setTextInteractionFlags(Qt.TextSelectableByMouse)
        ef.addRow("Position", self.coords)
        delete = QPushButton("Delete value")
        delete.clicked.connect(self.delete_entry)
        ef.addRow(delete)
        rv.addWidget(self.editor)
        help_text = QLabel("Text values use {{ field_id }}; check marks use a condition such as "
                           "<code>transport == 'Train'</code> or just <code>overnight</code>. "
                           "Ctrl + mouse wheel zooms.")
        help_text.setWordWrap(True)
        rv.addWidget(help_text)

        split = QSplitter()
        split.addWidget(left)
        split.addWidget(right)
        split.setSizes([1000, 400])
        lay = QVBoxLayout(self)
        lay.addWidget(split, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

        for key in (QKeySequence.Delete, QKeySequence.Backspace):
            QShortcut(key, self.view, activated=self.delete_selected_area)
        self.render_timer = QTimer(self, singleShot=True, interval=200)
        self.render_timer.timeout.connect(self.render)

        self.refresh_suggestions()
        self.mode_group.button(1 if not self.entries else 0).setChecked(True)
        self.render()
        self.page_spin.setRange(1, self.page_count)
        self.refresh_list()
        QTimer.singleShot(0, self.view.fit_width)

    # --- helpers ---

    def labels(self):
        out = {f.id: f.label for f in self.pack.profile + self.pack.fields}
        out.update({k: k for k in self.pack.computed})
        return out

    def refresh_suggestions(self):
        text = self.value.currentText()
        self.value.blockSignals(True)
        self.value.clear()
        for f in self.pack.profile + self.pack.fields:
            if f.type == "dates":
                self.value.addItem(f"{{{{ {f.id} | dates }}}}")
            elif f.type == "date":
                self.value.addItem(f"{{{{ {f.id} | date }}}}")
            else:
                self.value.addItem(f"{{{{ {f.id} }}}}")
        for k in self.pack.computed:
            self.value.addItem(f"{{{{ {k} }}}}")
        self.value.setEditText(text)
        self.value.blockSignals(False)

    def set_mode(self, mode):
        self.view.set_mode(mode)
        self.hint.setText(next(h for k, _, h in MODES if k == mode))

    def set_page(self, value):
        self.page_no = max(0, value - 1)
        self.render()

    def schedule_render(self):
        self.render_timer.start()

    def render(self):
        try:
            png, self.page_count = render_page(self.doc.template, self.page_no, DPI,
                                               self.entries if self.sample.isChecked() else (), self.labels())
        except Exception as e:
            QMessageBox.warning(self, "Cannot show the page", str(e))
            return
        self.view.set_page(png)
        self.draw_areas()

    def draw_areas(self):
        scene = self.view.scene()
        scene.blockSignals(True)
        for item in [i for i in scene.items() if isinstance(i, AreaItem)]:
            scene.removeItem(item)
        for i, e in enumerate(self.entries):
            if "field" in e or e.get("page", 0) != self.page_no:
                continue
            if "mark" in e:
                s = e.get("size", 11)
                x, y = e["at"]
                item = AreaItem(QRectF(x - 1, y - s * 0.75, s * 0.75, s * 0.85), i, None, MARK_COLOR)
                scene.addItem(item)
                item.setSelected(i == self.current)
            else:
                s = e.get("size", 10)
                for j, (x0, y, x1) in enumerate(e["lines"]):
                    item = AreaItem(QRectF(x0, y - s * 0.8, x1 - x0, s * 1.0), i, j, TEXT_COLOR)
                    scene.addItem(item)
                    item.setSelected(i == self.current)
        scene.blockSignals(False)

    def describe(self, e):
        if "field" in e:
            return f"Form field “{e['field']}” ← {e['value']}"
        if "mark" in e:
            return f"✓ {e['mark'] or '(no condition yet)'}   · page {e.get('page', 0) + 1}"
        n = len(e["lines"])
        return f"{e['value'] or '(no value yet)'}   · {n} line{'s' if n > 1 else ''}, page {e.get('page', 0) + 1}"

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
        self.editor.setEnabled(e is not None)
        if e is None:
            self.coords.setText("")
            return
        is_mark = "mark" in e
        self.value_label.setText("Condition" if is_mark else "Value")
        self.value.blockSignals(True)
        self.value.setEditText(e["mark"] if is_mark else e["value"])
        self.value.blockSignals(False)
        self.size.blockSignals(True)
        self.size.setValue(e.get("size", 11 if is_mark else 10))
        self.size.setEnabled("field" not in e)
        self.size.blockSignals(False)
        self.symbol.setVisible(is_mark)
        self.symbol_label.setVisible(is_mark)
        if is_mark:
            self.symbol.setText(e.get("symbol", "X"))
            self.coords.setText(f"at {e['at'][0]:.1f}, {e['at'][1]:.1f}")
        elif "lines" in e:
            self.coords.setText("\n".join(f"line {k + 1}: x {a:.1f}–{c:.1f}, baseline {b:.1f}"
                                          for k, (a, b, c) in enumerate(e["lines"])))
        else:
            self.coords.setText("fillable field (no position needed)")

    def changed(self, redraw=True):
        if self.current is not None:
            item = self.list.item(self.current)
            if item:
                item.setText(self.describe(self.entries[self.current]))
        self.show_editor()
        if redraw:
            self.draw_areas()
        self.schedule_render()

    # --- events ---

    def select_entry(self, row):
        self.current = row if 0 <= row < len(self.entries) else None
        e = self.entries[self.current] if self.current is not None else None
        if e and "field" not in e and e.get("page", 0) != self.page_no:
            self.page_spin.setValue(e.get("page", 0) + 1)
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

    def on_drawn(self, rect):
        h = rect.height()
        if h < 6:  # a thin stroke along an underline
            size, baseline = 10.0, rect.bottom()
        else:
            size = max(6.0, min(14.0, round(h * 0.75 * 2) / 2))
            baseline = rect.bottom() - h * 0.2
        line = [rect.left(), baseline, rect.right()]
        e = self.entries[self.current] if self.current is not None else None
        if self.view.mode == "line":
            if not e or "lines" not in e:
                QMessageBox.information(self, "Add line", "Select a text value first, then draw its next line.")
                return
            e["lines"].append(line)
        else:
            self.entries.append({"value": "", "page": self.page_no, "lines": [line], "font": "helv",
                                 "size": size, "min_size": 7.0})
            self.current = len(self.entries) - 1
            self.refresh_list()
            self.value.setFocus()
        self.changed()

    def on_clicked(self, point):
        size = 11.0
        self.entries.append({"mark": "", "page": self.page_no, "at": [point.x() - size * 0.33, point.y() + size * 0.36],
                             "symbol": "X", "size": size})
        self.current = len(self.entries) - 1
        self.refresh_list()
        self.value.setFocus()
        self.changed()

    def on_moved(self):
        for item in self.view.scene().selectedItems():
            if isinstance(item, AreaItem):
                self.shift(item, item.pos().x(), item.pos().y())
        self.changed()

    def shift(self, item, dx, dy):
        e = self.entries[item.entry_index]
        if "mark" in e:
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

    def on_value_edited(self, text):
        if self.current is None:
            return
        e = self.entries[self.current]
        e["mark" if "mark" in e else "value"] = text
        self.changed(redraw=False)

    def on_size(self, value):
        if self.current is not None:
            self.entries[self.current]["size"] = value
            self.changed()

    def on_symbol(self, text):
        if self.current is not None:
            self.entries[self.current]["symbol"] = text or "X"
            self.changed(redraw=False)

    def delete_entry(self):
        if self.current is None:
            return
        del self.entries[self.current]
        self.current = None
        self.refresh_list()
        self.changed()

    def delete_selected_area(self):
        items = [i for i in self.view.scene().selectedItems() if isinstance(i, AreaItem)]
        if not items:
            return
        item = items[0]
        e = self.entries[item.entry_index]
        if "lines" in e and len(e["lines"]) > 1:
            del e["lines"][item.line_index]
            self.changed()
        else:
            self.current = item.entry_index
            self.delete_entry()

    def new_field(self):
        d = NewFieldDialog(self)
        if not d.exec():
            return
        try:
            self.pack = add_field(self.pack.root, d.ident.text().strip(), d.label.text().strip() or d.ident.text(),
                                  d.type.currentText(), d.section.currentData())
        except FillmateError as e:
            QMessageBox.warning(self, "New field", str(e))
            return
        fid, ftype = d.ident.text().strip(), d.type.currentText()
        self.refresh_suggestions()
        if self.current is not None and "mark" not in self.entries[self.current]:
            filt = " | dates" if ftype == "dates" else " | date" if ftype == "date" else ""
            self.value.setEditText(f"{{{{ {fid}{filt} }}}}")

    # --- save ---

    def save(self):
        for i, e in enumerate(self.entries):
            key = "mark" if "mark" in e else "value"
            if not str(e.get(key, "")).strip():
                self.list.setCurrentRow(i)
                QMessageBox.warning(self, "Missing value", "Give every area a value (or a condition for check "
                                    "marks), or delete it.")
                return
        try:
            set_document_fill(self.pack.root, self.doc.id, self.entries)
        except FillmateError as e:
            QMessageBox.warning(self, "Cannot save", str(e))
            return
        self.pack = load_pack(self.pack.root)
        self.accept()
