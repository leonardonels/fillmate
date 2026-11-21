"""The "Ink and hand" look: dot-grid paper, ink for what you wrote, a red pen for
fillmate's notes, and Liquid Glass for the tools that lie on top.

Liquid Glass is painted here, not by the platform: each GlassPanel copies the
content behind it, blurs it a little, bends it near the rim with a displacement
map (the same rounded-rect lens as the design canvas), and draws the rim and a
faint contact shadow. The copy is refreshed by GlassLayer.schedule(), which callers run
whenever the content under the glass changes (scrolling, new previews, resizes).
"""
import random
import re
from pathlib import Path

import numpy as np
from PySide6.QtCore import (
    QEasingCurve, QEvent, QObject, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, QVariantAnimation, Signal,
)
from PySide6.QtGui import (
    QBrush, QColor, QFont, QFontDatabase, QFontMetricsF, QIcon, QImage, QLinearGradient, QPainter, QPainterPath, QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractButton, QApplication, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QSizePolicy,
    QToolButton, QVBoxLayout, QWidget,
)

# --- tokens ----------------------------------------------------------------

LIGHT = {
    "ink": "#1b2a6b", "red": "#d7263d", "hl": "#ffe45c", "ground": "#f6f4ee", "paper": "#ffffff",
    "label": "#1d1c1a", "label2": "#5c5a54", "rule": "#e4e1d8", "dot": QColor(27, 42, 107, 44),
    "glass": QColor(255, 255, 255, 51), "glass_reg": QColor(255, 255, 255, 30), "rim": QColor(255, 255, 255),
    "edge": QColor(0, 0, 0, 36), "shadow": QColor(30, 30, 20), "pill": QColor(255, 255, 255, 158),
    "switch_on": "#5a5852", "track": QColor(120, 120, 128, 51), "on_ink": "#ffffff",
    "hover": QColor(255, 255, 255, 150), "pill_rim": 255, "sheen": 90,
}
DARK = {
    # Dark glass carries a smoky tint, so it stays dark (and its light icons readable) even over the
    # white page of a preview; rims and pills catch less light than on paper.
    "ink": "#aab9ff", "red": "#ff7a88", "hl": "#e8c62e", "ground": "#121211", "paper": "#1f1f1d",
    "label": "#efeee9", "label2": "#a7a59e", "rule": "#363531", "dot": QColor(170, 185, 255, 34),
    "glass": QColor(36, 36, 34, 150), "glass_reg": QColor(32, 32, 30, 120), "rim": QColor(255, 255, 255, 95),
    "edge": QColor(255, 255, 255, 26), "shadow": QColor(0, 0, 0), "pill": QColor(255, 255, 255, 40),
    "switch_on": "#8f8d86", "track": QColor(120, 120, 128, 90), "on_ink": "#101114",
    "hover": QColor(255, 255, 255, 30), "pill_rim": 80, "sheen": 34,
}
T = dict(LIGHT)

FONTS_DIR = Path(__file__).resolve().parent.parent / "resources" / "fonts"
UI_FONTS = ["Inter", "Inter Variable", "Cantarell", "Noto Sans", "Segoe UI", "Helvetica Neue", "Arial"]
HAND_FONTS = ["Caveat", "Kalam", "Patrick Hand", "Comic Neue", "Segoe Print", "Bradley Hand"]
MONO_FONTS = ["Courier Prime", "Courier New", "DejaVu Sans Mono", "Menlo", "Consolas", "monospace"]
DISPLAY_FONTS = ["Archivo", "Inter", "Inter Variable", "Arial"]
_families = {}


def load_fonts():
    """Register any fonts shipped in resources/fonts, then pick a family per role."""
    if FONTS_DIR.is_dir():
        for path in sorted(FONTS_DIR.glob("*.[ot]tf")):
            QFontDatabase.addApplicationFont(str(path))
    available = set(QFontDatabase.families())
    for role, wanted in (("ui", UI_FONTS), ("hand", HAND_FONTS), ("mono", MONO_FONTS), ("display", DISPLAY_FONTS)):
        _families[role] = next((f for f in wanted if f in available), "")


def font(role="ui", px=14, weight=QFont.Normal):
    f = QFont(_families.get(role) or QApplication.font().family())
    f.setPixelSize(px)
    f.setWeight(weight)
    if role == "hand" and not _families.get("hand"):
        f.setItalic(True)  # no handwriting font installed: italic still reads as a note
    return f


def hand_px(px):
    """Handwriting fonts run small; an italic fallback does not need the boost."""
    return px if _families.get("hand") else round(px * 0.72)


def hand_style(px, underline=False):
    """Stylesheet for text in fillmate's red pen."""
    family = _families.get("hand")
    face = f'font-family: "{family}";' if family else "font-style: italic;"
    line = " text-decoration: underline;" if underline else ""
    return f"color: {T['red']}; {face} font-size: {hand_px(px)}px; font-weight: 700;{line}"


def set_dark(dark):
    T.clear()
    T.update(DARK if dark else LIGHT)


def stylesheet():
    """App-wide Qt stylesheet built from the current tokens."""
    t = T
    light = t["ground"] == LIGHT["ground"]
    ui = _families.get("ui") or ""
    mono = _families.get("mono") or "monospace"
    family = f'font-family: "{ui}";' if ui else ""
    return f"""
    QWidget {{ color: {t['label']}; {family} font-size: 14px; }}
    QDialog, QMainWindow {{ background: {t['ground']}; }}
    QToolTip {{ background: {t['paper']}; color: {t['label']}; border: 1px solid {t['rule']}; padding: 4px 8px; }}
    QScrollArea, QScrollArea > QWidget > QWidget, QStackedWidget {{ background: transparent; border: 0; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: rgba(120,120,128,0.35); border-radius: 3px; min-height: 30px; }}
    QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ height: 0; background: none; }}
    QLabel[role="lbl"] {{ color: {t['label2']}; font-size: 12px; font-weight: 500; }}
    QLabel[role="muted"] {{ color: {t['label2']}; font-size: 13px; }}
    QLabel[role="sec"] {{ color: {t['label2']}; font-size: 13px; font-weight: 600; padding: 0 6px; }}
    QLabel[role="h1"] {{ font-size: 17px; font-weight: 700; }}
    QLabel[role="h2"] {{ font-size: 28px; font-weight: 700; }}
    QLabel[role="path"] {{ font-family: "{mono}"; font-size: 13px; }}
    QLabel[role="ink"] {{ color: {t['ink']}; font-size: 15px; font-weight: 500; }}
    QLineEdit, QPlainTextEdit, QDateEdit, QTimeEdit, QSpinBox, QDoubleSpinBox, QComboBox[editable="true"] {{
        background: transparent; border: 0; border-bottom: 1px solid {t['rule']}; border-radius: 0;
        color: {t['ink']}; font-size: 15px; font-weight: 500; padding: 4px 0 5px 0;
        selection-background-color: {t['hl']}; selection-color: {LIGHT['label']};
    }}
    QLineEdit:focus, QPlainTextEdit:focus, QDateEdit:focus, QTimeEdit:focus, QSpinBox:focus,
    QDoubleSpinBox:focus, QComboBox[editable="true"]:focus {{ border-bottom: 2px solid {t['ink']}; padding-bottom: 4px; }}
    QLineEdit:disabled, QPlainTextEdit:disabled {{ color: {t['label2']}; }}
    QLineEdit[role="plain"] {{ color: {t['label']}; }}
    QDateEdit::drop-down, QTimeEdit::up-button, QTimeEdit::down-button, QSpinBox::up-button,
    QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 0; border: 0; }}
    QComboBox {{ background: {t['paper']}; border: 1px solid rgba(120,120,128,0.35); border-radius: 10px;
        padding: 5px 12px; min-height: 22px; }}
    QComboBox::drop-down {{ border: 0; width: 22px; }}
    QComboBox QAbstractItemView {{ background: {t['paper']}; border: 1px solid {t['rule']}; border-radius: 8px;
        selection-background-color: rgba(120,120,128,0.18); selection-color: {t['label']}; padding: 4px; }}
    QPushButton {{ background: rgba(255,255,255,{'0.55' if light else '0.06'}); border: 1px solid rgba(120,120,128,0.38);
        border-radius: 15px; padding: 6px 14px; font-size: 13px; font-weight: 500; }}
    QPushButton:hover {{ background: {'#ffffff' if light else 'rgba(255,255,255,0.14)'};
        border-color: rgba(120,120,128,0.5); }}
    QPushButton:disabled {{ color: {t['label2']}; border-color: rgba(120,120,128,0.2); }}
    /* primary and secondary are pills: Qt drops a radius over half the height, so the height is pinned
       (20 px text + 2 × 10 px padding + 2 × 1.5 px border = 43 px, and 21 px is half of that). */
    QPushButton[role="primary"] {{ background: transparent; border: 1.5px solid {t['ink']}; color: {t['ink']};
        border-radius: 21px; font-size: 15px; font-weight: 600; padding: 10px 18px; min-height: 20px; }}
    QPushButton[role="primary"]:hover {{ background: {hover_css()}; border-color: {t['ink']}; }}
    QPushButton[role="primary"]:disabled {{ border-color: rgba(120,120,128,0.35); color: {t['label2']}; }}
    QPushButton[role="ink"]:hover {{ background: {t['ink']}; border: 1px solid rgba(255,255,255,0.7); }}
    QPushButton[role="ink"] {{ background: {t['ink']}; border: 0; color: {t['on_ink']}; font-weight: 600;
        border-radius: 15px; padding: 7px 18px; }}
    QPushButton[role="danger"] {{ color: {t['red']}; border-color: rgba(215,38,61,0.4); }}
    QPushButton[role="add"] {{ border-style: dashed; color: {t['label2']}; }}
    QToolButton {{ background: transparent; border: 0; border-radius: 12px; padding: 4px; }}
    QToolButton:hover {{ background: {hover_css()}; }}
    QToolButton:checked {{ background: {t['pill'].name(QColor.HexArgb)}; font-weight: 600; }}
    QFrame#Sheet {{ background: {t['paper']}; border: 1px solid rgba(0,0,0,0.05); border-radius: 16px; }}
    QFrame#Sheet QLabel, QFrame#Sheet QCheckBox, QFrame#Sheet QRadioButton {{ background: transparent; }}
    QListWidget {{ background: {t['paper']}; border: 1px solid rgba(0,0,0,0.05); border-radius: 14px;
        padding: 4px; outline: 0; }}
    QListWidget::item {{ padding: 8px 10px; border-radius: 10px; }}
    QListWidget::item:hover {{ background: {hover_css()}; }}
    QListWidget::item:selected {{ background: rgba(120,120,128,0.16); color: {t['label']}; font-weight: 600; }}
    QGroupBox {{ background: {t['paper']}; border: 1px solid {t['rule']}; border-radius: 16px;
        margin-top: 26px; padding: 14px 14px 12px 14px; }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 6px; top: 2px; color: {t['label2']};
        font-size: 13px; font-weight: 600; }}
    QTabWidget::pane {{ border: 0; }}
    QCheckBox, QRadioButton {{ spacing: 8px; }}
    QMenu {{ background: {t['ground']}; border: 1px solid rgba(120,120,128,0.28); border-radius: 14px;
        padding: 6px; font-size: 14px; }}
    QMenu::item {{ padding: 8px 18px 8px 10px; border-radius: 9px; background: transparent; }}
    QMenu::item:selected {{ background: {'#ffffff' if light else 'rgba(255,255,255,0.12)'};
        color: {t['label']}; border: 1px solid rgba(255,255,255,0.9); }}
    QMenu::icon {{ padding-left: 8px; }}
    QMenu::separator {{ height: 1px; background: {t['rule']}; margin: 5px 10px; }}
    """


