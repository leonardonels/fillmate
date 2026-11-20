#!/usr/bin/env python3
"""Render the SVG icon to every format the installers need (Linux PNGs, Windows .ico, macOS .icns).

Run from the repo root: python tools/make_icons.py
"""
import io
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

ROOT = Path(__file__).resolve().parents[1]
SVG = ROOT / "src" / "fillmate" / "resources" / "fillmate.svg"
OUT = ROOT / "packaging" / "icons"
LINUX_SIZES = (16, 32, 64, 128, 256, 512)


def render(renderer, size):
    image = QImage(size, size, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    renderer.render(painter)
    painter.end()
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.WriteOnly)
    image.save(buf, "PNG")
    return Image.open(io.BytesIO(bytes(data))).convert("RGBA")


def main():
    QGuiApplication([])
    renderer = QSvgRenderer(str(SVG))
    OUT.mkdir(parents=True, exist_ok=True)
    big = render(renderer, 1024)
    for size in LINUX_SIZES:
        render(renderer, size).save(OUT / f"fillmate-{size}.png")
    big.save(OUT / "fillmate.ico", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    big.save(OUT / "fillmate.icns")
    print(f"icons written to {OUT}")


if __name__ == "__main__":
    main()
