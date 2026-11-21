"""The window frame: no system title bar, round corners, and window buttons where each platform expects them.

- macOS keeps the real traffic lights; the window content runs under the title bar (Qt 6.9+).
- Windows and Linux are frameless. Moving and resizing are handed to the system
  (QWindow.startSystemMove/Resize), so snapping and tiling keep working.
- Linux shows the buttons the desktop asks for (GNOME's button-layout, KDE's title bar settings).
"""
import configparser
import shutil
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QPainter
from PySide6.QtWidgets import QAbstractButton, QApplication, QDialog, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from . import ink
from .ink import T, GlassPanel, glass_path, icon, paint_hover

PLATFORM = "mac" if sys.platform == "darwin" else "windows" if sys.platform.startswith("win") else "linux"
# Linux: concentric with the sidebar (its 24 px radius + the 12 px gap); mac and Windows round corners themselves
RADIUS = {"mac": 0, "windows": 0, "linux": 36}
EDGE = 6  # px of invisible resize band along each edge
MAC_LIGHTS = 36  # room the traffic lights need at the top of the sidebar
ALL = ["minimize", "maximize", "close"]


def _gnome_layout():
    if not shutil.which("gsettings"):
        return None
    try:
        out = subprocess.run(["gsettings", "get", "org.gnome.desktop.wm.preferences", "button-layout"],
                             capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    text = out.stdout.strip().strip("'\"")
    left, _, right = text.partition(":")
    pick = lambda part: [b for b in part.split(",") if b in ALL]
    return pick(left), pick(right)


def _kde_layout():
    rc = Path.home() / ".config" / "kwinrc"
    letters = {"I": "minimize", "A": "maximize", "X": "close"}
    left, right = "M", "IAX"  # KDE's defaults
    if rc.is_file():
        cp = configparser.ConfigParser(strict=False, interpolation=None)
        try:
            cp.read(rc)
            sec = cp["org.kde.kdecoration2"] if "org.kde.kdecoration2" in cp else {}
            left, right = sec.get("ButtonsOnLeft", left), sec.get("ButtonsOnRight", right)
        except (configparser.Error, KeyError):
            pass
    pick = lambda s: [letters[c] for c in s if c in letters]
    return pick(left), pick(right)


def button_layout(dialog=False):
    """(left, right) window buttons for this platform and desktop."""
    if PLATFORM == "mac":
        return [], []  # the real traffic lights
    if PLATFORM == "windows":
        left, right = [], list(ALL)
    else:
        import os

        desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
        layout = _kde_layout() if "kde" in desktop else _gnome_layout()
        left, right = layout if layout and (layout[0] or layout[1]) else ([], list(ALL))
    if dialog:  # dialogs only close
        left, right = [b for b in left if b == "close"], [b for b in right if b == "close"]
        if not left and not right:
            right = ["close"]
    return left, right


def set_windows_round_corners(widget):
    """Ask Windows 11 to round this frameless window's corners (no effect on older Windows)."""
    try:
        import ctypes

        pref = ctypes.c_int(2)  # DWMWCP_ROUND
        ctypes.windll.dwmapi.DwmSetWindowAttribute(int(widget.winId()), 33, ctypes.byref(pref), ctypes.sizeof(pref))
    except Exception:
        pass


class WindowButton(GlassPanel):
    """One round glass window button (Linux), or one of the three in the Windows capsule."""

    def __init__(self, parent, kind, round_button=True):
        # small buttons need no shadow: the rim is enough
        super().__init__(parent, radius=16 if round_button else 18, bezel=12, shadow=False)
        self.kind, self.round_button = kind, round_button
        self.hoverable = True
        self.setFixedSize(32, 32)
        self.setCursor(Qt.ArrowCursor)
        self.setToolTip({"minimize": "Minimize", "maximize": "Maximize", "close": "Close"}[kind])
        self.setAccessibleName(self.toolTip())
        self.active = True
        self._down = False

    def mousePressEvent(self, event):
        self._down = event.button() == Qt.LeftButton

    def mouseReleaseEvent(self, event):
        if self._down and self.rect().contains(event.position().toPoint()):
            win = self.window()
            if self.kind == "minimize":
                win.showMinimized()
            elif self.kind == "maximize":
                win.showNormal() if win.isMaximized() else win.showMaximized()
            else:
                win.close()
        self._down = False

    def paint_content(self, p):
        name = {"minimize": "minus", "close": "close"}.get(self.kind)
        if self.kind == "maximize":
            name = "restore" if self.window().isMaximized() else "square"
        color = QColor(T["label"] if self.active else T["label2"])
        if not self.active:
            color.setAlpha(150)
        icon(name, color.name(), 14, 1.7).paint(p, QRect(9, 9, 14, 14))


class WindowsCaption(QWidget):
    """The window buttons in one glass capsule, like the toolbar's other groups.
    On Windows, close turns Windows red on hover."""

    RED = "#c42b1c"

    def __init__(self, parent, buttons):
        super().__init__(parent)
        self.red_close = PLATFORM == "windows"
        self.glass = GlassPanel(parent, radius=22)
        self.glass.stackUnder(self)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(0)
        self.buttons = []
        for kind in buttons:
            b = _CaptionButton(self, kind)
            lay.addWidget(b)
            self.buttons.append(b)
        self.setFixedSize(8 + 40 * len(buttons), 44)

    def moveEvent(self, event):
        super().moveEvent(event)
        self.glass.setGeometry(self.geometry())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.glass.setGeometry(self.geometry())

    def showEvent(self, event):
        super().showEvent(event)
        self.glass.show()
        self.glass.setGeometry(self.geometry())
        self.glass.stackUnder(self)

    def raise_(self):
        self.glass.raise_()
        super().raise_()


class _CaptionButton(QAbstractButton):
    def __init__(self, parent, kind):
        super().__init__(parent)
        self.kind = kind
        self.active = True
        self.setFixedSize(40, 36)
        self.setCursor(Qt.ArrowCursor)
        self.setToolTip({"minimize": "Minimize", "maximize": "Maximize", "close": "Close"}[kind])
        self.setAccessibleName(self.toolTip())
        self.setAttribute(Qt.WA_Hover)
        self.clicked.connect(self._act)

    def _act(self):
        win = self.window()
        if self.kind == "minimize":
            win.showMinimized()
        elif self.kind == "maximize":
            win.showNormal() if win.isMaximized() else win.showMaximized()
        else:
            win.close()

    def enterEvent(self, e):
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        hot = self.underMouse()
        red = hot and self.kind == "close" and self.parentWidget().red_close
        if red:
            p.fillPath(glass_path(r, 18), QColor(WindowsCaption.RED))
        elif hot:
            paint_hover(p, glass_path(r, 18))
        name = {"minimize": "minus", "close": "close"}[self.kind] if self.kind != "maximize" else (
            "restore" if self.window().isMaximized() else "square")
        color = "#ffffff" if red else (T["label"] if self.active else T["label2"])
        icon(name, color, 18, 1.8).paint(p, QRect(int(r.center().x()) - 9, 9, 18, 18))  # like the toolbar icons


class Frame(QObject):
    """Makes `window` frameless (or full-size on macOS) and handles moving, resizing and the buttons.

    drag_zone(widget, pos_in_window) says whether a press there should move the window.
    """

    def __init__(self, window, drag_zone, dialog=False):
        super().__init__(window)
        self.window, self.drag_zone = window, drag_zone
        self.left, self.right = button_layout(dialog)
        self._cursor_edges = None
        if PLATFORM == "mac":
            window.setWindowFlag(Qt.ExpandedClientAreaHint, True)
            window.setWindowFlag(Qt.NoTitleBarBackgroundHint, True)
        else:
            window.setWindowFlag(Qt.FramelessWindowHint, True)
            if PLATFORM == "linux":
                window.setAttribute(Qt.WA_TranslucentBackground)  # transparent outside the round corners
        QApplication.instance().installEventFilter(self)
        self.timer = QTimer(self, interval=60)
        self.timer.timeout.connect(self._track_cursor)
        self.timer.start()

    # --- buttons ---

    def make_buttons(self, parent, side):
        """The buttons on one side, in one glass capsule like the toolbar's other groups."""
        kinds = self.left if side == "left" else self.right
        return WindowsCaption(parent, kinds) if kinds else None

    def mac_top(self):
        """Extra room at the top of the sidebar for the traffic lights."""
        return MAC_LIGHTS if PLATFORM == "mac" else 0

    # --- shape ---

    def radius(self):
        w = self.window
        if w.isMaximized() or w.isFullScreen():
            return 0
        return RADIUS[PLATFORM]

    def overlay(self, parent):
        """A see-through layer on top of everything that trims the window to its round corners."""
        self._overlay = FrameOverlay(parent, self.radius)
        return self._overlay

    def after_show(self):
        if PLATFORM == "windows":
            set_windows_round_corners(self.window)

    # --- moving and resizing ---

    def _edges(self, global_pos):
        w = self.window
        if PLATFORM == "mac" or w.isMaximized() or w.isFullScreen():
            return Qt.Edges()
        p = w.mapFromGlobal(global_pos)
        r = w.rect()
        if not r.adjusted(-1, -1, 1, 1).contains(p):
            return Qt.Edges()
        e = Qt.Edges()
        if p.x() < EDGE:
            e |= Qt.LeftEdge
        if p.x() >= r.width() - EDGE:
            e |= Qt.RightEdge
        if p.y() < EDGE:
            e |= Qt.TopEdge
        if p.y() >= r.height() - EDGE:
            e |= Qt.BottomEdge
        return e

    def _track_cursor(self):
        if not self.window.isActiveWindow():
            return
        edges = self._edges(QCursor.pos())
        if edges == self._cursor_edges:
            return
        self._cursor_edges = edges
        shapes = {Qt.LeftEdge: Qt.SizeHorCursor, Qt.RightEdge: Qt.SizeHorCursor, Qt.TopEdge: Qt.SizeVerCursor,
                  Qt.BottomEdge: Qt.SizeVerCursor, Qt.LeftEdge | Qt.TopEdge: Qt.SizeFDiagCursor,
                  Qt.RightEdge | Qt.BottomEdge: Qt.SizeFDiagCursor, Qt.RightEdge | Qt.TopEdge: Qt.SizeBDiagCursor,
                  Qt.LeftEdge | Qt.BottomEdge: Qt.SizeBDiagCursor}
        while QApplication.overrideCursor() is not None and getattr(self, "_overriding", False):
            QApplication.restoreOverrideCursor()
            self._overriding = False
        if edges in shapes:
            QApplication.setOverrideCursor(shapes[edges])
            self._overriding = True

    def eventFilter(self, obj, event):
        t = event.type()
        if t not in (QEvent.MouseButtonPress, QEvent.MouseButtonDblClick, QEvent.WindowStateChange,
                     QEvent.WindowActivate, QEvent.WindowDeactivate):
            return False
        if not isinstance(obj, QWidget) or obj.window() is not self.window:
            return False
        if t in (QEvent.WindowStateChange, QEvent.WindowActivate, QEvent.WindowDeactivate):
            if obj is self.window:
                self._state_changed()
            return False
        if event.button() != Qt.LeftButton:
            return False
        gp = event.globalPosition().toPoint()
        handle = self.window.windowHandle()
        if t == QEvent.MouseButtonPress:
            edges = self._edges(gp)
            if edges and handle is not None:
                handle.startSystemResize(edges)
                return True
            if self.drag_zone(obj, self.window.mapFromGlobal(gp)) and handle is not None:
                handle.startSystemMove()
                return True
        elif t == QEvent.MouseButtonDblClick and self.drag_zone(obj, self.window.mapFromGlobal(gp)):
            w = self.window
            w.showNormal() if w.isMaximized() else w.showMaximized()
            return True
        return False

    def _state_changed(self):
        active = self.window.isActiveWindow()
        for b in self.window.findChildren(WindowButton) + self.window.findChildren(_CaptionButton):
            b.active = active
            b.update()
        for paper in self.window.findChildren(ink.DotGrid) + self.window.findChildren(FrameOverlay):
            paper.update()
        self.window.update()

    def adopt(self, layer, *boxes):
        """Register the window buttons' glass with a GlassLayer so it bends what is behind it."""
        for box in boxes:
            if box is None:
                continue
            layer.add(box.glass)


class FrameOverlay(QWidget):
    """Cuts everything to the window's round corners and draws its hairline edge.

    It sits on top of all other widgets and takes no clicks. On a translucent window,
    painting the corners with the Clear mode empties them whatever was drawn below
    (a document scrolled to the edge, a sidebar shadow)."""

    def __init__(self, parent, radius):
        super().__init__(parent)
        self.radius = radius
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_TranslucentBackground)

    def paintEvent(self, _e):
        r = self.radius()
        if not r:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        from PySide6.QtGui import QPainterPath
        outer = QPainterPath()
        outer.addRect(QRectF(self.rect()))
        shape = glass_path(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), r)
        p.setCompositionMode(QPainter.CompositionMode_Clear)
        p.fillPath(outer.subtracted(shape), Qt.transparent)
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)
        p.setPen(QColor(0, 0, 0, 56))
        p.drawPath(shape)