def set_role(widget, role):
    widget.setProperty("role", role)
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    return widget


def label(text, role=None, wrap=False, plain=True):
    w = QLabel(text)
    if plain:
        w.setTextFormat(Qt.PlainText)
    w.setWordWrap(wrap)
    if role:
        w.setProperty("role", role)
    return w


# --- paper ------------------------------------------------------------------

def _mark_color(alpha=140):
    c = QColor(T["label"])
    c.setAlpha(alpha)
    return c


def _stamp(p, center, word, small, dpr=1.0):
    """A rubber stamp in the red pen's colour: a double frame, the word, a small line under it,
    pressed on a little crooked and worn where the rubber didn't take."""
    big = font("display", 44, QFont.ExtraBold)
    if _families.get("display") == "Archivo":
        big.setVariableAxis(QFont.Tag("wdth"), 112)
    big.setLetterSpacing(QFont.AbsoluteSpacing, 4)
    mono = font("mono", 13)
    mono.setLetterSpacing(QFont.AbsoluteSpacing, 2.5)
    mono.setCapitalization(QFont.AllUppercase)
    bw = QFontMetricsF(big).horizontalAdvance(word)
    sw = QFontMetricsF(mono).horizontalAdvance(small) if small else 0
    W, H = max(bw, sw) + 56, (96 if small else 74)
    img = QImage(int((W + 8) * dpr), int((H + 8) * dpr), QImage.Format_ARGB32_Premultiplied)
    img.setDevicePixelRatio(dpr)
    img.fill(Qt.transparent)
    q = QPainter(img)
    q.setRenderHint(QPainter.Antialiasing)
    red = QColor(T["red"])
    q.setPen(QPen(red, 3.2))
    q.drawRoundedRect(QRectF(4, 4, W, H), 9, 9)
    q.setPen(QPen(red, 1.3))
    q.drawRoundedRect(QRectF(10, 10, W - 12, H - 12), 5, 5)
    q.setPen(red)
    q.setFont(big)
    q.drawText(QRectF(4, 10, W, 56), Qt.AlignCenter, word)
    if small:
        q.setFont(mono)
        q.drawText(QRectF(4, 62, W, 24), Qt.AlignCenter, small)
    # Wear: specks and a few scuffs where no ink reached the paper. Seeded by the text, so it
    # doesn't change on every repaint.
    q.setCompositionMode(QPainter.CompositionMode_DestinationOut)
    rnd = random.Random(word + small)
    q.setPen(Qt.NoPen)
    q.setBrush(QColor(0, 0, 0, 255))
    for _ in range(int(W * H / 55)):
        r = rnd.uniform(0.4, 1.5)
        q.drawEllipse(QPointF(rnd.uniform(4, W + 4), rnd.uniform(4, H + 4)), r, r)
    q.setPen(QPen(QColor(0, 0, 0, 160), 2.2, Qt.SolidLine, Qt.RoundCap))
    for _ in range(4):
        x, y = rnd.uniform(10, W - 20), rnd.uniform(8, 58)  # scuff the word, never the small line
        q.drawLine(QPointF(x, y), QPointF(x + rnd.uniform(14, 40), y + rnd.uniform(-3, 3)))
    q.end()
    p.save()
    p.translate(center)
    p.rotate(-9)
    p.setOpacity(0.62)
    p.drawImage(QPointF(-(W + 8) / 2, -(H + 8) / 2), img)
    p.restore()


class DotGrid(QWidget):
    """The paper every window is made of: the theme's plain ground.

    With `room` on (the main window) it becomes the print room behind the documents: the open pack's
    name in huge type, rulers, a registration mark
    and edge labels, and a colour bar that doubles as the theme's legend (label, ink, red pen). With `radius` set (a function giving the window's corner radius) it paints round
    corners. (The name is from when it was a dot grid.)"""

    radius = None

    def __init__(self, parent=None):
        super().__init__(parent)
        self.room = False
        self.heading = ""          # the open pack's name: the big type
        self.left = 300            # where the content starts (right of the sidebar)
        self.type_left = 0         # how far right of that the big type starts
        self.side_label = ""       # along the left edge of the content
        self.edge_label = ""       # along the right edge
        self.foot_label = ""       # along the bottom
        self.stamp = None          # (word, small line): a rubber stamp over the big type, e.g. ("SENT", "14:32")
        self._cache = None

    def set_room(self, heading="", side="", edge="", foot="", stamp=None):
        values = (heading, side, edge, foot, stamp)
        if values != (self.heading, self.side_label, self.edge_label, self.foot_label, self.stamp):
            self.heading, self.side_label, self.edge_label, self.foot_label, self.stamp = values
            self.update()

    def paintEvent(self, _event):
        r = self.radius() if self.radius else 0
        key = (self.size().width(), self.size().height(), r, T["ground"], T["label"], self.room,
               T["red"], self.heading, self.left, self.side_label, self.edge_label, self.foot_label, self.stamp)
        if self._cache is None or self._cache[0] != key:
            dpr = self.devicePixelRatioF()
            pm = QPixmap(self.size() * dpr)
            pm.setDevicePixelRatio(dpr)
            pm.fill(Qt.transparent)
            q = QPainter(pm)
            q.setRenderHint(QPainter.Antialiasing)
            self._paint(q, r)
            q.end()
            self._cache = (key, pm)
        QPainter(self).drawPixmap(0, 0, self._cache[1])

    def _paint(self, p, radius):
        rect = QRectF(self.rect())
        if radius:
            p.setClipPath(glass_path(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius))
        p.fillRect(rect, QColor(T["ground"]))
        if self.room:
            self._paint_room(p, rect)

    def _paint_room(self, p, rect):
        w, h = rect.width(), rect.height()
        # The pack's name, huge and faint: which form you are filling.
        name = re.sub(r"\s*\([^)]*\)", "", self.heading).strip()  # "Travel request (example)" → TRAVEL REQUEST
        words = name.upper().split()
        stamp_at = QPointF(self.left + self.type_left + (w - self.left - self.type_left) * 0.55, h * 0.42)
        if words:
            lines = [" ".join(words)]
            if len(words) > 1:
                cut = min(range(1, len(words)), key=lambda i: abs(len(" ".join(words[:i])) - len(" ".join(words[i:]))))
                lines = [" ".join(words[:cut]), " ".join(words[cut:])]
            f = font("display", 100, QFont.ExtraBold)
            stretch = 1.0
            if _families.get("display") == "Archivo":  # a real expanded cut, like a poster's display type
                f.setVariableAxis(QFont.Tag("wdth"), 125)
            else:
                stretch = 1.25  # no wide font: widen what there is
            p.setFont(f)
            widest = max(p.fontMetrics().horizontalAdvance(line) for line in lines) * stretch
            x0 = self.left + self.type_left  # over the previews, where the paper shows
            room = w - x0 - 50
            px = max(48, min(176, 100 * room / max(1, widest + (60 if len(lines) > 1 else 0))))
            f.setPixelSize(int(px))
            p.setFont(f)
            c = QColor(T["label"])
            c.setAlpha(15)
            p.setPen(c)
            y = h * 0.50
            for i, line in enumerate(lines):
                p.save()
                p.translate(x0 + i * px * 0.6, y + i * px * 0.95)
                p.scale(stretch, 1)
                p.drawText(QPointF(0, 0), line)
                p.restore()
            # The stamp lands across the end of the first line, as if pressed on after filling.
            first = p.fontMetrics().horizontalAdvance(lines[0]) * stretch
            stamp_at = QPointF(min(x0 + first - 40, w - 220), y - px * 0.55)
        if self.stamp:
            _stamp(p, stamp_at, *self.stamp, self.devicePixelRatioF())
        # Printer's marks: a ruler on the right edge, registration, labels, colour bar.
        # (No ruler on the left: it sat half under the sidebar's edge and read as a glitch.)
        mark = _mark_color()
        p.setPen(QPen(mark, 1))
        x, y = w - 20, h - 440.0
        while y < h - 140:
            p.drawLine(QPointF(x, y), QPointF(x + 6, y))
            y += 8
        cx, cy = self.left + self.type_left + 40, h - 19
        p.drawEllipse(QPointF(cx, cy), 7, 7)
        p.drawLine(QPointF(cx - 13, cy), QPointF(cx + 13, cy))
        p.drawLine(QPointF(cx, cy - 13), QPointF(cx, cy + 13))
        mono = font("mono", 10)
        mono.setLetterSpacing(QFont.AbsoluteSpacing, 1.4)
        mono.setCapitalization(QFont.AllUppercase)
        p.setFont(mono)
        p.setPen(mark)
        for text, x, y0 in ((self.edge_label, w - 12, 130),):
            if text:
                p.save()
                p.translate(x, y0)
                p.rotate(90)
                p.drawText(QPointF(0, 0), text)
                p.restore()
        if self.foot_label:
            p.drawText(QPointF(cx + 26, h - 15), self.foot_label)
        x = w - 40 - 3 * 15
        for color in (T["label"], T["ink"], T["red"]):
            p.fillRect(QRectF(x, h - 26, 12, 12), QColor(color))
            x += 15


