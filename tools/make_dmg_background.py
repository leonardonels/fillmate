#!/usr/bin/env python3
"""Draw the macOS installer window's background: the paper, and a hand-drawn ink arrow
from the app to the Applications folder with "drag to install" in the red pen.

Writes packaging/macos/dmg-background.png (640×400) and dmg-background@2x.png (1280×800);
the release workflow joins them into one Retina-ready TIFF. The icon positions here must
match packaging/macos/dmg_settings.py.

Run from the repo root: python tools/make_dmg_background.py
"""
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QImage, QPainter, QPainterPath, QPen

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "packaging" / "macos"
FONTS = ROOT / "src" / "fillmate" / "resources" / "fonts"
W, H = 640, 400
GROUND, INK, RED, MARK = "#f6f4ee", "#1b2a6b", "#d7263d", QColor(29, 28, 26, 70)
APP_X, APPS_X, ICON_Y = 160, 480, 190  # icon centres, as in dmg_settings.py


def draw(p):
    p.fillRect(QRectF(0, 0, W, H), QColor(GROUND))
    # The arrow: one loose stroke between the two icons, with an open head.
    stroke = QPainterPath(QPointF(APP_X + 88, ICON_Y + 6))
    stroke.cubicTo(APP_X + 140, ICON_Y - 22, APPS_X - 140, ICON_Y - 26, APPS_X - 86, ICON_Y + 2)
    p.setPen(QPen(QColor(INK), 3.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.setBrush(Qt.NoBrush)
    p.drawPath(stroke)
    tip = QPointF(APPS_X - 86, ICON_Y + 2)
    head = QPainterPath(QPointF(tip.x() - 17, tip.y() - 14))
    head.quadTo(tip.x() - 6, tip.y() - 5, tip.x(), tip.y())
    head.quadTo(tip.x() - 9, tip.y() + 6, tip.x() - 19, tip.y() + 9)
    p.drawPath(head)
    # The red pen's note above the arrow.
    fid = QFontDatabase.addApplicationFont(str(FONTS / "Caveat-Variable.ttf"))
    family = QFontDatabase.applicationFontFamilies(fid)[0] if fid >= 0 else "Comic Sans MS"
    f = QFont(family)
    f.setPixelSize(30)
    f.setWeight(QFont.DemiBold)
    p.setFont(f)
    p.setPen(QColor(RED))
    p.drawText(QRectF(0, ICON_Y - 92, W, 40), Qt.AlignHCenter | Qt.AlignVCenter, "drag to install")
    # A registration mark in the corner, like the app's window.
    p.setPen(QPen(MARK, 1))
    c = QPointF(W - 34, H - 30)
    p.drawEllipse(c, 7, 7)
    p.drawLine(QPointF(c.x() - 13, c.y()), QPointF(c.x() + 13, c.y()))
    p.drawLine(QPointF(c.x(), c.y() - 13), QPointF(c.x(), c.y() + 13))


def main():
    QGuiApplication([])
    OUT.mkdir(parents=True, exist_ok=True)
    for scale, name in ((1, "dmg-background.png"), (2, "dmg-background@2x.png")):
        image = QImage(W * scale, H * scale, QImage.Format_RGB32)
        image.setDotsPerMeterX(int(72 * scale / 0.0254))  # 72/144 dpi, so Finder sizes them alike
        image.setDotsPerMeterY(int(72 * scale / 0.0254))
        p = QPainter(image)
        p.setRenderHint(QPainter.Antialiasing)
        p.scale(scale, scale)
        draw(p)
        p.end()
        image.save(str(OUT / name))
        print("wrote", OUT / name)


if __name__ == "__main__":
    main()
