"""繁體中文控制面板。"""

from __future__ import annotations

import copy
import math

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtGui import QCloseEvent, QGuiApplication
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from livesub.config import Settings
from livesub.core.status import PipelineState, PipelineStatus, STATE_LABELS

from .theme import PANEL_QSS, make_font


class ControlPanel(QWidget):
    settingsChanged = Signal(object)
    startRequested = Signal()
    stopRequested = Signal()
    hideRequested = Signal()
    quitRequested = Signal()

    def __init__(self, settings: Settings, can_hide: bool = True):
        super().__init__(None, Qt.WindowType.WindowStaysOnTopHint)
        self.setObjectName("controlPanel")
        self.setWindowTitle("即時字幕")
        self.setFont(make_font(settings.display.font_family, 12))
        self.setStyleSheet(PANEL_QSS)
        self.setMinimumWidth(320)
        self._settings = copy.deepcopy(settings)
        self._status = PipelineStatus()
        self.can_hide = can_hide

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(5)

        self.start_button = QPushButton("開始")
        self.start_button.setObjectName("startButton")
        self.start_button.setMinimumHeight(34)
        self.start_button.clicked.connect(self._start_stop)
        root.addWidget(self.start_button)

        self.status_label = QLabel()
        self.status_label.setObjectName("statusLabel")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        self.warning_label = QLabel()
        self.warning_label.setObjectName("warningLabel")
        self.warning_label.setWordWrap(True)
        root.addWidget(self.warning_label)

        self.hotkey_label = QLabel()
        self.hotkey_label.setObjectName("hotkeyLabel")
        self.hotkey_label.setFont(make_font(settings.display.font_family, 10))
        self.hotkey_label.setWordWrap(True)
        root.addWidget(self.hotkey_label)

        main_form = QFormLayout()
        main_form.setSpacing(5)
        main_form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )
        root.addLayout(main_form)

        self.mode_combo = self._combo(
            [("原文", "original"), ("中文", "zh"),
             ("英文", "en"), ("中英雙語", "bilingual")],
            settings.display.mode,
        )
        self.position_combo = self._combo(
            [("上方", "top"), ("下方", "bottom")],
            settings.display.position,
        )
        self.font_slider, self.font_value, font_row = self._slider(
            16, 96, settings.display.font_size, " px"
        )
        main_form.addRow("顯示模式", self.mode_combo)
        main_form.addRow("位置", self.position_combo)
        main_form.addRow("字級", font_row)

        footer = QHBoxLayout()
        self.advanced_button = QToolButton()
        self.advanced_button.setText("進階")
        self.advanced_button.setCheckable(True)
        self.advanced_button.setArrowType(Qt.ArrowType.RightArrow)
        self.advanced_button.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        footer.addWidget(self.advanced_button)
        footer.addStretch()
        self.hide_button = QPushButton("隱藏面板")
        self.hide_button.setEnabled(can_hide)
        if not can_hide:
            self.hide_button.setToolTip("系統匣與熱鍵都不可用，無法隱藏")
        self.hide_button.clicked.connect(self._hide_panel)
        footer.addWidget(self.hide_button)
        root.addLayout(footer)

        self.advanced_group = QGroupBox("進階設定")
        advanced_form = QFormLayout(self.advanced_group)
        advanced_form.setSpacing(6)
        advanced_form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )
        root.addWidget(self.advanced_group)

        self.device_combo = self._combo([("系統預設", "")], "")
        self.level_bar = QProgressBar()
        self.level_bar.setRange(0, 100)
        self.level_bar.setTextVisible(False)
        self.level_bar.setAccessibleName("麥克風音量")

        self.source_combo = self._combo(
            [("自動", "auto"), ("中文", "zh"), ("英文", "en")],
            settings.audio.source_language,
        )
        self.style_combo = self._combo(
            [("直接顯示（描邊）", "outline"), ("半透明底框", "box")],
            settings.display.style,
        )
        self.opacity_slider, self.opacity_value, opacity_row = self._slider(
            0, 100, round(settings.display.box_opacity * 100), "%"
        )
        self.lines_spin = QSpinBox()
        self.lines_spin.setRange(1, 3)
        with QSignalBlocker(self.lines_spin):
            self.lines_spin.setValue(settings.display.max_lines)
        self.width_slider, self.width_value, width_row = self._slider(
            30, 100, settings.display.width_pct, "%"
        )
        self.screen_combo = QComboBox()
        self.screen_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.screen_combo.setMinimumContentsLength(12)
        self.screen_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )

        advanced_form.addRow("麥克風", self.device_combo)
        advanced_form.addRow("音量", self.level_bar)
        advanced_form.addRow("來源語言", self.source_combo)
        advanced_form.addRow("樣式", self.style_combo)
        advanced_form.addRow("底框不透明度", opacity_row)
        advanced_form.addRow("每區行數", self.lines_spin)
        advanced_form.addRow("字幕寬度", width_row)
        advanced_form.addRow("螢幕", self.screen_combo)

        for control, section, field in (
            (self.mode_combo, "display", "mode"),
            (self.position_combo, "display", "position"),
            (self.device_combo, "audio", "device"),
            (self.source_combo, "audio", "source_language"),
            (self.style_combo, "display", "style"),
            (self.screen_combo, "display", "screen"),
        ):
            control.currentIndexChanged.connect(
                lambda _index, c=control, s=section, f=field:
                self._combo_changed(c, s, f)
            )

        self.font_slider.valueChanged.connect(
            lambda value: self._change("display", "font_size", value)
        )
        self.opacity_slider.valueChanged.connect(
            lambda value: self._change("display", "box_opacity", value / 100)
        )
        self.lines_spin.valueChanged.connect(
            lambda value: self._change("display", "max_lines", value)
        )
        self.width_slider.valueChanged.connect(
            lambda value: self._change("display", "width_pct", value)
        )
        self.advanced_button.toggled.connect(self._set_advanced)
        self.advanced_group.hide()

        app = QGuiApplication.instance()
        if app is not None:
            app.screenAdded.connect(self._refresh_screens)
            app.screenRemoved.connect(self._refresh_screens)
        self._watched_screens = []
        self._refresh_screens()
        self.set_devices([], settings.audio.device)
        self._update_opacity_enabled()
        self.set_hotkey_ok(True)
        self.set_status(self._status)
        self.resize(320, 260)

    @staticmethod
    def _combo(items: list[tuple[str, object]], current: object) -> QComboBox:
        combo = QComboBox()
        with QSignalBlocker(combo):
            for label, value in items:
                combo.addItem(label, value)
            index = combo.findData(current)
            combo.setCurrentIndex(max(0, index))
        return combo

    @staticmethod
    def _slider(
        minimum: int, maximum: int, value: int, suffix: str
    ) -> tuple[QSlider, QLabel, QWidget]:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(minimum, maximum)
        with QSignalBlocker(slider):
            slider.setValue(value)
        label = QLabel(f"{slider.value()}{suffix}")
        label.setMinimumWidth(43)
        label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        slider.valueChanged.connect(
            lambda number: label.setText(f"{number}{suffix}")
        )
        layout.addWidget(slider, 1)
        layout.addWidget(label)
        return slider, label, row

    def _combo_changed(
        self, control: QComboBox, section: str, field: str
    ) -> None:
        if control.currentIndex() >= 0:
            self._change(section, field, control.currentData())

    def _change(self, section: str, field: str, value: object) -> None:
        target = getattr(self._settings, section)
        if getattr(target, field) == value:
            return
        setattr(target, field, value)
        if section == "display" and field == "style":
            self._update_opacity_enabled()
        self.settingsChanged.emit(copy.deepcopy(self._settings))

    def _update_opacity_enabled(self) -> None:
        enabled = self._settings.display.style == "box"
        self.opacity_slider.setEnabled(enabled)
        self.opacity_value.setEnabled(enabled)

    def _set_advanced(self, expanded: bool) -> None:
        self.advanced_button.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )
        self.advanced_group.setVisible(expanded)
        self.layout().activate()
        self.resize(self.width(), self.sizeHint().height())

    def _refresh_screens(self, *_: object) -> None:
        for screen in self._watched_screens:
            try:
                screen.geometryChanged.disconnect(self._refresh_screens)
            except (RuntimeError, TypeError):
                pass
        self._watched_screens = list(QGuiApplication.screens())
        with QSignalBlocker(self.screen_combo):
            self.screen_combo.clear()
            for index, screen in enumerate(self._watched_screens):
                geo = screen.geometry()
                name = screen.name() or f"螢幕 {index + 1}"
                self.screen_combo.addItem(
                    f"{name}（{geo.width()} × {geo.height()}）", index
                )
            selected = self.screen_combo.findData(self._settings.display.screen)
            if selected < 0:
                primary = QGuiApplication.primaryScreen()
                selected = next(
                    (i for i, screen in enumerate(self._watched_screens)
                     if screen is primary),
                    -1,
                )
            self.screen_combo.setCurrentIndex(selected)
        for screen in self._watched_screens:
            screen.geometryChanged.connect(self._refresh_screens)

    def set_devices(self, devices: list[str], current: str) -> None:
        names = list(dict.fromkeys(name for name in devices if name))
        if current and current not in names:
            names.append(current)
        with QSignalBlocker(self.device_combo):
            self.device_combo.clear()
            self.device_combo.addItem("系統預設", "")
            for name in names:
                self.device_combo.addItem(name, name)
            self.device_combo.setCurrentIndex(self.device_combo.findData(current))
        self._settings.audio.device = current

    def set_status(self, status: PipelineStatus) -> None:
        self._status = status
        busy = status.state in (PipelineState.LOADING, PipelineState.STOPPING)
        with QSignalBlocker(self.start_button):
            self.start_button.setText(
                "停止" if status.running or status.state == PipelineState.STOPPING
                else "開始"
            )
            self.start_button.setEnabled(not busy)

        parts = [STATE_LABELS[status.state]]
        if status.profile_label:
            parts.append(status.profile_label)
        if status.latency_s is not None and math.isfinite(status.latency_s):
            lat = f"延遲 {max(0.0, status.latency_s):.1f} 秒"
            if status.mt_latency_s is not None and math.isfinite(status.mt_latency_s):
                lat += f"（譯 +{max(0.0, status.mt_latency_s):.1f} 秒）"
            parts.append(lat)
        if status.segmented:
            parts.append("分段字幕")
        self.status_label.setText(" · ".join(parts))
        warnings = [status.message] if status.message else []
        if status.overflows > 0:
            warnings.append(f"掉音 {status.overflows} 次（電腦太忙，可降低等級）")
        self.warning_label.setText("；".join(warnings))
        self.warning_label.setVisible(bool(warnings))
        level = status.level if math.isfinite(status.level) else 0.0
        with QSignalBlocker(self.level_bar):
            self.level_bar.setValue(round(max(0.0, min(1.0, level)) * 100))

    def set_can_hide(self, can_hide: bool) -> None:
        self.can_hide = can_hide
        self.hide_button.setEnabled(can_hide)
        self.hide_button.setToolTip("" if can_hide else "系統匣與熱鍵都不可用，無法隱藏")

    def set_hotkey_ok(self, ok: bool) -> None:
        self.hotkey_label.setText(
            "Ctrl+Alt+H 顯示/隱藏面板 · Ctrl+Alt+S 開始/停止"
            if ok else "全域熱鍵註冊失敗，請用系統匣叫回面板"
        )

    def _start_stop(self) -> None:
        if self._status.state in (PipelineState.LOADING, PipelineState.STOPPING):
            return
        if self._status.running:
            self.stopRequested.emit()
        else:
            self.startRequested.emit()

    def _hide_panel(self) -> None:
        if self.can_hide:
            self.hide()
            self.hideRequested.emit()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.can_hide:
            event.ignore()
            self._hide_panel()
        else:
            event.accept()
            self.quitRequested.emit()