class Sheet(QFrame):
    """A plain white sheet lying on the paper."""

    def __init__(self, parent=None, shadow=True):
        super().__init__(parent)
        self.setObjectName("Sheet")
        if shadow:
            fx = QGraphicsDropShadowEffect(self)
            fx.setBlurRadius(24)
            fx.setOffset(0, 6)
            fx.setColor(QColor(30, 30, 20, 30))
            self.setGraphicsEffect(fx)


# --- icons -------------------------------------------------------------------

def _icon_path(name):
    """Simple stroke icons on a 24-unit grid."""
    p = QPainterPath()
    if name == "sidebar":
        p.addRoundedRect(QRectF(3, 4, 18, 16), 3, 3)
        p.moveTo(9, 4)
        p.lineTo(9, 20)
    elif name == "folder":
        p.moveTo(3, 7)
        p.quadTo(3, 5, 5, 5)
        p.lineTo(9, 5)
        p.lineTo(11, 7)
        p.lineTo(19, 7)
        p.quadTo(21, 7, 21, 9)
        p.lineTo(21, 17)
        p.quadTo(21, 19, 19, 19)
        p.lineTo(5, 19)
        p.quadTo(3, 19, 3, 17)
        p.closeSubpath()
    elif name == "mail":
        p.addRoundedRect(QRectF(3, 5, 18, 14), 2.5, 2.5)
        p.moveTo(3.5, 7)
        p.lineTo(12, 13)
        p.lineTo(20.5, 7)
    elif name == "send":
        p.moveTo(12, 19)
        p.lineTo(12, 5)
        p.moveTo(5, 12)
        p.lineTo(12, 5)
        p.lineTo(19, 12)
    elif name == "plus":
        p.moveTo(12, 5)
        p.lineTo(12, 19)
        p.moveTo(5, 12)
        p.lineTo(19, 12)
    elif name == "back":
        p.moveTo(15, 18)
        p.lineTo(9, 12)
        p.lineTo(15, 6)
    elif name == "forward":
        p.moveTo(9, 18)
        p.lineTo(15, 12)
        p.lineTo(9, 6)
    elif name == "arrow":
        p.moveTo(5, 12)
        p.lineTo(19, 12)
        p.moveTo(13, 6)
        p.lineTo(19, 12)
        p.lineTo(13, 18)
    elif name == "gear":
        import math
        teeth, outer, inner = 8, 9.6, 7.4
        pts = []
        for i in range(teeth):
            a = 2 * math.pi * i / teeth
            for da, r in ((-0.2, inner), (-0.13, outer), (0.13, outer), (0.2, inner)):
                pts.append(QPointF(12 + r * math.cos(a + da), 12 + r * math.sin(a + da)))
        p.moveTo(pts[0])
        for q in pts[1:]:
            p.lineTo(q)
        p.closeSubpath()
        p.addEllipse(QPointF(12, 12), 3, 3)
    elif name == "more":
        for y in (6, 12, 18):
            p.addEllipse(QPointF(12, y), 1.1, 1.1)
    elif name == "square":
        p.addRoundedRect(QRectF(5, 5, 14, 14), 2, 2)
    elif name == "restore":
        p.addRoundedRect(QRectF(4, 8, 12, 12), 2, 2)
        p.moveTo(8, 8)
        p.lineTo(8, 5)
        p.quadTo(8, 4, 9, 4)
        p.lineTo(19, 4)
        p.quadTo(20, 4, 20, 5)
        p.lineTo(20, 15)
        p.quadTo(20, 16, 19, 16)
        p.lineTo(16, 16)
    elif name == "minus":
        p.moveTo(6, 12)
        p.lineTo(18, 12)
    elif name == "fit":
        p.moveTo(4, 9)
        p.lineTo(4, 4)
        p.lineTo(9, 4)
        p.moveTo(15, 4)
        p.lineTo(20, 4)
        p.lineTo(20, 9)
        p.moveTo(20, 15)
        p.lineTo(20, 20)
        p.lineTo(15, 20)
        p.moveTo(9, 20)
        p.lineTo(4, 20)
        p.lineTo(4, 15)
    elif name == "eye":
        p.moveTo(2, 12)
        p.cubicTo(5, 6, 19, 6, 22, 12)
        p.cubicTo(19, 18, 5, 18, 2, 12)
        p.addEllipse(QPointF(12, 12), 3, 3)
    elif name == "cursor":
        p.moveTo(5, 3)
        p.lineTo(19, 11)
        p.lineTo(13, 13)
        p.lineTo(11, 19)
        p.closeSubpath()
    elif name == "box":
        p.addRoundedRect(QRectF(3, 7, 18, 10), 2, 2)
        p.moveTo(8, 12)
        p.lineTo(16, 12)
    elif name == "check":
        p.moveTo(5, 12)
        p.lineTo(10, 17)
        p.lineTo(19, 7)
    elif name == "doc":
        p.moveTo(14, 3)
        p.lineTo(7, 3)
        p.quadTo(5, 3, 5, 5)
        p.lineTo(5, 19)
        p.quadTo(5, 21, 7, 21)
        p.lineTo(17, 21)
        p.quadTo(19, 21, 19, 19)
        p.lineTo(19, 8)
        p.closeSubpath()
        p.moveTo(14, 3)
        p.lineTo(14, 8)
        p.lineTo(19, 8)
    elif name == "user":
        p.addEllipse(QPointF(12, 8), 4, 4)
        p.moveTo(4, 21)
        p.cubicTo(4, 14, 20, 14, 20, 21)
    elif name == "list":
        for y, x1 in ((6, 20), (12, 14), (18, 20)):
            p.moveTo(4, y)
            p.lineTo(x1, y)
    elif name == "trash":
        p.moveTo(3, 6)
        p.lineTo(21, 6)
        p.moveTo(8, 6)
        p.lineTo(8, 4)
        p.lineTo(16, 4)
        p.lineTo(16, 6)
        p.moveTo(19, 6)
        p.lineTo(18, 20)
        p.lineTo(6, 20)
        p.lineTo(5, 6)
    elif name == "close":
        p.moveTo(6, 6)
        p.lineTo(18, 18)
        p.moveTo(18, 6)
        p.lineTo(6, 18)
    return p


