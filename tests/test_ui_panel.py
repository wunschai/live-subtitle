from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSignalBlocker, Qt

from livesub.config import Settings
from livesub.core.status import PipelineState, PipelineStatus, STATE_LABELS
from livesub.ui.panel import ControlPanel

pytestmark = pytest.mark.gui


@pytest.fixture
def panel(qtbot):
    widget = ControlPanel(Settings())
    qtbot.addWidget(widget)
    return widget


@pytest.mark.parametrize("control,section,field,value", [
    ("mode_combo", "display", "mode", "original"),
    ("mode_combo", "display", "mode", "zh"),
    ("mode_combo", "display", "mode", "en"),
    ("position_combo", "display", "position", "top"),
    ("source_combo", "audio", "source_language", "zh"),
    ("source_combo", "audio", "source_language", "en"),
    ("style_combo", "display", "style", "outline"),
])
def test_combo_settings(panel, qtbot, control, section, field, value):
    combo = getattr(panel, control)
    with qtbot.waitSignal(panel.settingsChanged) as emitted:
        combo.setCurrentIndex(combo.findData(value))
    settings = emitted.args[0]
    assert isinstance(settings, Settings)
    assert getattr(getattr(settings, section), field) == value


@pytest.mark.parametrize("control,field,value,expected", [
    ("font_slider", "font_size", 64, 64),
    ("opacity_slider", "box_opacity", 37, 0.37),
    ("lines_spin", "max_lines", 3, 3),
    ("width_slider", "width_pct", 55, 55),
])
def test_numeric_settings(panel, qtbot, control, field, value, expected):
    with qtbot.waitSignal(panel.settingsChanged) as emitted:
        getattr(panel, control).setValue(value)
    assert getattr(emitted.args[0].display, field) == pytest.approx(expected)


def test_devices_programmatic_update_and_user_change(panel, qtbot):
    emissions = []
    panel.settingsChanged.connect(emissions.append)
    panel.set_devices(["麥克風 A", "麥克風 B", "麥克風 A"], "麥克風 B")
    assert emissions == []
    assert panel.device_combo.count() == 3
    assert panel.device_combo.itemText(0) == "系統預設"
    assert panel.device_combo.itemData(0) == ""
    assert panel.device_combo.currentData() == "麥克風 B"

    with qtbot.waitSignal(panel.settingsChanged) as emitted:
        panel.device_combo.setCurrentIndex(0)
    assert emitted.args[0].audio.device == ""


def test_missing_current_device_is_preserved(panel):
    panel.set_devices([], "尚未連接的麥克風")
    assert panel.device_combo.currentData() == "尚未連接的麥克風"


def test_screen_setting_without_second_physical_screen(panel, qtbot):
    with QSignalBlocker(panel.screen_combo):
        panel.screen_combo.addItem("測試螢幕（1920 × 1080）", 7)
    with qtbot.waitSignal(panel.settingsChanged) as emitted:
        panel.screen_combo.setCurrentIndex(panel.screen_combo.findData(7))
    assert emitted.args[0].display.screen == 7


def test_screen_refresh_does_not_emit(panel):
    emissions = []
    panel.settingsChanged.connect(emissions.append)
    panel._refresh_screens()
    assert emissions == []


def test_status_never_emits_settings(panel):
    emissions = []
    panel.settingsChanged.connect(emissions.append)
    status = PipelineStatus(
        state=PipelineState.RUNNING,
        profile_label="CPU 基本",
        latency_s=1.23,
        level=0.42,
        segmented=True,
        message="已切換備援模型",
    )
    panel.set_status(status)
    assert emissions == []
    assert STATE_LABELS[status.state] in panel.status_label.text()
    assert "CPU 基本" in panel.status_label.text()
    assert "延遲 1.2 秒" in panel.status_label.text()
    assert "分段字幕" in panel.status_label.text()
    assert panel.warning_label.text() == "已切換備援模型"
    assert not panel.warning_label.isHidden()
    assert panel.level_bar.value() == 42

    panel.set_status(PipelineStatus())
    assert panel.warning_label.isHidden()
    assert emissions == []


