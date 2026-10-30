"""不依賴外部圖片的系統匣圖示。"""

from __future__ import annotations

from PySide6.QtCore import QObject, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from .theme import PANEL_QSS


class TrayIcon(QSystemTrayIcon):
    toggleRequested = Signal()
    startStopRequested = Signal()
    quitRequested = Signal()

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.setIcon(self._make_icon())
        self.setToolTip("即時字幕")

        self._menu = QMenu()
        self._menu.setStyleSheet(PANEL_QSS)
        toggle_action = self._menu.addAction("顯示/隱藏面板")
        toggle_action.triggered.connect(self.toggleRequested.emit)
        self._start_action = self._menu.addAction("開始")
        self._start_action.triggered.connect(self.startStopRequested.emit)
        self._menu.addSeparator()
        quit_action = self._menu.addAction("結束")
        quit_action.triggered.connect(self.quitRequested.emit)

        self.setContextMenu(self._menu)
        self.activated.connect(self._activated)
        self.destroyed.connect(self._menu.deleteLater)

    @staticmethod
    def _make_icon() -> QIcon:
        icon = QIcon()
        for size in (16, 24, 32, 48, 64):
            pixmap = QPixmap(size, size)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.scale(size / 32, size / 32)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#306da8"))
            painter.drawRoundedRect(QRectF(1, 3, 30, 26), 6, 6)

            pen = QPen(QColor("white"))
            pen.setWidthF(2.5)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawLine(7, 12, 25, 12)
            painter.drawLine(7, 20, 15, 20)
            painter.drawLine(20, 20, 25, 20)
            painter.end()
            icon.addPixmap(pixmap)
        return icon

    def set_running(self, running: bool) -> None:
        self._start_action.setText("停止" if running else "開始")

    def _activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.toggleRequested.emit()
