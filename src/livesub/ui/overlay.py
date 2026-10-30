"""不取得焦點、可穿透滑鼠的字幕視窗。"""

from __future__ import annotations

import copy

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QPointF,
    QPropertyAnimation,
    QRect,
    QRectF,
    Qt,
)
from PySide6.QtGui import (
    QColor,
    QFontMetrics,
    QGuiApplication,
    QPaintEvent,
    QPainter,
    QPainterPath,
    QPen,
    QScreen,
    QTextCharFormat,
    QTextLayout,
    QTextOption,
)
from PySide6.QtWidgets import QWidget

from livesub.config import DisplaySettings
from livesub.core.captions import BoardSnapshot, EMPTY_SNAPSHOT

from .theme import (
    OVERLAY_BLOCK_GAP,
    OVERLAY_PADDING,
    OVERLAY_RADIUS,
    make_font,
)


class OverlayWindow(QWidget):
    def __init__(self, settings: DisplaySettings):
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        super().__init__(None, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        self._settings = copy.deepcopy(settings)
        self._snapshot = EMPTY_SNAPSHOT
        self._block_count = 1
        self._screen: QScreen | None = None
        self._fallback_to_primary = False
        self._paths: list[tuple[QPainterPath, bool]] = []
        self._font = make_font(settings.font_family, settings.font_size)

        self.fade_animation = QPropertyAnimation(self, b"windowOpacity", self)
        self.fade_animation.setDuration(300)
        self.fade_animation.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self._opacity_target = 0.0
        self.setWindowOpacity(0.0)

        app = QGuiApplication.instance()
        if app is None:
            raise RuntimeError("建立字幕視窗前必須先建立 QApplication")
        app.screenAdded.connect(self._screens_changed)
        app.screenRemoved.connect(self._screen_removed)
        app.primaryScreenChanged.connect(self._screens_changed)
        self.apply_settings(settings)

    def apply_settings(self, settings: DisplaySettings) -> None:
        if settings.screen != self._settings.screen:
            self._fallback_to_primary = False
        self._settings = copy.deepcopy(settings)
        self._font = make_font(settings.font_family, settings.font_size)
        self.setFont(self._font)
        self._refresh()

    def set_block_count(self, count: int) -> None:
        """收到新模式的快照之前，可先預留對應高度。"""
        count = max(1, int(count))
        if count != self._block_count:
            self._block_count = count
            self._refresh()

    def _choose_screen(self) -> QScreen | None:
        screens = QGuiApplication.screens()
        index = self._settings.screen
        if not self._fallback_to_primary and 0 <= index < len(screens):
            return screens[index]
        return QGuiApplication.primaryScreen()

    def _bind_screen(self) -> None:
        screen = self._choose_screen()
        if screen is self._screen:
            return
        if self._screen is not None:
            try:
                self._screen.geometryChanged.disconnect(self._geometry_changed)
            except (RuntimeError, TypeError):
                pass
        self._screen = screen
        if screen is not None:
            screen.geometryChanged.connect(self._geometry_changed)

    def _screens_changed(self, *_: object) -> None:
        self._refresh()

    def _screen_removed(self, screen: QScreen) -> None:
        if screen is self._screen:
            self._fallback_to_primary = True
        self._refresh()

    def _geometry_changed(self, _: QRect) -> None:
        self._refresh()

    def target_geometry(self) -> QRect:
        screen = self._choose_screen()
        geo = screen.geometry() if screen is not None else QRect(0, 0, 800, 600)
        d = self._settings
        width = max(1, round(geo.width() * d.width_pct / 100))
        line_height = QFontMetrics(self._font).lineSpacing()
        height = (
            self._block_count * d.max_lines * line_height
            + (self._block_count - 1) * OVERLAY_BLOCK_GAP
            + 2 * OVERLAY_PADDING
        )
        x = geo.left() + (geo.width() - width) // 2
        if d.position == "top":
            y = geo.top() + d.margin_px
        else:
            # 依介面契約使用 QRect.bottom() 的含尾端座標。
            y = geo.bottom() - d.margin_px - height
        return QRect(x, y, width, max(1, height))

    def _refresh(self) -> None:
        self._bind_screen()
        geometry = self.target_geometry()
        if self.geometry() != geometry:
            self.setGeometry(geometry)
        self._cache_paths()
        self.update()

    def show_snapshot(self, snap: BoardSnapshot) -> None:
        self._snapshot = snap
        if snap.blocks:
            self._block_count = len(snap.blocks)
        self._refresh()
        self._animate_opacity(1.0 if snap.visible and not snap.empty else 0.0)

    def _animate_opacity(self, target: float) -> None:
        if target == self._opacity_target:
            return
        self._opacity_target = target
        self.fade_animation.stop()
        current = self.windowOpacity()
        if abs(current - target) < 0.001:
            self.setWindowOpacity(target)
            return
        self.fade_animation.setStartValue(current)
        self.fade_animation.setEndValue(target)
        self.fade_animation.start()

    @staticmethod
    def _cursor_x(line: object, position: int) -> float:
        result = line.cursorToX(position)
        # PySide6 版本可能將輸入／輸出參數包成 tuple。
        return float(result[0] if isinstance(result, tuple) else result)

    def _cache_paths(self) -> None:
        self._paths.clear()
        if self._snapshot.empty:
            return

        d = self._settings
        metrics = QFontMetrics(self._font)
        line_height = metrics.lineSpacing()
        block_height = d.max_lines * line_height
        available_width = max(1, self.width() - 2 * OVERLAY_PADDING)

        for block_index, block in enumerate(self._snapshot.blocks):
            if block_index >= self._block_count:
                break

            # Qt 的文字索引以 UTF-16 code unit 計算，不是 Python 字元數。
            text = block.text.replace("\r", " ").replace("\n", "\u2028")
            encoded = text.encode("utf-16-le")
            spans: list[tuple[int, int, bool]] = []
            formats: list[QTextLayout.FormatRange] = []
            offset = 0
            for span in block.spans:
                length = len(span.text.encode("utf-16-le")) // 2
                spans.append((offset, offset + length, span.dim))
                fmt = QTextCharFormat()
                fmt.setForeground(QColor(255, 255, 255, 153 if span.dim else 255))
                item = QTextLayout.FormatRange()
                item.start = offset
                item.length = length
                item.format = fmt
                formats.append(item)
                offset += length

            layout = QTextLayout(text, self._font)
            option = QTextOption()
            option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
            option.setAlignment(Qt.AlignmentFlag.AlignLeft)
            option.setTextDirection(Qt.LayoutDirection.LeftToRight)
            layout.setTextOption(option)
            layout.setFormats(formats)

            lines = []
            layout.beginLayout()
            while True:
                line = layout.createLine()
                if not line.isValid():
                    break
                line.setLineWidth(available_width)
                line.setPosition(QPointF(0, len(lines) * line_height))
                lines.append(line)
            layout.endLayout()

            kept = lines[-d.max_lines:]
            block_top = OVERLAY_PADDING + block_index * (
                block_height + OVERLAY_BLOCK_GAP
            )
            for row, line in enumerate(kept):
                start = line.textStart()
                end = start + line.textLength()
                left = OVERLAY_PADDING + (
                    available_width - line.naturalTextWidth()
                ) / 2
                baseline = block_top + row * line_height + metrics.ascent()

                for span_start, span_end, dim in spans:
                    a, b = max(start, span_start), min(end, span_end)
                    if a >= b:
                        continue
                    fragment = encoded[a * 2:b * 2].decode("utf-16-le")
                    fragment = fragment.replace("\u2028", "")
                    if not fragment:
                        continue
                    path = QPainterPath()
                    path.addText(
                        QPointF(left + self._cursor_x(line, a), baseline),
                        self._font,
                        fragment,
                    )
                    self._paths.append((path, dim))

    def paintEvent(self, event: QPaintEvent) -> None:
        if self._snapshot.empty:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        d = self._settings

        if d.style == "box":
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(
                QColor(0, 0, 0, round(255 * max(0.0, min(1.0, d.box_opacity))))
            )
            painter.drawRoundedRect(
                QRectF(self.rect()), OVERLAY_RADIUS, OVERLAY_RADIUS
            )

        stroke_width = (
            max(1.0, d.font_size / 9)
            if d.style == "outline"
            else max(1.0, d.font_size / 24)
        )
        pen = QPen(QColor(0, 0, 0, 235))
        pen.setWidthF(stroke_width)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)

        for path, dim in self._paths:
            painter.save()
            painter.setOpacity(0.6 if dim else 1.0)
            if d.style == "outline":
                painter.save()
                painter.translate(1.5, 2.0)
                painter.fillPath(path, QColor(0, 0, 0, 105))
                painter.restore()
            painter.strokePath(path, pen)
            painter.fillPath(path, QColor("white"))
            painter.restore()
        painter.end()
