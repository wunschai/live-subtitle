"""共用字型、色彩與面板樣式。"""

from __future__ import annotations

from PySide6.QtGui import QFont, QFontDatabase

BACKGROUND = "#171c26"
SURFACE = "#242c3b"
FOREGROUND = "#f5f7fc"
MUTED = "#a5afc2"
ACCENT = "#70b7ff"
WARNING = "#ffcb78"

OVERLAY_PADDING = 12
OVERLAY_BLOCK_GAP = 8
OVERLAY_RADIUS = 12

FONT_FAMILIES = (
    "Microsoft JhengHei UI",
    "Microsoft JhengHei",
    "Noto Sans TC",
    "PingFang TC",
    "Segoe UI",
)


def make_font(family: str = "", pixel_size: int = 12) -> QFont:
    font = QFont()
    if family:
        font.setFamily(family)
    else:
        available = set(QFontDatabase.families())
        for candidate in FONT_FAMILIES:
            if candidate in available:
                font.setFamily(candidate)
                break
    font.setPixelSize(pixel_size)
    return font


PANEL_QSS = f"""
QWidget {{
    color: {FOREGROUND};
}}
QWidget#controlPanel {{
    background: {BACKGROUND};
}}
QLabel {{
    background: transparent;
}}
QLabel#statusLabel, QLabel#hotkeyLabel {{
    color: {MUTED};
}}
QLabel#warningLabel {{
    color: {WARNING};
}}
QPushButton, QToolButton, QComboBox, QSpinBox {{
    background: {SURFACE};
    border: 1px solid #354157;
    border-radius: 6px;
    padding: 4px 7px;
}}
QPushButton:hover, QToolButton:hover, QComboBox:hover {{
    border-color: {ACCENT};
}}
QPushButton:pressed, QToolButton:pressed {{
    background: #34465f;
}}
QPushButton:focus, QToolButton:focus, QComboBox:focus, QSpinBox:focus {{
    border-color: {ACCENT};
}}
QPushButton:disabled, QComboBox:disabled, QSpinBox:disabled {{
    color: #69758b;
    background: #202633;
}}
QPushButton#startButton {{
    background: #306da8;
    border-color: #4788c5;
    font-weight: bold;
    padding: 7px;
}}
QPushButton#startButton:disabled {{
    background: #2a3c50;
    color: {MUTED};
}}
QComboBox QAbstractItemView {{
    background: {SURFACE};
    color: {FOREGROUND};
    selection-background-color: #306da8;
    border: 1px solid #354157;
}}
QSlider::groove:horizontal {{
    height: 4px;
    border-radius: 2px;
    background: #354157;
}}
QSlider::sub-page:horizontal {{
    background: {ACCENT};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: {FOREGROUND};
    width: 12px;
    margin: -4px 0;
    border-radius: 6px;
}}
QSlider::sub-page:horizontal:disabled {{
    background: #536078;
}}
QSlider::handle:horizontal:disabled {{
    background: #69758b;
}}
QGroupBox {{
    border: 1px solid #354157;
    border-radius: 8px;
    margin-top: 8px;
    padding: 8px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 8px;
    padding: 0 4px;
    color: {MUTED};
}}
QProgressBar {{
    background: #283142;
    border: none;
    border-radius: 3px;
    min-height: 6px;
    max-height: 6px;
}}
QProgressBar::chunk {{
    background: {ACCENT};
    border-radius: 3px;
}}
QMenu {{
    background: {BACKGROUND};
    color: {FOREGROUND};
    border: 1px solid #354157;
    padding: 4px;
}}
QMenu::item {{
    padding: 6px 18px;
    border-radius: 4px;
}}
QMenu::item:selected {{
    background: #306da8;
}}
QToolTip {{
    background: {SURFACE};
    color: {FOREGROUND};
    border: 1px solid #354157;
    padding: 5px;
}}
"""