@pytest.mark.parametrize("state,text,enabled", [
    (PipelineState.IDLE, "開始", True),
    (PipelineState.LOADING, "停止", False),
    (PipelineState.RUNNING, "停止", True),
    (PipelineState.STOPPING, "停止", False),
    (PipelineState.FAILED, "開始", True),
])
def test_start_button_state(panel, state, text, enabled):
    panel.set_status(PipelineStatus(state=state))
    assert panel.start_button.text() == text
    assert panel.start_button.isEnabled() is enabled


def test_start_stop_signals(panel, qtbot):
    with qtbot.waitSignal(panel.startRequested):
        panel.start_button.click()
    panel.set_status(PipelineStatus(state=PipelineState.RUNNING))
    with qtbot.waitSignal(panel.stopRequested):
        panel.start_button.click()


def test_busy_button_does_not_emit(panel):
    calls = []
    panel.startRequested.connect(lambda: calls.append("start"))
    panel.stopRequested.connect(lambda: calls.append("stop"))
    for state in (PipelineState.LOADING, PipelineState.STOPPING):
        panel.set_status(PipelineStatus(state=state))
        panel.start_button.click()
    assert calls == []


def test_settings_are_deep_copies(qtbot):
    original = Settings()
    panel = ControlPanel(original)
    qtbot.addWidget(panel)
    emissions = []
    panel.settingsChanged.connect(emissions.append)

    panel.font_slider.setValue(50)
    first = emissions[-1]
    panel.font_slider.setValue(60)
    assert original.display.font_size == 36
    assert first.display.font_size == 50
    first.audio.device = "外部修改"

    panel.width_slider.setValue(70)
    assert emissions[-1].audio.device == ""
    assert emissions[-1].display.font_size == 60


def test_opacity_enabled_only_for_box(panel):
    panel.style_combo.setCurrentIndex(panel.style_combo.findData("outline"))
    assert not panel.opacity_slider.isEnabled()
    panel.style_combo.setCurrentIndex(panel.style_combo.findData("box"))
    assert panel.opacity_slider.isEnabled()


def test_hide_disabled_and_close_quits(qtbot):
    panel = ControlPanel(Settings(), can_hide=False)
    qtbot.addWidget(panel)
    assert not panel.hide_button.isEnabled()
    assert panel.hide_button.toolTip() == "系統匣與熱鍵都不可用，無法隱藏"
    panel.show()
    with qtbot.waitSignal(panel.quitRequested):
        assert panel.close()


def test_hide_button_and_close_hide(panel, qtbot):
    panel.show()
    with qtbot.waitSignal(panel.hideRequested):
        panel.hide_button.click()
    assert not panel.isVisible()

    panel.show()
    with qtbot.waitSignal(panel.hideRequested):
        panel.close()
    assert not panel.isVisible()


def test_hotkey_message(panel):
    panel.set_hotkey_ok(False)
    assert panel.hotkey_label.text() == "全域熱鍵註冊失敗，請用系統匣叫回面板"
    panel.set_hotkey_ok(True)
    assert "Ctrl+Alt+H" in panel.hotkey_label.text()
    assert "Ctrl+Alt+S" in panel.hotkey_label.text()


def test_advanced_expands_and_collapses(panel, qtbot):
    panel.show()
    qtbot.wait(10)
    assert panel.advanced_group.isHidden()
    collapsed = panel.height()
    panel.advanced_button.click()
    qtbot.wait(10)
    assert not panel.advanced_group.isHidden()
    assert panel.height() > collapsed
    panel.advanced_button.click()
    qtbot.wait(10)
    assert panel.advanced_group.isHidden()
    assert panel.height() <= collapsed


def test_panel_is_not_tool_window(panel):
    assert (
        panel.windowFlags() & Qt.WindowType.WindowType_Mask
    ) == Qt.WindowType.Window


def test_status_shows_translation_latency_and_overflows(panel):
    panel.set_status(PipelineStatus(state=PipelineState.RUNNING, profile_label="Intel 內顯",
                                    latency_s=2.0, mt_latency_s=0.12, overflows=3))
    text = panel.status_label.text() + panel.warning_label.text()
    assert "延遲 2.0 秒" in text and "譯 +0.1 秒" in text and "掉音 3" in text