def icon(name, color=None, size=20, width=1.8):
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(size / 24, size / 24)
    pen = QPen(QColor(color or T["label"]), width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawPath(_icon_path(name))
    p.end()
    return QIcon(pm)


def tick_path(x, y, w, h):
    """A pen tick that overshoots its box, like a real one."""
    p = QPainterPath(QPointF(x + w * 0.12, y + h * 0.55))
    p.cubicTo(x + w * 0.2, y + h * 0.62, x + w * 0.27, y + h * 0.72, x + w * 0.33, y + h * 0.82)
    p.cubicTo(x + w * 0.46, y + h * 0.5, x + w * 0.66, y + h * 0.25, x + w * 0.92, y + h * 0.08)
    return p


# --- Liquid Glass --------------------------------------------------------------

_lens_cache = {}


def _lens_sources(w, h, radius, bezel, power=0.4):
    """Source pixel indices for a rounded-rect lens: near the rim, sample from deeper
    inside the shape; the centre is untouched. All in device pixels."""
    key = (w, h, round(radius), round(bezel), power)
    if key in _lens_cache:
        return _lens_cache[key]
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    px, py = xs + 0.5 - w / 2, ys + 0.5 - h / 2
    r = min(radius, w / 2, h / 2)
    qx, qy = np.abs(px) - (w / 2 - r), np.abs(py) - (h / 2 - r)
    d = np.hypot(np.maximum(qx, 0), np.maximum(qy, 0)) + np.minimum(np.maximum(qx, qy), 0) - r
    gy, gx = np.gradient(d)
    n = np.hypot(gx, gy) + 1e-6
    t = np.clip(-d / bezel, 0, 1)
    mag = bezel * (t ** power - t)
    mag[d > 0] = 0
    sx = np.clip(np.rint(xs - gx / n * mag), 0, w - 1).astype(np.int32)
    sy = np.clip(np.rint(ys - gy / n * mag), 0, h - 1).astype(np.int32)
    if len(_lens_cache) > 64:
        _lens_cache.clear()
    _lens_cache[key] = (sy, sx)
    return sy, sx


def _box_blur(a, k):
    if k < 1:
        return a
    for axis in (0, 1):
        pad = [(0, 0)] * 3
        pad[axis] = (k + 1, k)
        c = np.cumsum(np.pad(a, pad, mode="edge"), axis=axis, dtype=np.float32)
        hi = [slice(None)] * 3
        lo = [slice(None)] * 3
        hi[axis] = slice(2 * k + 1, None)
        lo[axis] = slice(None, -2 * k - 1)
        a = (c[tuple(hi)] - c[tuple(lo)]) / (2 * k + 1)
    return a


def glass_image(pixmap, radius, bezel, blur, saturate=1.5):
    """The backdrop bent by the lens, slightly blurred and saturated."""
    img = pixmap.toImage().convertToFormat(QImage.Format_RGBA8888)
    w, h = img.width(), img.height()
    if w < 4 or h < 4:
        return img
    dpr = pixmap.devicePixelRatio()
    buf = np.frombuffer(img.constBits(), np.uint8, count=img.sizeInBytes())
    a = buf.reshape(h, img.bytesPerLine())[:, :w * 4].reshape(h, w, 4).astype(np.float32)
    k = int(round(blur * dpr))
    a = _box_blur(_box_blur(a, k), k) if k else a
    sy, sx = _lens_sources(w, h, radius * dpr, bezel * dpr)
    a = a[sy, sx]
    rgb = a[..., :3]
    grey = rgb @ np.array([0.2126, 0.7152, 0.0722], np.float32)
    a[..., :3] = np.clip(grey[..., None] + (rgb - grey[..., None]) * saturate, 0, 255)
    out = np.ascontiguousarray(a.astype(np.uint8))
    result = QImage(out.data, w, h, w * 4, QImage.Format_RGBA8888).copy()
    result.setDevicePixelRatio(dpr)
    return result


def glass_path(rect, radius):
    p = QPainterPath()
    p.addRoundedRect(QRectF(rect), radius, radius)
    return p


def paint_glass_skin(p, rect, radius, fill, tint=None):
    """Fill, inner light, edge and the specular rim drawn over a bent backdrop."""
    path = glass_path(rect, radius)
    p.fillPath(path, fill)
    if tint:
        p.fillPath(path, QColor(tint))
    r = QRectF(rect)
    top = QLinearGradient(r.topLeft(), QPointF(r.left(), r.top() + min(14, r.height() / 2)))
    top.setColorAt(0, QColor(255, 255, 255, T["sheen"] if not tint else 50))
    top.setColorAt(1, QColor(255, 255, 255, 0))
    p.fillPath(path, top)
    bottom = QLinearGradient(QPointF(r.left(), r.bottom() - min(12, r.height() / 2)), r.bottomLeft())
    bottom.setColorAt(0, QColor(0, 0, 0, 0))
    bottom.setColorAt(1, QColor(30, 30, 20, 22))
    p.fillPath(path, bottom)
    p.setPen(QPen(T["edge"], 0.6))
    p.drawPath(path)
    rim = QLinearGradient(r.topLeft(), r.bottomRight())
    strength = T["rim"].alpha() / 255  # the dark theme's rim is fainter
    c = QColor(T["rim"])
    c.setAlpha(int(240 * strength))
    rim.setColorAt(0, c)
    c0 = QColor(T["rim"])
    c0.setAlpha(0)
    rim.setColorAt(0.26, c0)
    rim.setColorAt(0.7, c0)
    c.setAlpha(int(190 * strength))
    rim.setColorAt(1, c)
    p.setPen(QPen(QBrush(rim), 1.2))
    p.drawPath(glass_path(r.adjusted(0.8, 0.8, -0.8, -0.8), max(0, radius - 0.8)))


def hover_css():
    c = T["hover"]
    return f"rgba({c.red()},{c.green()},{c.blue()},{c.alphaF():.2f})"


class Tween(QObject):
    """A number that eases toward a target, repainting `widget` on every step: hover fades,
    the switch knob's slide, a selection pill gliding to the next option."""

    IN, OUT = 120, 220  # ms: light comes up quickly and fades a little slower

    def __init__(self, widget, value=0.0):
        super().__init__(widget)
        self.widget, self.value, self.target = widget, float(value), float(value)
        self.anim = QVariantAnimation(self)
        self.anim.setEasingCurve(QEasingCurve.OutCubic)
        self.anim.valueChanged.connect(self._step)

    def _step(self, v):
        self.value = float(v)
        self.widget.update()

    def to(self, target, ms=None):
        target = float(target)
        if target == self.target and (self.anim.state() == QVariantAnimation.Running or self.value == target):
            return
        self.target = target
        self.anim.stop()
        if not self.widget.isVisible():
            self.value = target
            self.widget.update()
            return
        self.anim.setStartValue(self.value)
        self.anim.setEndValue(target)
        self.anim.setDuration(ms if ms is not None else (self.IN if target > self.value else self.OUT))
        self.anim.start()

    def set(self, value):
        """Jump without animating."""
        self.anim.stop()
        self.value = self.target = float(value)
        self.widget.update()


def lerp_rect(rects, pos):
    """The rect at a fractional index between neighbouring rects (for a pill on its way)."""
    if not rects:
        return QRectF()
    pos = max(0.0, min(pos, len(rects) - 1.0))
    i = int(pos)
    a, b, f = rects[i], rects[min(i + 1, len(rects) - 1)], pos - i
    return QRectF(a.x() + (b.x() - a.x()) * f, a.y() + (b.y() - a.y()) * f,
                  a.width() + (b.width() - a.width()) * f, a.height() + (b.height() - a.height()) * f)


def paint_hover(p, path, amount=1.0):
    """The one hover look: a light wash with a brighter top edge, the way glass catches light.
    `amount` (0–1) fades it in and out."""
    if amount <= 0.01:
        return
    p.save()
    p.setOpacity(p.opacity() * min(1.0, amount))
    p.fillPath(path, T["hover"])
    r = path.boundingRect()
    edge = QLinearGradient(r.topLeft(), r.bottomLeft())
    c = QColor(255, 255, 255, min(255, T["hover"].alpha() + 60))
    edge.setColorAt(0, c)
    c.setAlpha(0)
    edge.setColorAt(0.5, c)
    p.setPen(QPen(QBrush(edge), 1))
    p.setBrush(Qt.NoBrush)
    p.drawPath(path)
    p.restore()


def discard(w):
    """Remove a widget now. deleteLater alone waits for the event loop it was called in, so a widget
    replaced while a dialog's own loop runs would stay on screen (a doubled card) until the dialog closes."""
    w.hide()
    w.setParent(None)
    w.deleteLater()


def paint_lens_pill(p, rect, radius=None):
    """The selected item inside a glass control: a small raised glass pill."""
    r = QRectF(rect)
    radius = r.height() / 2 if radius is None else radius
    path = glass_path(r, radius)
    for i, alpha in enumerate((10, 7, 4)):
        p.fillPath(glass_path(r.adjusted(-i - 1, -i + 1, i + 1, i + 3), radius + i), QColor(30, 30, 20, alpha))
    p.fillPath(path, T["pill"])
    p.setPen(QPen(T["edge"], 0.6))
    p.drawPath(path)
    rim = QLinearGradient(r.topLeft(), r.bottomRight())
    rim.setColorAt(0, QColor(255, 255, 255, T["pill_rim"]))
    rim.setColorAt(0.35, QColor(255, 255, 255, 0))
    rim.setColorAt(1, QColor(255, 255, 255, int(T["pill_rim"] * 0.75)))
    p.setPen(QPen(QBrush(rim), 1.1))
    p.drawPath(glass_path(r.adjusted(0.7, 0.7, -0.7, -0.7), max(0, radius - 0.7)))


class GlassShadow(QWidget):
    """The soft shadow under a glass panel; never takes clicks."""

    MARGIN = 18

    def __init__(self, parent, radius):
        super().__init__(parent)
        self.radius = radius
        self.glow = None  # a colour: the panel is the prominent action and glows in it
        self.lift = False  # a popover over everything: a soft ambient shadow, not just the contact edge
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        m = self.MARGIN
        core = QRectF(self.rect()).adjusted(m, m, -m, -m)
        if self.glow:
            c = QColor(self.glow)
            for i in range(14, 0, -1):
                c.setAlpha(int(3 + (14 - i) * 1.4))
                p.fillPath(glass_path(core.adjusted(-i * 0.9, -i * 0.5 + 4, i * 0.9, i * 0.9 + 4),
                                      self.radius + i), c)
            return
        # Liquid Glass is told apart from what's under it by its rim and the way it bends the backdrop,
        # not by a drop shadow. So glass lying on the window gets only a faint contact edge; a popover
        # floating over everything (the send check, menus) gets a soft ambient shadow as well.
        c = QColor(T["shadow"])
        if self.lift:
            for i in range(10, 0, -1):
                c.setAlpha(int(1 + (10 - i) * 0.25))
                p.fillPath(glass_path(core.adjusted(-i * 0.8, -i * 0.4 + 3, i * 0.8, i * 0.8 + 3), self.radius + i),
                           c)
        for i in range(3, 0, -1):
            c.setAlpha(2 + (3 - i) * 2)
            p.fillPath(glass_path(core.adjusted(-i * 0.4, -i * 0.2 + 1, i * 0.4, i * 0.5 + 1), self.radius + i), c)


class GlassLayer:
    """Keeps every glass panel's copy of what is behind it up to date."""

    def __init__(self, backdrop):
        self.backdrop = backdrop
        self.panels = []
        self.timer = QTimer(singleShot=True, interval=16)
        self.timer.timeout.connect(self.refresh)

    def add(self, panel):
        panel.layer = self
        self.panels.append(panel)
        self.schedule()
        return panel

    def schedule(self, *_args):
        self.timer.start()

    def refresh(self):
        for panel in self.panels:
            if panel.isVisible():
                panel.capture(self.backdrop)


class GlassPanel(QWidget):
    """A piece of Liquid Glass lying over `backdrop` content.

    regular: the variant for panels with a lot of text (sidebar, popovers).
    tint: a colour for the one prominent action (Send).
    """

    def __init__(self, parent=None, radius=22, bezel=18, blur=0.8, regular=False, tint=None, shadow=True,
                 lift=False):
        super().__init__(parent)
        self.lift = lift  # floats over everything (a popover): a soft shadow as well as the contact edge
        self.no_shadow = not shadow
        self.radius, self.bezel, self.blur = radius, bezel, blur
        self.regular, self.tint = regular, tint
        self.image = None
        self.layer = None
        self.hoverable = False  # brighten under the pointer (buttons)
        self._hover = False
        self.hover = Tween(self)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_Hover)
        self.shadow = GlassShadow(parent, radius) if parent is not None and shadow else None

    # The shadow is a sibling just below the panel, a little larger.
    def _place_shadow(self):
        if self.shadow is None:
            return
        if self.shadow.parent() is not self.parent():
            self.shadow.setParent(self.parent())
        m = GlassShadow.MARGIN
        self.shadow.setGeometry(self.geometry().adjusted(-m, -m, m, m))
        self.shadow.glow = None
        self.shadow.lift = getattr(self, "lift", False)
        self.shadow.setVisible(self.isVisible())
        self.shadow.stackUnder(self)
        self.shadow.update()

    def moveEvent(self, event):
        super().moveEvent(event)
        self._place_shadow()
        if self.layer:
            self.layer.schedule()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._place_shadow()
        if self.layer:
            self.layer.schedule()

    def showEvent(self, event):
        super().showEvent(event)
        if self.shadow is None and self.parent() is not None and not getattr(self, "no_shadow", False):
            self.shadow = GlassShadow(self.parent(), self.radius)
        self._place_shadow()
        if self.layer:
            self.layer.schedule()

    def hideEvent(self, event):
        super().hideEvent(event)
        if self.shadow:
            self.shadow.hide()

    def enterEvent(self, event):
        self._hover = True
        self.hover.to(1)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.hover.to(0)
        super().leaveEvent(event)

    def capture(self, backdrop):
        top_left = backdrop.mapFromGlobal(self.mapToGlobal(QPoint(0, 0)))
        rect = QRect(top_left, self.size()).intersected(backdrop.rect())
        if rect.isEmpty():
            self.image = None
        else:
            pix = backdrop.grab(rect)
            if rect.size() != self.size():  # partly outside the backdrop: pad with paper
                full = QPixmap(self.size() * pix.devicePixelRatio())
                full.setDevicePixelRatio(pix.devicePixelRatio())
                full.fill(QColor(T["ground"]))
                q = QPainter(full)
                q.drawPixmap(rect.topLeft() - top_left, pix)
                q.end()
                pix = full
            self.image = glass_image(pix, self.radius, self.bezel, self.blur)
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        path = glass_path(r, self.radius)
        p.save()
        p.setClipPath(path)
        if self.image is not None:
            p.drawImage(r, self.image)
        else:
            p.fillRect(r, QColor(T["ground"]))
        p.restore()
        # A prominent action is clear glass too; only its label is in ink.
        paint_glass_skin(p, r, self.radius, T["glass_reg"] if self.regular else T["glass"])
        if self.hoverable and self.isEnabled():
            paint_hover(p, path, self.hover.value)
        self.paint_content(p)

    def paint_content(self, p):
        pass


