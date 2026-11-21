"""Shared GUI pieces: background work, theme, page previews."""
import shutil
import subprocess
import sys
import threading
import traceback

from PySide6.QtCore import QObject, Qt, Signal, Slot
from PySide6.QtGui import QColor, QPalette, QPixmap
from PySide6.QtWidgets import (
    QApplication, QGraphicsDropShadowEffect, QLabel, QScrollArea, QVBoxLayout, QWidget,
)

from .. import FillmateError

THEMES = (("Automatic theme", "auto"), ("Light theme", "light"), ("Dark theme", "dark"))
GNOME_SCHEME = ["org.gnome.desktop.interface", "color-scheme"]


class Bridge(QObject):
    """Runs work in a thread and delivers the result on the UI thread.

    One QObject that lives as long as the window: no Qt object is created or
    destroyed in worker threads.
    """
    call = Signal(object, object)

    def __init__(self):
        super().__init__()
        self.call.connect(self._dispatch)

    @Slot(object, object)
    def _dispatch(self, fn, arg):
        fn(arg)

    def run(self, fn, on_done, on_error):
        def worker():
            try:
                result = fn()
            except FillmateError as e:
                callback, arg = on_error, str(e)
            except Exception as e:
                traceback.print_exc()
                callback, arg = on_error, f"Unexpected error: {e}"
            else:
                callback, arg = on_done, result
            try:
                self.call.emit(callback, arg)
            except RuntimeError:
                pass  # window closed while the work was running

        threading.Thread(target=worker, daemon=True).start()


# --- theme --------------------------------------------------------------

def gnome_available():
    return sys.platform.startswith("linux") and shutil.which("gsettings") is not None


def system_is_dark(app):
    # pip's PySide6 does not read GNOME's setting (always reports Light), so ask gsettings.
    if gnome_available():
        try:
            out = subprocess.run(["gsettings", "get", *GNOME_SCHEME], capture_output=True, text=True, timeout=2)
            if out.returncode == 0:
                return "dark" in out.stdout
        except (OSError, subprocess.SubprocessError):
            pass
    return app.styleHints().colorScheme() == Qt.ColorScheme.Dark


def dark_palette():
    p = QPalette()
    colors = {
        QPalette.Window: "#262626", QPalette.WindowText: "#e8e8e8", QPalette.Base: "#1c1c1c",
        QPalette.AlternateBase: "#2a2a2a", QPalette.ToolTipBase: "#2a2a2a", QPalette.ToolTipText: "#e8e8e8",
        QPalette.Text: "#e8e8e8", QPalette.Button: "#333333", QPalette.ButtonText: "#e8e8e8",
        QPalette.BrightText: "#ff6b6b", QPalette.Link: "#7aa7ff", QPalette.Highlight: "#3d6fc4",
        QPalette.HighlightedText: "#ffffff", QPalette.PlaceholderText: "#8c8c8c",
        QPalette.Light: "#3a3a3a", QPalette.Midlight: "#333333", QPalette.Mid: "#2a2a2a",
        QPalette.Dark: "#181818", QPalette.Shadow: "#000000",
    }
    for role, color in colors.items():
        p.setColor(role, QColor(color))
    for role in (QPalette.Text, QPalette.ButtonText, QPalette.WindowText):
        p.setColor(QPalette.Disabled, role, QColor("#777777"))
    return p


def apply_theme(choice):
    """Apply the palette and the ink-and-paper stylesheet; return True when dark."""
    from . import ink

    app = QApplication.instance()
    dark = choice == "dark" or (choice == "auto" and system_is_dark(app))
    ink.set_dark(dark)
    palette = dark_palette() if dark else app.style().standardPalette()
    for role, key in ((QPalette.Window, "ground"), (QPalette.Base, "paper"), (QPalette.Text, "label"),
                      (QPalette.WindowText, "label"), (QPalette.PlaceholderText, "label2"),
                      (QPalette.Highlight, "hl"), (QPalette.Link, "ink")):
        palette.setColor(role, QColor(ink.T[key]))
    palette.setColor(QPalette.HighlightedText, QColor(ink.T["label"]))
    app.setPalette(palette)
    app.setStyleSheet(ink.stylesheet())
    return dark



# --- preview ------------------------------------------------------------

