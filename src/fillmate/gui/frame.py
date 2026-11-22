"""The window frame: no system title bar, round corners, and window buttons where each platform expects them.

- macOS keeps the real traffic lights; the window content runs under the title bar (Qt 6.9+). A window can
  ask for them to sit centred on its toolbar row instead of in the title bar's corner (mac_lights).
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


def hide_mac_title(window):
    """Hide the window title the title bar would draw over the content (the window keeps its name for the
    Dock and Mission Control). Best effort, like place_mac_lights."""
    if PLATFORM != "mac":
        return
    try:
        import ctypes
        import ctypes.util
        from ctypes import c_long, c_void_p

        objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
        objc.sel_registerName.restype = c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]
        msg = ctypes.cast(objc.objc_msgSend, c_void_p).value

        def send(restype, obj, sel, *args):
            proto = ctypes.CFUNCTYPE(restype, c_void_p, c_void_p, *[type(a) for a in args])
            return proto(msg)(obj, objc.sel_registerName(sel.encode()), *args)

        nswindow = send(c_void_p, c_void_p(int(window.winId())), "window")
        if nswindow:
            send(None, c_void_p(nswindow), "setTitleVisibility:", c_long(1))  # NSWindowTitleHidden
    except Exception:  # noqa: BLE001 - cosmetic
        return


def place_mac_lights(window, center_y, first_x, spacing=20):
    """Move the real traffic lights so their centres sit at (first_x + i * spacing, center_y), in the
    window's points: centred on the toolbar row, as a native toolbar would put them. AppKit lays them out
    again on resizes and full screen, so Frame re-applies this then. Best effort: if anything here fails
    the lights stay where macOS put them."""
    if PLATFORM != "mac" or window.isFullScreen():
        return
    try:
        import ctypes
        import ctypes.util
        import platform
        from ctypes import c_double, c_long, c_void_p

        class Point(ctypes.Structure):
            _fields_ = [("x", c_double), ("y", c_double)]

        class Size(ctypes.Structure):
            _fields_ = [("width", c_double), ("height", c_double)]

        class Rect(ctypes.Structure):
            _fields_ = [("origin", Point), ("size", Size)]

        objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
        objc.sel_registerName.restype = c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]

        def send(restype, obj, sel, *args):
            # Intel returns structs this large through objc_msgSend_stret; Apple Silicon through objc_msgSend.
            fn = objc.objc_msgSend_stret if restype is Rect and platform.machine() == "x86_64" else objc.objc_msgSend
            proto = ctypes.CFUNCTYPE(restype, c_void_p, c_void_p, *[type(a) for a in args])
            return proto(ctypes.cast(fn, c_void_p).value)(obj, objc.sel_registerName(sel.encode()), *args)

        nswindow = send(c_void_p, c_void_p(int(window.winId())), "window")
        buttons = [send(c_void_p, c_void_p(nswindow), "standardWindowButton:", c_long(i)) for i in range(3)]
        if not nswindow or not all(buttons):
            return
        container = send(c_void_p, send(c_void_p, c_void_p(buttons[0]), "superview"), "superview")
        frame_view = send(c_void_p, c_void_p(container), "superview")
        if not container or not frame_view:
            return
        bar = send(Rect, c_void_p(container), "frame")
        height = 2 * center_y  # a title bar this tall has its middle on the toolbar row
        bar.origin.y = send(Rect, c_void_p(frame_view), "frame").size.height - height  # AppKit's y runs up
        bar.size.height = height
        send(None, c_void_p(container), "setFrame:", bar)
        for i, button in enumerate(buttons):
            f = send(Rect, c_void_p(button), "frame")
            f.origin.x = first_x + i * spacing - f.size.width / 2
            f.origin.y = (height - f.size.height) / 2
            send(None, c_void_p(button), "setFrame:", f)
    except Exception:  # noqa: BLE001 - cosmetic; never break the window over it
        return


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
        self.hover = ink.Tween(self)
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
        self.hover.to(1)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.hover.to(0)
        super().leaveEvent(e)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        h = self.hover.value
        red = self.kind == "close" and self.parentWidget().red_close
        if red and h > 0.01:
            c = QColor(WindowsCaption.RED)
            c.setAlphaF(h)
            p.fillPath(glass_path(r, 18), c)
        elif not red:
            paint_hover(p, glass_path(r, 18), h)
        red = red and h > 0.5
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
        self.mac_lights = None  # (centre y, first light's centre x): move the traffic lights there (macOS)
        self._cursor_edges = None
        if PLATFORM == "mac":
            window.setWindowFlag(Qt.ExpandedClientAreaHint, True)
            window.setWindowFlag(Qt.NoTitleBarBackgroundHint, True)
            # Qt 6.9+ pads the contents by the title bar's height; the layouts here already run under it
            window.setAttribute(Qt.WA_ContentsMarginsRespectsSafeArea, False)
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
        hide_mac_title(self.window)
        self.place_lights()

    def place_lights(self):
        """Put the traffic lights back on the toolbar row after AppKit moved them (resize, full screen)."""
        if PLATFORM == "mac" and self.mac_lights:
            QTimer.singleShot(0, lambda: place_mac_lights(self.window, *self.mac_lights))

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
        if t == QEvent.Resize and obj is self.window:
            self.place_lights()
            return False
        if t not in (QEvent.MouseButtonPress, QEvent.MouseButtonDblClick, QEvent.WindowStateChange,
                     QEvent.WindowActivate, QEvent.WindowDeactivate):
            return False
        if not isinstance(obj, QWidget) or obj.window() is not self.window:
            return False
        if t in (QEvent.WindowStateChange, QEvent.WindowActivate, QEvent.WindowDeactivate):
            if obj is self.window:
                self._state_changed()
                self.place_lights()
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
        lay.setContentsMargins(24, 20 + self.frame.mac_top(), 24, 18)  # a Mac's lights sit above the title
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


class PaperDialog(QDialog):
    """A dialog on fillmate's dotted paper, with its title as a heading."""

    def __init__(self, parent, title):
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
        self.body = QVBoxLayout(paper)
        self.body.setContentsMargins(24, 20 + self.frame.mac_top(), 24, 18)
        self.body.setSpacing(12)
        self.body.addWidget(ink.set_role(ink.label(title), "h1"))
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
