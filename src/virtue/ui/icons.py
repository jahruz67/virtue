"""Small vector icons rendered with QtGui, without platform-theme dependencies."""

from functools import lru_cache

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QPushButton


_PATHS = {
    "add": (("M", 12, 5), ("L", 12, 19), ("M", 5, 12), ("L", 19, 12)),
    "monitor": (("M", 12, 17), ("L", 12, 21), ("M", 8, 21), ("L", 16, 21)),
    "import": (("M", 4, 14), ("L", 4, 20), ("L", 20, 20), ("L", 20, 14),
               ("M", 12, 3), ("L", 12, 14), ("M", 8, 10), ("L", 12, 14), ("L", 16, 10)),
    "folder": (("M", 3, 6), ("L", 9, 6), ("L", 11, 8), ("L", 21, 8),
               ("L", 21, 19), ("L", 3, 19), ("Z",)),
    "help": (("M", 9.2, 8.5), ("C", 9.5, 5.8, 15.5, 6, 15, 9.5),
             ("C", 14.8, 11.5, 12, 11.5, 12, 14)),
    "more": (),
}


def _action_pixmap(name, size, color):
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(size / 24, size / 24)
    painter.setPen(QPen(QColor(color), 1.8, Qt.PenStyle.SolidLine,
                        Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    if name == "monitor":
        painter.drawRoundedRect(QRectF(3, 4, 18, 13), 2, 2)
    elif name == "help":
        painter.drawEllipse(QRectF(3, 3, 18, 18))
        painter.drawPoint(12, 17)
    elif name == "more":
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        for x in (5, 12, 19):
            painter.drawEllipse(QRectF(x - 1.2, 10.8, 2.4, 2.4))
    path = QPainterPath()
    for command, *values in _PATHS[name]:
        if command == "M":
            path.moveTo(*values)
        elif command == "L":
            path.lineTo(*values)
        elif command == "C":
            path.cubicTo(*values)
        else:
            path.closeSubpath()
    painter.drawPath(path)
    painter.end()
    return pixmap


@lru_cache(maxsize=24)
def action_icon(name: str, color: str = "#34445b") -> QIcon:
    if name not in _PATHS:
        raise ValueError(f"Unknown action icon: {name}")
    icon = QIcon()
    for size in (18, 24, 36, 48):
        icon.addPixmap(_action_pixmap(name, size, color))
        icon.addPixmap(_action_pixmap(name, size, "#8b95a4"), QIcon.Mode.Disabled)
    return icon


def icon_button(name: str, description: str) -> QPushButton:
    button = QPushButton()
    button.setIcon(action_icon(name))
    button.setIconSize(QSize(18, 18))
    button.setFixedHeight(36)
    button.setMinimumWidth(36)
    button.setProperty("compact", True)
    button.setToolTip(description)
    button.setAccessibleName(description)
    return button


@lru_cache(maxsize=1)
def app_icon() -> QIcon:
    """Match packaging/linux/virtue.svg at native taskbar and HiDPI sizes."""
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.scale(size / 128, size / 128)
        painter.setPen(Qt.PenStyle.NoPen)
        for color, rect, radius in (
            ("#245edb", (4, 4, 120, 120), 28),
            ("#a6c5ff", (25, 36, 64, 64), 8),
            ("#ffffff", (39, 24, 64, 64), 8),
        ):
            painter.setBrush(QColor(color))
            painter.drawRoundedRect(QRectF(*rect), radius, radius)
        painter.fillRect(QRectF(39, 40, 64, 2), QColor("#c8dafb"))
        painter.setBrush(QColor("#245edb"))
        for x in (49, 57):
            painter.drawEllipse(QRectF(x - 2, 30, 4, 4))
        for x in (55, 71):
            for y in (52, 68):
                painter.drawRoundedRect(QRectF(x, y, 12, 12), 1, 1)
        painter.end()
        icon.addPixmap(pixmap)
    return icon