class Preview(QScrollArea):
    """Rendered pages stacked vertically, scaled to the available width.

    The pages are paper lying on the window's dot grid. New pages open already
    tucked under the toolbar's glass (TUCK), so the glass has the page to bend
    from the start; scrolling up shows the page's top clear of it.
    """
    changed = Signal()
    zoomed = Signal(float)
    MAX_W = 640
    ZOOMS = (0.5, 0.67, 0.8, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0)
    TUCK = 52  # px scrolled at the start: the page's top edge sits just under the top of the tab bar

    def __init__(self, placeholder):
        super().__init__()
        self.zoom = 1.0  # 1 = fit the page to the column (up to MAX_W)
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.viewport().setAutoFillBackground(False)
        self.inner = QWidget()
        self.inner.setAutoFillBackground(False)
        self.box = QVBoxLayout(self.inner)
        self.box.setContentsMargins(12, 76, 12, 32)
        self.box.setSpacing(24)
        self.box.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self.setWidget(self.inner)
        self.pixmaps, self.labels = [], []
        self._tuck = False
        self.verticalScrollBar().rangeChanged.connect(lambda *_: self._apply_tuck())
        self.message(placeholder)

    def _apply_tuck(self):
        bar = self.verticalScrollBar()
        if self._tuck and bar.maximum() >= self.TUCK:
            self._tuck = False
            bar.setValue(self.TUCK)

    def _clear(self):
        while self.box.count():
            w = self.box.takeAt(0).widget()
            if w:
                __import__("fillmate.gui.ink", fromlist=["discard"]).discard(w)
        self.pixmaps, self.labels = [], []

    def message(self, text):
        self._clear()
        label = QLabel(text)
        label.setTextFormat(Qt.PlainText)
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignCenter)
        label.setProperty("role", "muted")
        label.setContentsMargins(0, 120, 0, 0)
        self.box.addWidget(label)
        self.changed.emit()

    def set_pages(self, pngs):
        self._clear()
        for data in pngs:
            pm = QPixmap()
            pm.loadFromData(data, "PNG")
            label = QLabel()
            label.setAlignment(Qt.AlignHCenter)
            shadow = QGraphicsDropShadowEffect(label)
            shadow.setBlurRadius(40)
            shadow.setOffset(0, 14)
            shadow.setColor(QColor(30, 30, 20, 60))
            label.setGraphicsEffect(shadow)
            self.box.addWidget(label)
            self.pixmaps.append(pm)
            self.labels.append(label)
        self._rescale()
        self._tuck = True  # applied once the scroll range has room for it (after layout)
        self._apply_tuck()
        self.changed.emit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._rescale()

    def _rescale(self):
        width = max(200, min(self.MAX_W, self.viewport().width() - 40)) * self.zoom
        dpr = self.devicePixelRatioF()
        for pm, label in zip(self.pixmaps, self.labels):
            scaled = pm.scaledToWidth(int(width * dpr), Qt.SmoothTransformation)
            scaled.setDevicePixelRatio(dpr)
            label.setPixmap(scaled)

    def has_pages(self):
        return bool(self.pixmaps)

    def set_zoom(self, zoom, anchor=None):
        """Zoom the pages, keeping the point under `anchor` (viewport coordinates) in place."""
        zoom = max(self.ZOOMS[0], min(self.ZOOMS[-1], zoom))
        if not self.pixmaps or abs(zoom - self.zoom) < 1e-3:
            return
        anchor = anchor or self.viewport().rect().center()
        h, v = self.horizontalScrollBar(), self.verticalScrollBar()
        fx = (h.value() + anchor.x()) / max(1, self.inner.width())
        fy = (v.value() + anchor.y()) / max(1, self.inner.height())
        self.zoom = zoom
        self._rescale()
        self.inner.adjustSize()
        h.setValue(int(fx * self.inner.width() - anchor.x()))
        v.setValue(int(fy * self.inner.height() - anchor.y()))
        self.zoomed.emit(zoom)
        self.changed.emit()

    def zoom_in(self):
        self.set_zoom(next((z for z in self.ZOOMS if z > self.zoom + 1e-3), self.ZOOMS[-1]))

    def zoom_out(self):
        self.set_zoom(next((z for z in reversed(self.ZOOMS) if z < self.zoom - 1e-3), self.ZOOMS[0]))

    def wheelEvent(self, event):
        if event.modifiers() & Qt.ControlModifier:
            step = 1.12 if event.angleDelta().y() > 0 else 1 / 1.12
            self.set_zoom(self.zoom * step, event.position().toPoint())
            event.accept()
        else:
            super().wheelEvent(event)
