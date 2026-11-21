"""Calibration tool: place where each value goes on a PDF by drawing on the page.

Scene coordinates are PDF points (1/72 inch, origin top-left), the same units pack.yaml uses.

No modes: drag over a blank line (or double-click it) to add a box, click a box to select
and move it, right-click for check marks. Values are picked by field name; the
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
    QVBoxLayout, QWidget,
)

from .. import FillmateError
from ..authoring import field_uses, remove_field, render_page, save_positions
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
         "Drag a box to move it: arrow keys nudge it, Delete removes it.",
         "Right-click for check marks. Ctrl + wheel zooms.")
INSET, PANEL_W = 12, 340
TYPES = (("Text", "text"), ("Long text", "multiline"), ("Date", "date"), ("Several days", "dates"),
         ("Time", "time"))
CUSTOM = "__custom__"
PAGE_INK = "#1b2a6b"  # the page stays white in dark mode too, so boxes keep the light ink


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
        ef.addWidget(set_role(label("Selected value"), "sec"))
        self.value_label = label("What goes here", "lbl")
        ef.addWidget(self.value_label)
        self.value = QComboBox()
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
        groups = [g for g in (self.pages, self.zoom_bar) if not g.isHidden()]
        total = sum(g.width() for g in groups) + 10 * (len(groups) - 1)
        x = mid - total // 2
        for g in groups:
            g.move(x, INSET)
            x += g.width() + 10
        self.view.setGeometry(0, INSET + 52, self.panel.x() - 4, h - INSET - 52)
        for wdg in (self.panel, self.pages, self.zoom_bar, self.cancel_btn, self.save_btn, self.win_left,
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

    def text_choices(self):
        return [(f["label"], template_for(f)) for f in self.fields() if f["type"] != "bool"] + \
               [(k.replace("_", " ").capitalize(), f"{{{{ {k} }}}}") for k in self.pack.computed]

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
        used_here = " ".join(str(e.get("value", e.get("mark", ""))) for e in self.entries)
        unused = []
        for f in self.pack.profile + self.pack.fields:
            if re.search(rf"\b{re.escape(f.id)}\b", used_here):
                continue
            try:
                if not field_uses(self.pack.root, f.id):
                    unused.append(f)
            except FillmateError:
                pass
        self.new_fields = [nf for nf in self.new_fields if re.search(rf"\b{nf['id']}\b", used_here)]
        if not unused:
            frame_mod.tell(self, "Unused fields", "Every field of this pack is used somewhere.")
            return
        names = "\n".join(f"• {f.label}" for f in unused)
        if frame_mod.ask(self, "Remove unused fields?",
                                f"These fields are not used on any document, in the email or in a rule:\n\n{names}"
                                "\n\nRemove them from the pack?") != QMessageBox.Yes:
            return
        for f in unused:
            try:
                self.pack = remove_field(self.pack.root, f.id)
            except FillmateError as e:
                frame_mod.tell(self, "Remove field", str(e))
        self.refresh_list()

    # --- page ---

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
        self.editor.setVisible(e is not None)
        self.hints.setVisible(e is None)
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

    def on_drawn(self, rect):
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
            m.add("Add a text box here", "box", lambda: self.on_drawn(
                QRectF(QPointF(point.x(), point.y() - 9), QPointF(point.x() + QUICK_WIDTH, point.y() + 3))))
            m.add("Add a check mark here", "check", lambda: self.add_mark(point))
        m.popup(self.view.mapToGlobal(self.view.mapFromScene(point)))

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

    def on_size(self, value):
        if self.current is not None:
            self.entries[self.current]["size"] = value
            self.draw_areas()

    def delete_entry(self):
        if self.current is None:
            return
        del self.entries[self.current]
        self.current = None
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

    # --- save ---

    def save(self):
        for i, e in enumerate(self.entries):
            key = "mark" if "mark" in e else "value"
            if not str(e.get(key, "")).strip():
                self.list.setCurrentRow(i)
                frame_mod.tell(self, "Missing value", "Choose what goes in every box (or when every check "
                                    "mark is ticked), or delete it.")
                return
        text = " ".join(str(e.get("value", e.get("mark", ""))) for e in self.entries)
        used_new = [nf for nf in self.new_fields if re.search(rf"\b{nf['id']}\b", text)]
        try:
            save_positions(self.pack.root, self.doc.id, self.entries, used_new)
        except FillmateError as e:
            frame_mod.tell(self, "Cannot save", str(e))
            return
        self.pack = load_pack(self.pack.root)
        self.accept()
