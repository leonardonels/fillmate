"""Shared GUI pieces: background work, theme, page previews."""
import shutil
import subprocess
import sys
import threading
import traceback

from PySide6.QtCore import QObject, Qt, Signal, Slot
from PySide6.QtGui import QColor, QPalette, QPixmap
from PySide6.QtWidgets import QApplication, QLabel, QScrollArea, QVBoxLayout, QWidget

from .. import FillmateError

THEMES = (("Automatic theme", "auto"), ("Light theme", "light"), ("Dark theme", "dark"))
# Banner colours: (background, text) for light / dark.
BANNERS = {
    "test": {False: ("#ffe3df", "#8a1f11"), True: ("#45201b", "#ffb4a8")},
    "warn": {False: ("#fff4d6", "#6b4c00"), True: ("#3d3216", "#f3d68a")},
}
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
    """Apply the palette; return True when dark."""
    app = QApplication.instance()
    dark = choice == "dark" or (choice == "auto" and system_is_dark(app))
    app.setPalette(dark_palette() if dark else app.style().standardPalette())
    return dark


def banner_style(kind, dark, bold=False, radius=0):
    bg, fg = BANNERS[kind][dark]
    weight = "font-weight:600;" if bold else ""
    return f"background:{bg}; color:{fg}; {weight} padding:8px 14px; border-radius:{radius}px;"


# --- preview ------------------------------------------------------------

class Preview(QScrollArea):
    """Rendered pages stacked vertically, scaled to the available width."""

    def __init__(self, placeholder):
        super().__init__()
        self.setWidgetResizable(True)
        self.inner = QWidget()
        self.box = QVBoxLayout(self.inner)
        self.box.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self.setWidget(self.inner)
        self.pixmaps, self.labels = [], []
        self.message(placeholder)

    def _clear(self):
        while self.box.count():
            w = self.box.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.pixmaps, self.labels = [], []

    def message(self, text):
        self._clear()
        label = QLabel(text)
        label.setTextFormat(Qt.PlainText)
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignCenter)
        self.box.addWidget(label)

    def set_pages(self, pngs):
        self._clear()
        for data in pngs:
            pm = QPixmap()
            pm.loadFromData(data, "PNG")
            label = QLabel()
            label.setAlignment(Qt.AlignHCenter)
            self.box.addWidget(label)
            self.pixmaps.append(pm)
            self.labels.append(label)
        self._rescale()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._rescale()

    def _rescale(self):
        width = max(200, self.viewport().width() - 24)
        for pm, label in zip(self.pixmaps, self.labels):
            label.setPixmap(pm.scaledToWidth(width, Qt.SmoothTransformation))