def is_passive(widget):
    """True for widgets that do nothing on click (so a press there may move the window)."""
    from PySide6.QtWidgets import QAbstractScrollArea, QComboBox, QLineEdit, QPlainTextEdit

    active = (QAbstractButton, QLineEdit, QPlainTextEdit, QComboBox, QAbstractScrollArea, ink.GlassButton,
              ink.GlassSegmented, ink.GlassGroup, ink.GlassMenu, WindowButton, ink.CircleChoice, ink.TickChoice)
    w = widget
    while w is not None:
        if isinstance(w, active):
            return False
        w = w.parentWidget()
    return True


# --- messages ----------------------------------------------------------------

class Message(QDialog):
    """A question or a notice on the app's own paper, in place of the system's message box."""

    def __init__(self, parent, title, text, ok="OK", cancel=None, danger=False, field=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.frame = Frame(self, lambda w, pos: is_passive(w), dialog=True)
        if PLATFORM == "linux":
            self.setStyleSheet("QDialog { background: transparent; }")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        paper = ink.DotGrid()
        paper.radius = self.frame.radius
        outer.addWidget(paper)
        lay = QVBoxLayout(paper)
        lay.setContentsMargins(24, 20, 24, 18)
        lay.setSpacing(12)
        lay.addWidget(ink.set_role(ink.label(title), "h1"))
        body = ink.label(text, "lbl" if field is not None else None, wrap=True)
        body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        body.setMinimumWidth(380)
        lay.addWidget(body)
        if field is not None:  # a prompt: what the person types or picks, right under its label
            lay.addWidget(field)
            field.setFocus()
        lay.addSpacing(4)
        row = QHBoxLayout()
        row.addStretch()
        if cancel:
            no = QPushButton(cancel)
            no.clicked.connect(self.reject)
            no.setMinimumHeight(36)
            row.addWidget(no)
        yes = ink.set_role(QPushButton(ok), "danger" if danger else "ink")
        yes.setMinimumHeight(36)
        yes.setDefault(True)
        yes.clicked.connect(self.accept)
        row.addWidget(yes)
        lay.addLayout(row)
        self.edge = self.frame.overlay(self)
        self.adjustSize()
        self.setFixedWidth(max(440, self.sizeHint().width()))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.edge.setGeometry(self.rect())
        self.edge.raise_()

    def showEvent(self, event):
        super().showEvent(event)
        self.frame.after_show()


DANGER = ("Remove", "Delete")


def ask(parent, title, text, *_args):
    """Drop-in for QMessageBox.question: returns QMessageBox.Yes or No. The button says what happens
    ("Remove", "Replace"…), taken from the title."""
    from PySide6.QtWidgets import QMessageBox

    verb = title.split()[0].rstrip("?") if title.endswith("?") else "OK"
    m = Message(parent, title, text, ok=verb, cancel="Cancel", danger=verb in DANGER)
    return QMessageBox.Yes if m.exec() else QMessageBox.No


def tell(parent, title, text, *_args):
    """Drop-in for QMessageBox.warning / information."""
    Message(parent, title, text).exec()


def get_text(parent, title, prompt, text="", ok="OK"):
    """Drop-in for QInputDialog.getText: returns (text, accepted)."""
    from PySide6.QtWidgets import QLineEdit

    field = QLineEdit(text)
    field.selectAll()
    m = Message(parent, title, prompt.rstrip(":"), ok=ok, cancel="Cancel", field=field)
    accepted = bool(m.exec())
    return field.text(), accepted


def get_item(parent, title, prompt, items, current=0, _editable=False, ok="OK"):
    """Drop-in for QInputDialog.getItem: returns (item, accepted)."""
    from PySide6.QtWidgets import QComboBox

    field = QComboBox()
    field.addItems(items)
    field.setCurrentIndex(current)
    m = Message(parent, title, prompt.rstrip(":"), ok=ok, cancel="Cancel", field=field)
    accepted = bool(m.exec())
    return field.currentText(), accepted