class GlassButton(GlassPanel):
    """A capsule of glass that is itself a button (Send, Done, Save).

    filled: a flat fill of the tint with a light label, for the one action that commits something and sits
    on another piece of glass (the send check's Send). Clear glass on glass would read as a second
    pane; a filled control reads as a button lying on the first."""

    clicked = Signal()

    def __init__(self, text="", icon_name=None, parent=None, tint=None, width=96, icon_trails=False,
                 filled=False):
        super().__init__(parent, radius=22, tint=tint, shadow=not filled)  # a filled button is ink, it casts none
        self.filled = filled
        self.hoverable = True
        self.text, self.icon_name = text, icon_name
        self.icon_trails = icon_trails  # the icon after the label (Create documents →), not before
        self.setFixedSize(width, 44)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self._down = False
        self.press = Tween(self)
        self.key_focus = False  # the ring shows for Tab focus only, not when a dialog focuses it for Enter

    def focusInEvent(self, event):
        self.key_focus = event.reason() in (Qt.TabFocusReason, Qt.BacktabFocusReason)
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event):
        self.key_focus = False
        super().focusOutEvent(event)
        self.update()

    def setText(self, text):
        self.text = text
        self.update()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.EnabledChange:
            self._place_shadow()
            self.update()

    def set_tint(self, tint):
        self.tint = tint
        self._place_shadow()
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._down = True
            self.press.to(1, 90)

    def mouseReleaseEvent(self, event):
        was = self._down
        self._down = False
        self.press.to(0, 320)
        if was and self.rect().contains(event.position().toPoint()) and self.isEnabled():
            self.clicked.emit()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter) and self.isEnabled():
            self.clicked.emit()
        else:
            super().keyPressEvent(event)

    def paintEvent(self, event):
        if not (self.filled and self.tint):
            super().paintEvent(event)
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        path = glass_path(r, self.radius)
        base = QColor(self.tint) if self.isEnabled() else QColor(120, 120, 128, 90)
        p.fillPath(path, base)  # flat: no sheen or rim, it is ink rather than a pane of glass
        if self.isEnabled():
            p.fillPath(path, QColor(255, 255, 255, int(34 * max(self.hover.value, self.press.value))))
        self.paint_content(p)

    def paint_content(self, p):
        k = self.press.value
        if k > 0.01:  # pressed: the glass catches more light and the label swells, then settles
            if not self.filled:
                paint_hover(p, glass_path(QRectF(self.rect()), self.radius), k)
            c = self.rect().center()
            p.translate(c.x(), c.y())
            p.scale(1 + 0.06 * k, 1 + 0.06 * k)
            p.translate(-c.x(), -c.y())
        color = QColor(T["on_ink"] if self.filled and self.tint else T["ink"] if self.tint else T["label"])
        if not self.isEnabled():
            color = QColor(T["label2"])
            color.setAlpha(150)
        p.setPen(color)
        p.setFont(font("ui", 15, QFont.DemiBold))
        fm = p.fontMetrics()
        tw = fm.horizontalAdvance(self.text)
        iw = 22 if self.icon_name else 0
        x = (self.width() - tw - iw) / 2
        tx, ix = (x, x + tw + 6) if self.icon_trails else (x + iw, x)
        if self.icon_name:
            icon(self.icon_name, color.name(), 16, 2.1).paint(p, QRect(int(ix), 14, 16, 16))
        p.drawText(QRectF(tx, 0, tw + 2, self.height()), Qt.AlignVCenter | Qt.AlignLeft, self.text)
        if self.hasFocus() and self.key_focus:
            p.setPen(QPen(QColor(T["ink"]), 1.5))
            p.setBrush(Qt.NoBrush)
            p.drawPath(glass_path(QRectF(self.rect()).adjusted(2, 2, -2, -2), self.radius - 2))


