"""Draw the startup splash in the style of the game's title screen.

Qt is already a dependency, so this needs no image library. Run it after
changing the title text or the tested game version:

    python tools/make_splash.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import (QColor, QFont, QGuiApplication, QImage,
                           QLinearGradient, QPainter, QPen)

from crimson import TESTED_GAME_VERSION

WIDTH, HEIGHT = 720, 300
PROGRESS_TRACK_Y = 268
# The title screen uses a wide-tracked serif; take the closest installed one.
SERIF_CHOICES = ("Cambria", "Constantia", "Georgia", "Palatino Linotype",
                 "Book Antiqua", "Times New Roman")
MONO_CHOICES = ("Consolas", "Courier New")
GOLD_HI = QColor(238, 214, 154)
GOLD = QColor(198, 160, 88)
GOLD_LO = QColor(140, 104, 48)


def _resolve(choices):
    """Return the first family Qt can actually render, not a substitute.

    The offscreen platform plugin exposes no fonts at all, which silently
    renders every glyph as a box - so refuse to write a splash of tofu.
    """
    from PySide6.QtGui import QFontInfo

    for family in choices:
        if QFontInfo(QFont(family, 20)).family().casefold() == family.casefold():
            return family
    raise SystemExit(
        "No usable font found. Run this without QT_QPA_PLATFORM=offscreen: "
        "the offscreen plugin has an empty font database."
    )


def _tracked(painter, text, font, y, spacing, color):
    """Draw centred text with the wide letter spacing the title screen uses."""
    painter.setFont(font)
    metrics = painter.fontMetrics()
    width = sum(metrics.horizontalAdvance(c) + spacing for c in text) - spacing
    x = (WIDTH - width) / 2
    painter.setPen(color)
    for char in text:
        advance = metrics.horizontalAdvance(char)
        painter.drawText(QRectF(x, y, advance + 2, metrics.height()),
                         Qt.AlignLeft | Qt.AlignVCenter, char)
        x += advance + spacing
    return width


def main() -> int:
    QGuiApplication([])
    serif = _resolve(SERIF_CHOICES)
    mono = _resolve(MONO_CHOICES)
    image = QImage(WIDTH, HEIGHT, QImage.Format_ARGB32)
    image.fill(QColor(6, 5, 4))

    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.TextAntialiasing)

    glow = QLinearGradient(0, 0, 0, HEIGHT)
    glow.setColorAt(0.0, QColor(16, 12, 8))
    glow.setColorAt(0.45, QColor(28, 21, 12))
    glow.setColorAt(1.0, QColor(6, 5, 4))
    painter.fillRect(0, 0, WIDTH, HEIGHT, glow)

    title = QFont(serif, 34)
    title.setWeight(QFont.Medium)
    width = _tracked(painter, "CRIMSON  DESERT", title, 88, 11, GOLD_HI)

    rule_y = 150
    painter.setPen(QPen(QColor(120, 92, 44), 1))
    painter.drawLine(int((WIDTH - width) / 2), rule_y, int((WIDTH + width) / 2), rule_y)
    painter.setPen(QPen(GOLD, 1))
    painter.drawLine(int(WIDTH / 2 - 26), rule_y, int(WIDTH / 2 + 26), rule_y)

    _tracked(painter, "SAVE  &  MOD  EDITOR", QFont(serif, 11), 162, 6,
             QColor(176, 146, 96))
    _tracked(painter, f"TESTED ON GAME BUILD {TESTED_GAME_VERSION}",
             QFont(serif, 9), 188, 3, QColor(126, 104, 66))

    painter.setPen(QColor(122, 104, 72))
    painter.setFont(QFont(serif, 9))
    painter.drawText(QRectF(0, 214, WIDTH, 16), Qt.AlignHCenter,
                     "Based on the original work of NattKh")
    painter.setPen(QColor(96, 84, 66))
    painter.setFont(QFont(mono, 8))
    painter.drawText(QRectF(0, 232, WIDTH, 14), Qt.AlignHCenter,
                     "github.com/NattKh")

    # Empty band at the foot of the splash reserved for the progress line,
    # with a faint track so the bar has something to sit in.
    track_w, track_h = 420, 3
    track_x, track_y = (WIDTH - track_w) / 2, PROGRESS_TRACK_Y
    painter.fillRect(QRectF(track_x, track_y, track_w, track_h), QColor(38, 30, 20))
    painter.end()

    out = Path(__file__).resolve().parents[1] / "splash.png"
    image.save(str(out))
    print(f"wrote {out} ({image.width()}x{image.height()}) "
          f"serif={serif} tested={TESTED_GAME_VERSION}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
