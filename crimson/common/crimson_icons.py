from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import sys

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap


_ALIASES = {
    "backup": "save",
    "database": "inventory",
    "dragon": "mounts",
    "game patches": "mounts",
    "menu": "menu",
    "packs": "packs",
    "path": "path",
    "saves": "save",
    "tools": "mods",
}


def resolve_game_art(asset_id: int | str) -> Path | None:
    """Return checked-in or bundled game artwork without touching user folders."""
    filename = f"{asset_id}.webp"
    candidates: list[Path] = []
    bundle_root = getattr(sys, "_MEIPASS", "")
    if bundle_root:
        candidates.append(Path(bundle_root) / "crimson_assets" / filename)
    repository_root = Path(__file__).resolve().parents[1]
    candidates.extend(
        (
            repository_root / "icons_mercenary" / filename,
            Path.cwd() / "icons_mercenary" / filename,
            Path(sys.executable).resolve().parent / "crimson_assets" / filename,
        )
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def game_art_icon(asset_id: int | str, fallback: str = "mounts") -> QIcon:
    path = resolve_game_art(asset_id)
    return QIcon(str(path)) if path is not None else symbol_icon(fallback)


def inferred_symbol(label: str) -> str:
    normalized = label.strip().lower()
    if normalized in _ALIASES:
        return _ALIASES[normalized]
    if any(word in normalized for word in ("mount", "blackstar", "dragon", "pet")):
        return "mounts"
    if any(
        word in normalized
        for word in ("item", "inventory", "equipment", "socket", "dye", "store", "drop")
    ):
        return "inventory"
    if any(
        word in normalized
        for word in ("world", "field", "region", "spawn", "quest", "map", "knowledge")
    ):
        return "world"
    if any(word in normalized for word in ("save", "backup", "restore", "overview")):
        return "save"
    return "mods"


@lru_cache(maxsize=96)
def symbol_icon(name: str, size: int = 24) -> QIcon:
    """Draw restrained Crimson line icons without relying on platform glyphs."""
    canonical = _ALIASES.get(name.strip().lower(), name.strip().lower())
    icon = QIcon()
    for mode, color in (
        (QIcon.Normal, QColor("#8E958D")),
        (QIcon.Active, QColor("#F2EDE3")),
        (QIcon.Selected, QColor("#F2EDE3")),
        (QIcon.Disabled, QColor("#3D443E")),
    ):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, True)
        scale = size / 24.0
        painter.scale(scale, scale)
        painter.setPen(QPen(color, 1.55, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.setBrush(Qt.NoBrush)
        _draw_symbol(painter, canonical)
        painter.end()
        icon.addPixmap(pixmap, mode, QIcon.Off)
        icon.addPixmap(pixmap, mode, QIcon.On)
    return icon


def _draw_symbol(painter: QPainter, name: str) -> None:
    if name == "save":
        painter.drawRoundedRect(QRectF(5, 3, 14, 18), 1.2, 1.2)
        painter.drawLine(QPointF(8, 3), QPointF(8, 9))
        painter.drawLine(QPointF(8, 9), QPointF(16, 9))
        painter.drawLine(QPointF(10, 14), QPointF(16, 14))
        painter.drawLine(QPointF(10, 17), QPointF(16, 17))
        return
    if name == "mounts":
        wing = QPainterPath(QPointF(4, 16))
        wing.cubicTo(6, 7, 12, 3, 20, 5)
        wing.cubicTo(15, 8, 14, 12, 13, 18)
        wing.cubicTo(10, 14, 8, 14, 4, 16)
        painter.drawPath(wing)
        painter.drawLine(QPointF(7, 14), QPointF(16, 7))
        painter.drawLine(QPointF(10, 15), QPointF(17, 10))
        return
    if name == "inventory":
        bag = QPainterPath(QPointF(6, 8))
        bag.lineTo(18, 8)
        bag.lineTo(20, 20)
        bag.lineTo(4, 20)
        bag.closeSubpath()
        painter.drawPath(bag)
        painter.drawArc(QRectF(8, 3, 8, 9), 0, 180 * 16)
        painter.drawLine(QPointF(8, 12), QPointF(16, 12))
        return
    if name == "world":
        painter.drawEllipse(QRectF(3.5, 3.5, 17, 17))
        needle = QPainterPath(QPointF(14.5, 7))
        needle.lineTo(12.5, 13)
        needle.lineTo(7.5, 17)
        needle.lineTo(10.2, 10.3)
        needle.closeSubpath()
        painter.drawPath(needle)
        painter.drawEllipse(QRectF(11.2, 11.2, 1.6, 1.6))
        return
    if name in {"mods", "tools"}:
        painter.drawLine(QPointF(5, 19), QPointF(18, 6))
        painter.drawLine(QPointF(6, 6), QPointF(19, 19))
        painter.drawEllipse(QRectF(3.5, 3.5, 4.5, 4.5))
        painter.drawEllipse(QRectF(16, 16, 4.5, 4.5))
        painter.drawLine(QPointF(15, 5), QPointF(20, 4))
        painter.drawLine(QPointF(20, 4), QPointF(19, 9))
        return
    if name == "menu":
        for y, width in ((6, 15), (12, 11), (18, 15)):
            painter.drawLine(QPointF(4, y), QPointF(4 + width, y))
        return
    if name == "path":
        folder = QPainterPath(QPointF(3, 7))
        folder.lineTo(9, 7)
        folder.lineTo(11, 9)
        folder.lineTo(21, 9)
        folder.lineTo(19, 19)
        folder.lineTo(3, 19)
        folder.closeSubpath()
        painter.drawPath(folder)
        painter.drawEllipse(QRectF(10, 11, 4, 4))
        return
    if name == "packs":
        painter.drawRect(QRectF(5, 6, 12, 14))
        painter.drawRect(QRectF(8, 3, 12, 14))
        painter.drawLine(QPointF(11, 8), QPointF(17, 8))
        return
    _draw_symbol(painter, inferred_symbol(name))