class GlassGroup(GlassPanel):
    """Icon buttons sharing one glass capsule (a toolbar group)."""

    def __init__(self, parent=None, height=44):
        super().__init__(parent, radius=height / 2)
        self.setFixedHeight(height)
        self.row = QHBoxLayout(self)
        self.row.setContentsMargins(4, 4, 4, 4)
        self.row.setSpacing(2)
        self._tweens = {}

    def eventFilter(self, obj, event):
        tween = self._tweens.get(obj)
        if tween is not None:
            if event.type() == QEvent.Enter and obj.isEnabled():
                tween.to(1)
            elif event.type() in (QEvent.Leave, QEvent.Hide, QEvent.EnabledChange):
                tween.to(0)
        return False

    def add(self, icon_name, tooltip, slot=None, checkable=False):
        b = QToolButton(self)
        b.setProperty("icon_name", icon_name)
        b.setIcon(icon(icon_name, T["label"], 19))
        b.setIconSize(QSize(19, 19))
        b.setToolTip(tooltip)
        b.setAccessibleName(tooltip)
        b.setFixedSize(40, self.height() - 8)
        b.setCheckable(checkable)
        b.setCursor(Qt.PointingHandCursor)
        b.setStyleSheet("QToolButton { border-radius: 18px; background: transparent; }"
                        "QToolButton:hover { background: transparent; }"
                        "QToolButton:checked { background: transparent; }"
                        "QToolButton:disabled { background: transparent; }")
        self._tweens[b] = Tween(self)
        b.installEventFilter(self)
        if slot:
            b.clicked.connect(slot)
        b.toggled.connect(lambda _on: self.update())
        self.row.addWidget(b)
        self.setFixedWidth(8 + 40 * self.row.count() + 2 * (self.row.count() - 1))
        return b

    def restyle(self):
        """Redraw the icons in the current theme's colour."""
        for i in range(self.row.count()):
            b = self.row.itemAt(i).widget()
            if b and b.property("icon_name"):
                b.setIcon(icon(b.property("icon_name"), T["label"], 19))

    def paint_content(self, p):
        for i in range(self.row.count()):
            b = self.row.itemAt(i).widget()
            if b and b.isCheckable() and b.isChecked():
                paint_lens_pill(p, QRectF(b.geometry()))
            elif b and b in self._tweens:
                r = QRectF(b.geometry())
                paint_hover(p, glass_path(r, r.height() / 2), self._tweens[b].value)


class GlassSegmented(GlassPanel):
    """A glass tab switcher; the selected tab is a raised glass pill."""

    changed = Signal(int)

    def __init__(self, parent=None, labels=()):
        super().__init__(parent, radius=22)
        self.labels, self.current = list(labels), 0
        self.hovered = -1
        self.max_width = None  # set by the window: the room between the title and the buttons
        self.pill = Tween(self)  # where the selection pill is, as a (fractional) index
        self.hover_fade = Tween(self)
        self.setMouseTracking(True)
        self.setFixedHeight(44)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.key_focus = False
        self.setAccessibleName("Outputs")
        self._fit()

    def set_labels(self, labels, current=0):
        self.labels = list(labels)
        self.current = max(0, min(current, len(self.labels) - 1))
        self._fit()
        self.pill.set(self.current)
        self.update()

    MIN_TAB = 84
    SPARE = 40  # most a tab grows by when the bar has room

    def set_max_width(self, width):
        if width != self.max_width:
            self.max_width = width
            self._fit()
            self.update()

    def _fit(self):
        from PySide6.QtGui import QFontMetrics
        fm = QFontMetrics(font("ui", 14, QFont.DemiBold))
        widths = [max(self.MIN_TAB, 32 + fm.horizontalAdvance(t)) for t in self.labels] or [self.MIN_TAB]
        room = (self.max_width or 0) - 8
        if self.max_width and sum(widths) > room:
            # Too wide: the longest names give way first (short ones like "Email" stay whole),
            # down to a cap that makes everything fit; their text is shortened with "…".
            ordered, used, cap = sorted(widths), 0, room / len(widths)
            for i, w in enumerate(ordered):
                left = len(ordered) - i
                if used + w * left >= room:  # from here on every tab is cut to the same width
                    cap = (room - used) / left
                    break
                used += w
            cap = max(self.MIN_TAB, cap)
            widths = [min(w, int(cap)) for w in widths]
        elif self.max_width:  # room to spare: give every tab more air, up to a point
            extra = min(self.SPARE, (room * 0.85 - sum(widths)) / len(widths))
            if extra > 0:
                widths = [int(w + extra) for w in widths]
        self.setFixedWidth(8 + sum(widths))
        self._widths = widths

    def _rects(self):
        x = 4.0
        for w in self._widths:
            yield QRectF(x, 4, w, self.height() - 8)
            x += w

    def set_current(self, index, emit=True):
        if 0 <= index < len(self.labels) and index != self.current:
            self.current = index
            self.pill.to(index, 280)
            if emit:
                self.changed.emit(index)

    def mousePressEvent(self, event):
        for i, r in enumerate(self._rects()):
            if r.contains(event.position()):
                self.set_current(i)

    def mouseMoveEvent(self, event):
        over = next((i for i, r in enumerate(self._rects()) if r.contains(event.position())), -1)
        if over != self.hovered:
            if over >= 0:
                self.hovered = over
                self.hover_fade.set(0)
                self.hover_fade.to(1)
            else:
                self.hover_fade.to(0)
            self.setToolTip(self.labels[over] if over >= 0 else "")  # the whole name, when it is shortened

    def leaveEvent(self, event):
        self.hover_fade.to(0)
        super().leaveEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Right:
            self.set_current(min(self.current + 1, len(self.labels) - 1))
        elif event.key() == Qt.Key_Left:
            self.set_current(max(self.current - 1, 0))
        else:
            super().keyPressEvent(event)

    def paint_content(self, p):
        rects = list(self._rects())
        if 0 <= self.hovered < len(rects) and self.hovered != self.current:
            r = rects[self.hovered]
            paint_hover(p, glass_path(r, r.height() / 2), self.hover_fade.value)
        if rects:
            paint_lens_pill(p, lerp_rect(rects, self.pill.value))
        for i, r in enumerate(rects):
            p.setPen(QColor(T["label"]))
            p.setFont(font("ui", 14, QFont.DemiBold if i == self.current else QFont.Medium))
            text = p.fontMetrics().elidedText(self.labels[i], Qt.ElideMiddle, int(r.width()) - 24)
            p.drawText(r, Qt.AlignCenter, text)
        if self.hasFocus() and self.key_focus:
            p.setPen(QPen(QColor(T["ink"]), 1.5))
            p.setBrush(Qt.NoBrush)
            p.drawPath(glass_path(QRectF(self.rect()).adjusted(2, 2, -2, -2), self.radius - 2))

    # The ring shows for Tab focus only, not after a click (or when focus comes back from the send check).
    def focusInEvent(self, event):
        self.key_focus = event.reason() in (Qt.TabFocusReason, Qt.BacktabFocusReason)
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event):
        self.key_focus = False
        super().focusOutEvent(event)
        self.update()


# --- controls --------------------------------------------------------------------

class GlassSwitch(QAbstractButton):
    """A setting switch: plain Liquid Glass, no ink. The knob turns to glass while held."""

    W, H = 52, 30

    def __init__(self, parent=None, checked=False):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.TabFocus)
        self.setFixedSize(self.W + 6, self.H + 6)  # room for the keyboard focus ring
        self.slide = Tween(self, 1.0 if checked else 0.0)
        self.hold = Tween(self)
        self.toggled.connect(lambda on: self.slide.to(1 if on else 0, 260))
        self.pressed.connect(lambda: self.hold.to(1, 110))
        self.released.connect(lambda: self.hold.to(0, 260))
        self._keyboard_focus = False

    def sizeHint(self):
        return QSize(self.W + 6, self.H + 6)

    def focusInEvent(self, event):
        self._keyboard_focus = event.reason() in (Qt.TabFocusReason, Qt.BacktabFocusReason)
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        self._keyboard_focus = False
        super().focusOutEvent(event)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(3, 3, self.W, self.H)
        if self.slide.target != (1.0 if self.isChecked() else 0.0):  # set from code before showing
            self.slide.set(1.0 if self.isChecked() else 0.0)
        s, held = self.slide.value, self.hold.value
        if not self.isEnabled():
            p.setOpacity(0.45)
        p.setPen(Qt.NoPen)
        a, b = QColor(T["track"]), QColor(T["switch_on"])
        track = QColor.fromRgbF(a.redF() + (b.redF() - a.redF()) * s, a.greenF() + (b.greenF() - a.greenF()) * s,
                                a.blueF() + (b.blueF() - a.blueF()) * s, a.alphaF() + (b.alphaF() - a.alphaF()) * s)
        p.setBrush(track)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        # a soft inner shade along the top of the track
        shade = QLinearGradient(r.topLeft(), r.bottomLeft())
        shade.setColorAt(0, QColor(0, 0, 0, 26))
        shade.setColorAt(0.5, QColor(0, 0, 0, 0))
        p.setBrush(shade)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        kw, kh = 30, self.H - 4
        left, right = r.left() + 2, r.right() - 2 - kw
        knob = QRectF(left + (right - left) * s, r.top() + 2, kw, kh)
        g = 3 * held  # held: the knob grows and turns to glass
        knob = knob.adjusted(-g, -g, g, g)
        if held < 0.99:
            p.save()
            p.setOpacity(p.opacity() * (1 - held))
            for i, al in enumerate((22, 10)):
                p.setBrush(QColor(0, 0, 0, al))
                p.drawRoundedRect(knob.adjusted(-0.5, 0.5 + i, 0.5, 1 + i), knob.height() / 2, knob.height() / 2)
            p.setBrush(QColor("#ffffff"))
            p.drawRoundedRect(knob, knob.height() / 2, knob.height() / 2)
            p.restore()
        if held > 0.01:
            p.save()
            p.setOpacity(p.opacity() * held)
            paint_lens_pill(p, knob)
            p.restore()
        if self._keyboard_focus and self.hasFocus():
            p.setOpacity(1)
            p.setPen(QPen(QColor(T["ink"]), 1.5))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r.adjusted(-2.5, -2.5, 2.5, 2.5), r.height() / 2 + 2.5, r.height() / 2 + 2.5)


