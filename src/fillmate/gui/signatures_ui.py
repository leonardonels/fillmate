"""Signatures in the app: the list in Settings → Profile, the picker in forms, and cutting one
out of a PDF."""
from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QHBoxLayout, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout,
    QWidget,
)

from .. import FillmateError, signatures
from . import frame
from .frame import PaperDialog
from .ink import label, set_role

THUMB = QSize(132, 44)
NONE_YET = "yes"  # what a picker keeps while there is no signature: sign as soon as there is one


def thumbnail(sig, size=THUMB):
    """The signature on a small white card: dark ink stays readable in the dark theme too."""
    pm = QPixmap(str(sig.files[0]))
    if pm.isNull():
        return QIcon()
    card = QPixmap(size)
    card.fill(Qt.transparent)
    p = QPainter(card)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#ffffff"))
    p.drawRoundedRect(QRectF(0, 0, size.width(), size.height()), 6, 6)
    inner = pm.scaled(size.width() - 8, size.height() - 6, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    p.drawPixmap((size.width() - inner.width()) // 2, (size.height() - inner.height()) // 2, inner)
    p.end()
    return QIcon(card)


class SignaturePicker(QComboBox):
    """Which of your signatures goes on the form ("" = don't sign)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setIconSize(QSize(84, 28))
        self.wanted = NONE_YET
        self.refresh()

    def refresh(self):
        self.blockSignals(True)
        self.clear()
        sigs = signatures.list_signatures()
        for sig in sigs:
            n = len(sig.files)
            self.addItem(thumbnail(sig, QSize(84, 28)), sig.name + (f"  ({n} variants)" if n > 1 else ""), sig.id)
        if not sigs:
            self.addItem("Sign once you add a signature (Settings → Profile)", NONE_YET)
        self.addItem("Don't sign", "")
        self.blockSignals(False)
        self.set(self.wanted)

    def get(self):
        return self.currentData()

    def set(self, value):
        text = "" if value in (None, False) else "yes" if value is True else str(value)
        self.wanted = text
        low = text.strip().lower()
        if low in ("", "no", "0", "false", "off", "none"):
            index = self.findData("")
        elif low in ("yes", "y", "1", "true", "on", "x", "✓"):
            index = 0  # your first signature (or the "once you add one" line)
        else:
            index = self.findData(text)
        self.setCurrentIndex(max(0, index))


class SignaturesBox(QWidget):
    """Settings → Profile: your signatures, with thumbnails."""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        v.addWidget(set_role(label("Signatures"), "lbl"))
        v.addWidget(label("Photos or scans of your signature (the paper becomes transparent), or cut out of a "
                          "PDF. Several images of one signature take turns, row after row. They stay on this "
                          "computer and are never part of a pack you share.", "muted", wrap=True))
        self.list = QListWidget()
        self.list.setIconSize(THUMB)
        self.list.setFixedHeight(150)
        self.list.currentRowChanged.connect(self._update_buttons)
        v.addWidget(self.list)
        row = QHBoxLayout()
        row.setSpacing(6)
        add = set_role(QPushButton("+ From images…"), "add")
        add.setToolTip("One or more photos or scans of the same signature")
        add.clicked.connect(self.add_images)
        row.addWidget(add)
        pdf = set_role(QPushButton("+ From a PDF…"), "add")
        pdf.clicked.connect(self.add_pdf)
        row.addWidget(pdf)
        row.addStretch()
        self.more = QPushButton("Add a variant…")
        self.more.setToolTip("Another image of the selected signature: rows take turns")
        self.more.clicked.connect(self.add_variant)
        row.addWidget(self.more)
        self.rename_btn = QPushButton("Rename…")
        self.rename_btn.clicked.connect(self.rename)
        row.addWidget(self.rename_btn)
        self.remove_btn = set_role(QPushButton("Remove"), "danger")
        self.remove_btn.clicked.connect(self.remove)
        row.addWidget(self.remove_btn)
        v.addLayout(row)
        self.refresh()

    def refresh(self, select=None):
        self.list.clear()
        for sig in signatures.list_signatures():
            n = len(sig.files)
            item = QListWidgetItem(thumbnail(sig), f"{sig.name}" + (f"\n{n} variants" if n > 1 else ""))
            item.setData(Qt.UserRole, sig.id)
            self.list.addItem(item)
            if sig.id == select:
                self.list.setCurrentItem(item)
        if self.list.count() == 0:
            empty = QListWidgetItem("No signature yet")
            empty.setFlags(Qt.NoItemFlags)
            self.list.addItem(empty)
        self._update_buttons()

    def current(self):
        item = self.list.currentItem()
        return item.data(Qt.UserRole) if item else None

    def _update_buttons(self, *_args):
        on = self.current() is not None
        for b in (self.more, self.rename_btn, self.remove_btn):
            b.setEnabled(on)

    def _done(self, sig):
        self.refresh(select=sig.id if sig else None)
        self.changed.emit()

    def _ask_name(self, default):
        name, ok = frame.get_text(self.window(), "Signature", "Name (e.g. Full signature, Initials):",
                                  text=default, ok="Save")
        return name.strip() if ok and name.strip() else None

    def add_images(self, to=None):
        paths, _ = QFileDialog.getOpenFileNames(self.window(), "Images of your signature", str(Path.home()),
                                                "Images (*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff)")
        if not paths:
            return
        name = "" if to else self._ask_name("My signature")
        if name is None:
            return
        try:
            self._done(signatures.add_images(paths, name, to=to))
        except FillmateError as e:
            frame.tell(self.window(), "Signature", str(e))

    def add_variant(self):
        if self.current():
            self.add_images(to=self.current())

    def add_pdf(self):
        path, _ = QFileDialog.getOpenFileName(self.window(), "PDF with your signature", str(Path.home()),
                                              "PDF (*.pdf)")
        if not path:
            return
        d = CutFromPdfDialog(self.window(), path)
        if not d.exec() or d.rect is None:
            return
        name = self._ask_name("My signature")
        if name is None:
            return
        try:
            self._done(signatures.add_pdf(path, d.page_no, d.rect, name))
        except FillmateError as e:
            frame.tell(self.window(), "Signature", str(e))

    def rename(self):
        sid = self.current()
        sig = signatures.get(sid) if sid else None
        if sig is None:
            return
        name = self._ask_name(sig.name)
        if name:
            signatures.rename(sid, name)
            self._done(sig)

    def remove(self):
        sid = self.current()
        sig = signatures.get(sid) if sid else None
        if sig is None:
            return
        if frame.ask(self.window(), "Remove signature?",
                     f"Remove “{sig.name}” from this computer? Forms that use it will no longer be signed with "
                     "it.") != QMessageBox.Yes:
            return
        signatures.remove(sid)
        self._done(None)


class CutFromPdfDialog(PaperDialog):
    """Draw a box around the signature on a PDF page."""

    def __init__(self, parent, path):
        super().__init__(parent, "Cut out your signature")
        from ..authoring import render_page
        from .calibrate import DPI, PageView

        self.path, self.page_no, self.page_count, self.rect = path, 0, 1, None
        self._render_page = render_page
        self.body.addWidget(label("Drag a box around the signature. Only what is inside the box is kept.",
                                  "muted", wrap=True))
        self.view = PageView()
        self.view.drawn.connect(self.on_drawn)
        self.dpi = DPI
        self.body.addWidget(self.view, 1)
        nav = QHBoxLayout()
        self.prev_btn = QPushButton("‹ Previous page")
        self.prev_btn.clicked.connect(lambda: self.go(self.page_no - 1))
        self.next_btn = QPushButton("Next page ›")
        self.next_btn.clicked.connect(lambda: self.go(self.page_no + 1))
        self.where = label("", "lbl")
        for w in (self.prev_btn, self.where, self.next_btn):
            nav.addWidget(w)
        nav.addStretch()
        self.body.addLayout(nav)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        self.ok = set_role(QPushButton("Use this"), "ink")
        self.ok.setEnabled(False)
        self.ok.clicked.connect(self.accept)
        self.buttons(cancel, self.ok)
        self.resize(900, 760)
        self.box = None
        self.go(0)
        QTimer.singleShot(0, self.view.fit_width)

    def go(self, index):
        try:
            png, self.page_count = self._render_page(self.path, max(0, index), self.dpi)
        except Exception as e:
            frame.tell(self, "Cannot show the page", str(e))
            return
        self.page_no = max(0, min(index, self.page_count - 1))
        self.view.set_page(png)
        self.box, self.rect = None, None
        self.ok.setEnabled(False)
        self.where.setText(f"Page {self.page_no + 1} of {self.page_count}")
        self.prev_btn.setEnabled(self.page_no > 0)
        self.next_btn.setEnabled(self.page_no < self.page_count - 1)
        for w in (self.prev_btn, self.next_btn, self.where):
            w.setVisible(self.page_count > 1)

    def on_drawn(self, rect):
        if self.box is not None:
            self.view.scene().removeItem(self.box)
        pen = QPen(QColor("#1b2a6b"), 1.6)
        pen.setCosmetic(True)
        self.box = self.view.scene().addRect(QRectF(rect), pen)
        self.rect = (rect.left(), rect.top(), rect.right(), rect.bottom())
        self.ok.setEnabled(rect.width() > 4 and rect.height() > 4)