class CircleChoice(QWidget):
    """A yes/no answer that ends up on the form: you circle Yes or No in ink."""

    toggled = Signal(bool)

    def __init__(self, parent=None, yes="Yes", no="No"):
        super().__init__(parent)
        self.words, self.value = (yes, no), False
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(36)

    def sizeHint(self):
        return QSize(120, 36)

    def isChecked(self):
        return self.value

    def setChecked(self, on):
        on = bool(on)
        if on != self.value:
            self.value = on
            self.update()
            self.toggled.emit(on)

    def _rects(self):
        w = self.width()
        return QRectF(w - 112, 2, 52, 32), QRectF(w - 56, 2, 52, 32)

    def mousePressEvent(self, event):
        yes, no = self._rects()
        if yes.contains(event.position()):
            self.setChecked(True)
        elif no.contains(event.position()):
            self.setChecked(False)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Space, Qt.Key_Left, Qt.Key_Right, Qt.Key_Y, Qt.Key_N):
            self.setChecked({Qt.Key_Y: True, Qt.Key_N: False}.get(event.key(), not self.value))
        else:
            super().keyPressEvent(event)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        for i, r in enumerate(self._rects()):
            chosen = (i == 0) == self.value
            p.setFont(font("ui", 15, QFont.DemiBold if chosen else QFont.Medium))
            p.setPen(QColor(T["ink"] if chosen else T["label2"]))
            p.drawText(r, Qt.AlignCenter, self.words[i])
            if chosen:
                loop = QPainterPath(QPointF(r.left() + r.width() * 0.73, r.top() + 2))
                loop.cubicTo(r.left() + r.width() * 0.45, r.top() - 1, r.left() + 1, r.top() + 4,
                             r.left() + 2, r.center().y())
                loop.cubicTo(r.left() + 3, r.bottom(), r.right() - 2, r.bottom() + 1, r.right() - 1,
                             r.center().y())
                loop.cubicTo(r.right(), r.top() + 6, r.left() + r.width() * 0.6, r.top() + 1,
                             r.left() + r.width() * 0.45, r.top() + 3)
                p.setPen(QPen(QColor(T["ink"]), 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                p.setBrush(Qt.NoBrush)
                p.drawPath(loop)
        if self.hasFocus():
            p.setPen(QPen(QColor(T["ink"]), 1, Qt.DotLine))
            p.drawRect(QRectF(self.width() - 114, 0, 112, 35))


class TickChoice(QWidget):
    """A choice that becomes tick boxes on the form; the tick is ink.

    Offers the small part of QComboBox's API that FieldInput uses.
    """

    currentTextChanged = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.options, self.index = [], 0
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(32)

    def addItems(self, items):
        self.options.extend(str(i) for i in items)
        self.updateGeometry()
        self.update()

    def findText(self, text):
        return self.options.index(text) if text in self.options else -1

    def currentText(self):
        return self.options[self.index] if self.options else ""

    def setCurrentText(self, text):
        i = self.findText(text)
        if i >= 0 and i != self.index:
            self.index = i
            self.update()
            self.currentTextChanged.emit(text)

    def _rects(self):
        x = 0
        f = font("ui", 15)
        from PySide6.QtGui import QFontMetrics
        fm = QFontMetrics(f)
        for o in self.options:
            w = 26 + fm.horizontalAdvance(o) + 18
            yield QRectF(x, 4, w, 26)
            x += w

    def sizeHint(self):
        rects = list(self._rects())
        return QSize(int(rects[-1].right()) if rects else 100, 34)

    def mousePressEvent(self, event):
        for i, r in enumerate(self._rects()):
            if r.contains(event.position()):
                self.setCurrentText(self.options[i])

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Right and self.index < len(self.options) - 1:
            self.setCurrentText(self.options[self.index + 1])
        elif event.key() == Qt.Key_Left and self.index > 0:
            self.setCurrentText(self.options[self.index - 1])
        else:
            super().keyPressEvent(event)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setFont(font("ui", 15))
        for i, r in enumerate(self._rects()):
            box = QRectF(r.left() + 1, r.top() + 4, 18, 18)
            p.setPen(QPen(QColor(T["label2"]), 1.5))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(box, 3, 3)
            if i == self.index:
                p.setPen(QPen(QColor(T["ink"]), 2.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                p.drawPath(tick_path(box.left() - 2, box.top() - 7, 24, 22))
            p.setPen(QColor(T["label"]))
            p.drawText(QRectF(r.left() + 26, r.top(), r.width() - 26, r.height()), Qt.AlignVCenter, self.options[i])
        if self.hasFocus() and self.options:
            r = list(self._rects())[self.index]
            p.setPen(QPen(QColor(T["ink"]), 1, Qt.DotLine))
            p.setBrush(Qt.NoBrush)
            p.drawRect(r.adjusted(-2, -2, 0, 2))


class MarginNote(QWidget):
    """fillmate's red pen: a short note in the margin, with an arrow up to what it's about."""

    def __init__(self, text="", parent=None, arrow=True, px=21):
        super().__init__(parent)
        self.arrow = arrow
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.mark = QLabel()
        self.mark.setFixedSize(34, 26)
        self.mark.setVisible(arrow)
        lay.addWidget(self.mark, 0, Qt.AlignTop)
        self.px = px
        self.text = QLabel(text)
        self.text.setTextFormat(Qt.PlainText)
        self.text.setWordWrap(True)
        lay.addWidget(self.text, 1)
        self.restyle()

    def restyle(self):
        # The app stylesheet sets the UI font on every widget, so the pen's font goes in a stylesheet too.
        self.text.setStyleSheet(hand_style(self.px) + "background: transparent;")
        pm = QPixmap(68, 52)
        pm.setDevicePixelRatio(2)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(T["red"]), 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        path = QPainterPath(QPointF(30, 22))
        path.cubicTo(20, 22, 10, 18, 6, 6)
        path.moveTo(6, 6)
        path.lineTo(4, 13)
        path.moveTo(6, 6)
        path.lineTo(12, 9)
        p.drawPath(path)
        p.end()
        self.mark.setPixmap(pm)

    def setText(self, text):
        self.text.setText(text)


def highlight(text):
    """Highlighter behind a short piece of text (rich text for QLabel)."""
    import html
    return (f'<span style="background-color:{T["hl"]}; color:{LIGHT["label"]};">'
            f'&nbsp;{html.escape(text)}&nbsp;</span>')


class SideButton(QAbstractButton):
    """A sidebar row; the current one is a raised glass pill."""

    def __init__(self, text, icon_name=None, parent=None, muted=False):
        super().__init__(parent)
        self.setText(text)
        self.icon_name, self.muted = icon_name, muted
        self.reserve = 0  # room kept free on the right (for a row button)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(38)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.hover = Tween(self)
        self.sel = Tween(self)
        self.toggled.connect(lambda on: self.sel.to(1 if on else 0, 240 if on else 160))

    def sizeHint(self):
        return QSize(200, 38)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(3, 1, -3, -5)  # room for the pill's shadow, which is drawn below it
        if self.sel.target != (1.0 if self.isChecked() else 0.0):  # checked from code before it was shown
            self.sel.set(1.0 if self.isChecked() else 0.0)
        s = self.sel.value
        paint_hover(p, glass_path(r, 12), self.hover.value * (1 - s))
        if s > 0.01:  # the pill fades in and settles from a little larger
            p.save()
            p.setOpacity(s)
            g = 2.5 * (1 - s)
            paint_lens_pill(p, r.adjusted(-g, -g / 2, g, g / 2), 12)
            p.restore()
        x = 12
        color = QColor(T["label2"] if self.muted else T["label"])
        if not self.isEnabled():
            color.setAlpha(120)
        if self.icon_name:
            icon(self.icon_name, color.name(), 17).paint(p, QRect(x, int(r.center().y() - 8.5), 17, 17))
            x += 27
        p.setPen(color)
        p.setFont(font("ui", 14, QFont.DemiBold if self.isChecked() else QFont.Normal))
        fm = p.fontMetrics()
        text = fm.elidedText(self.text(), Qt.ElideRight, self.width() - x - 10 - self.reserve)
        p.drawText(QRectF(x, r.top(), self.width() - x, r.height()), Qt.AlignVCenter, text)
        if self.hasFocus():
            p.setPen(QPen(QColor(T["ink"]), 1.2, Qt.DotLine))
            p.setBrush(Qt.NoBrush)
            p.drawPath(glass_path(r, 12))

    def enterEvent(self, event):
        self.hover.to(1)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.hover.to(0)
        super().leaveEvent(event)


def column(*widgets, spacing=8, margins=(0, 0, 0, 0)):
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    for x in widgets:
        lay.addWidget(x)
    return w


class PackRow(SideButton):
    """A pack in the sidebar, with a ⋮ button for what you can do with it."""

    more = Signal(QPoint)

    def __init__(self, text, parent=None):
        super().__init__(text, "doc", parent)
        self.reserve = 30
        self.button = QToolButton(self)
        self.button.setIcon(icon("more", T["label"], 18, 2.2))
        self.button.setIconSize(QSize(18, 18))
        self.button.setFixedSize(30, 30)
        self.button.setToolTip("Edit, show in folder, remove")
        self.button.setAccessibleName(f"More for {text}")
        self.button.setCursor(Qt.PointingHandCursor)
        self.button.setStyleSheet("QToolButton { border-radius: 15px; background: transparent; }"
                                  f"QToolButton:hover {{ background: {hover_css()}; }}")
        self.button.clicked.connect(lambda: self.more.emit(self.button.mapToGlobal(QPoint(0, self.button.height()))))
        self.toggled.connect(lambda _on: self._sync())
        self._sync()

    def _sync(self):
        self.button.setVisible(self.isChecked() or self.underMouse())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.button.move(self.width() - self.button.width() - 6, 17 - self.button.height() // 2)  # on the pill

    def enterEvent(self, event):
        super().enterEvent(event)
        self._sync()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self._sync()


def menu(parent=None):
    """A pop-up menu in the app's look: a white sheet with rounded corners."""
    from PySide6.QtWidgets import QMenu

    m = QMenu(parent)
    m.setWindowFlags(m.windowFlags() | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
    m.setAttribute(Qt.WA_TranslucentBackground)
    return m


class MenuRow(QAbstractButton):
    """One choice in a glass menu."""

    def __init__(self, text, icon_name=None, danger=False, parent=None):
        super().__init__(parent)
        self.setText(text)
        self.icon_name, self.danger = icon_name, danger
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(36)
        self.setAttribute(Qt.WA_Hover)
        self.hover = Tween(self)

    def sizeHint(self):
        return QSize(200, 36)

    def focusInEvent(self, event):
        self.hover.to(1)
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        if not self.underMouse():
            self.hover.to(0)
        super().focusOutEvent(event)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        paint_hover(p, glass_path(r, 11), self.hover.value)
        color = QColor(T["red"] if self.danger else T["label"])
        x = 12
        if self.icon_name:
            icon(self.icon_name, color.name(), 17).paint(p, QRect(x, 9, 17, 17))
            x += 28
        p.setPen(color)
        p.setFont(font("ui", 14))
        text = p.fontMetrics().elidedText(self.text(), Qt.ElideRight, self.width() - x - 10)
        p.drawText(QRectF(x, 0, self.width() - x, self.height()), Qt.AlignVCenter, text)

    def enterEvent(self, event):
        self.hover.to(1)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.hover.to(1 if self.hasFocus() else 0)
        super().leaveEvent(event)


class GlassMenu(GlassPanel):
    """A pop-up menu made of Liquid Glass, inside the window (so it can bend what is behind it)."""

    def __init__(self, parent, width=230):
        super().__init__(parent, radius=18, bezel=22, blur=5, regular=True, lift=True)
        self.setFixedWidth(width)
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(6, 6, 6, 6)
        self.box.setSpacing(1)
        self.catcher = QWidget(parent)  # a click anywhere else closes the menu
        self.catcher.hide()
        self.catcher.mousePressEvent = lambda _e: self.close_menu()
        self.hide()

    def clear(self):
        while self.box.count():
            w = self.box.takeAt(0).widget()
            if w:
                discard(w)

    def add(self, text, icon_name, slot, danger=False):
        row = MenuRow(text, icon_name, danger, self)
        row.clicked.connect(lambda: (self.close_menu(), slot()))
        self.box.addWidget(row)
        return row

    def add_separator(self):
        line = QWidget(self)
        line.setFixedHeight(9)
        line.paintEvent = lambda _e, w=line: QPainter(w).fillRect(QRect(10, 4, w.width() - 20, 1),
                                                                  QColor(120, 120, 128, 60))
        self.box.addWidget(line)

    def popup(self, global_pos):
        parent = self.parentWidget()
        self.adjustSize()
        at = parent.mapFromGlobal(global_pos)
        x = max(8, min(at.x(), parent.width() - self.width() - 8))
        y = max(8, min(at.y() + 4, parent.height() - self.height() - 8))
        self.catcher.setGeometry(parent.rect())
        self.catcher.show()
        self.catcher.raise_()
        self.move(x, y)
        self.show()
        self.raise_()
        if self.box.count():
            self.box.itemAt(0).widget().setFocus()
        if self.layer:
            self.layer.refresh()

    def close_menu(self):
        self.hide()
        self.catcher.hide()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close_menu()
        else:
            super().keyPressEvent(event)


class ChoiceRow(QWidget):
    """Pick one of a few options (Automatic / Light / Dark): a rounded track whose chosen
    option is a raised glass pill. Offers the part of QComboBox's API the settings use."""

    currentIndexChanged = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.items, self.index, self.hovered = [], -1, -1
        self.pill = Tween(self)
        self.hover_fade = Tween(self)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(36)

    def addItem(self, text, data=None):
        self.items.append((text, data))
        if self.index < 0:
            self.index = 0
            self.pill.set(0)
        self.updateGeometry()
        self.update()

    def count(self):
        return len(self.items)

    def findData(self, data):
        return next((i for i, (_t, d) in enumerate(self.items) if d == data), -1)

    def currentIndex(self):
        return self.index

    def currentData(self):
        return self.items[self.index][1] if 0 <= self.index < len(self.items) else None

    def setCurrentIndex(self, i):
        if 0 <= i < len(self.items) and i != self.index:
            self.index = i
            self.pill.to(i, 280)
            self.currentIndexChanged.emit(i)

    def _widths(self):
        from PySide6.QtGui import QFontMetrics
        fm = QFontMetrics(font("ui", 13, QFont.DemiBold))
        return [max(70, fm.horizontalAdvance(t) + 28) for t, _d in self.items]

    def _rects(self):
        x = 3.0
        for w in self._widths():
            yield QRectF(x, 3, w, self.height() - 6)
            x += w

    def sizeHint(self):
        return QSize(int(sum(self._widths()) + 6), 36)

    def minimumSizeHint(self):
        return self.sizeHint()

    def mousePressEvent(self, event):
        for i, r in enumerate(self._rects()):
            if r.contains(event.position()):
                self.setCurrentIndex(i)

    def mouseMoveEvent(self, event):
        over = next((i for i, r in enumerate(self._rects()) if r.contains(event.position())), -1)
        if over != self.hovered:
            if over >= 0:
                self.hovered = over
                self.hover_fade.set(0)
                self.hover_fade.to(1)
            else:
                self.hover_fade.to(0)

    def leaveEvent(self, event):
        self.hover_fade.to(0)
        super().leaveEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Right:
            self.setCurrentIndex(min(self.index + 1, len(self.items) - 1))
        elif event.key() == Qt.Key_Left:
            self.setCurrentIndex(max(self.index - 1, 0))
        else:
            super().keyPressEvent(event)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rects = list(self._rects())
        if not rects:
            return
        track = QRectF(0.5, 0.5, rects[-1].right() + 2.5, self.height() - 1)
        p.fillPath(glass_path(track, track.height() / 2), QColor(120, 120, 128, 30))
        if 0 <= self.hovered < len(rects) and self.hovered != self.index:
            r = rects[self.hovered]
            paint_hover(p, glass_path(r, r.height() / 2), self.hover_fade.value)
        if self.index >= 0:
            paint_lens_pill(p, lerp_rect(rects, self.pill.value))
        for i, r in enumerate(rects):
            p.setPen(QColor(T["label"]))
            p.setFont(font("ui", 13, QFont.DemiBold if i == self.index else QFont.Normal))
            p.drawText(r, Qt.AlignCenter, self.items[i][0])
        if self.hasFocus():
            p.setPen(QPen(QColor(T["ink"]), 1.2, Qt.DotLine))
            p.setBrush(Qt.NoBrush)
            p.drawPath(glass_path(track, track.height() / 2))
