"""Monkey 測試：像人一樣亂按面板、系統匣、熱鍵，同時 pipeline 在跑。

固定種子可重現；任何例外、或字幕列幾何出現非有限值／非正值都算失敗。
"""

import math
import random
import sys

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QPushButton, QSlider, QSpinBox, QToolButton

from livesub import profiles
from livesub.config import Settings
from livesub.core.status import PipelineState

from test_pipeline import FakeASR, FakeMT, FakeSource, FakeVAD

pytestmark = pytest.mark.gui

PATTERN = [(0.3, False)] + [(0.8, True), (0.7, False)] * 40


@pytest.fixture
def app(qtbot, tmp_path, monkeypatch):
    from livesub.ui import app as app_mod

    monkeypatch.setattr(app_mod, "list_input_devices", lambda: [])
    s = Settings()
    s.display.clear_after_s = 1.0
    asr, mt = FakeASR(partial_delay=0.005), FakeMT()
    a = app_mod.LiveSubApp(
        app_mod.QApplication.instance(), s, profiles.get("intel-gpu-lite"), tmp_path / "config.toml",
        pipeline_kwargs=dict(
            source_factory=lambda _s: FakeSource(PATTERN, realtime_factor=0.25),
            asr_factory=lambda _p: asr, mt_factory=lambda _p, _s: mt, vad_factory=FakeVAD,
            require_models=False,
        ),
    )
    a.hotkeys.unregister()  # 測試中不佔用真的全域熱鍵
    a.show()
    yield a
    a.pipeline.stop(wait=True)
    a.overlay.close()
    a.panel.hide()
    if a.tray:
        a.tray.hide()


def _actions(a):
    p = a.panel
    widgets = [w for cls in (QComboBox, QSlider, QSpinBox, QPushButton, QToolButton) for w in p.findChildren(cls)]

    def poke(rng):
        w = rng.choice(widgets)
        if isinstance(w, QComboBox) and w.count():
            w.setCurrentIndex(rng.randrange(w.count()))
        elif isinstance(w, QSlider):
            w.setValue(rng.randint(w.minimum() - 5, w.maximum() + 5))  # 含超出範圍的值
        elif isinstance(w, QSpinBox):
            w.setValue(rng.randint(w.minimum() - 2, w.maximum() + 2))
        elif isinstance(w, (QPushButton, QToolButton)):
            for _ in range(rng.choice([1, 1, 3])):  # 偶爾連點三下
                w.click()

    return [
        (8, poke),
        (2, lambda rng: a.start_stop()),
        (1, lambda rng: a.toggle_panel()),
        (1, lambda rng: a._on_hotkey(rng.choice(["toggle_panel", "start_stop", "bogus"]))),
        (1, lambda rng: a.tray and rng.choice([a.tray.toggleRequested, a.tray.startStopRequested]).emit()),
        (1, lambda rng: a.panel.close()),
        (1, lambda rng: a.pipeline.stop()),
        (1, lambda rng: a.pipeline.start()),
    ]


def _check_geometry(a, trail):
    g = a.overlay.target_geometry()
    for v in (g.x(), g.y(), g.width(), g.height()):
        assert math.isfinite(v), trail[-20:]
    assert g.width() > 0 and g.height() > 0, trail[-20:]
    op = a.overlay.windowOpacity()
    assert 0.0 <= op <= 1.0, trail[-20:]


@pytest.mark.parametrize("seed", range(10))
def test_monkey(app, qtbot, seed, monkeypatch):
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda *exc: errors.append(exc))
    rng = random.Random(seed)
    acts = _actions(app)
    weights = [w for w, _ in acts]
    trail = []
    for step in range(300):
        i = rng.choices(range(len(acts)), weights)[0]
        trail.append(i)
        acts[i][1](rng)
        if rng.random() < 0.15:
            qtbot.wait(rng.choice([1, 5, 30]))
        if step % 25 == 0:
            _check_geometry(app, trail)
        assert not errors, (errors[0], trail[-20:])
    # 收尾：一定能停下來，也一定能重新開始並出字幕
    app.stop()
    assert app.pipeline.wait_idle(10)
    s = Settings()
    s.display.mode = "zh"
    s.display.clear_after_s = 0
    app.panel.mode_combo.setCurrentIndex(app.panel.mode_combo.findData("zh"))
    app.start()
    qtbot.waitUntil(lambda: app.pipeline.state == PipelineState.RUNNING, timeout=10000)
    qtbot.waitUntil(lambda: "句" in "".join(b.text for b in app.overlay._snapshot.blocks), timeout=15000)
    _check_geometry(app, trail)
    assert not errors


def test_settings_saved_with_debounce(app, qtbot, tmp_path):
    from livesub import config

    app.panel.font_slider.setValue(60)
    app.panel.font_slider.setValue(61)
    qtbot.waitUntil(lambda: (tmp_path / "config.toml").exists(), timeout=3000)
    assert config.load(tmp_path / "config.toml").display.font_size == 61


def test_hide_and_restore_panel(app, qtbot):
    assert app.panel.isVisible()
    app.panel.hide_button.click()
    assert not app.panel.isVisible()
    app._on_hotkey("toggle_panel")
    assert app.panel.isVisible()


def test_cannot_hide_without_tray_and_hotkeys(app):
    app.panel.set_can_hide(False)
    app.toggle_panel()
    assert app.panel.isVisible()
    assert not app.panel.hide_button.isEnabled()


def test_device_change_restarts_pipeline(app, qtbot):
    app.start()
    qtbot.waitUntil(lambda: app.pipeline.state == PipelineState.RUNNING, timeout=10000)
    s = Settings()
    s.audio.device = "另一支麥克風"
    app._on_settings(s)
    qtbot.waitUntil(lambda: app.pipeline.state == PipelineState.RUNNING and not app._restart_pending,
                    timeout=15000)


def test_overlay_receives_bilingual_snapshot(app, qtbot):
    app.panel.mode_combo.setCurrentIndex(app.panel.mode_combo.findData("bilingual"))
    app.start()
    qtbot.waitUntil(lambda: len(app.overlay._snapshot.blocks) == 2
                    and "[en]" in app.overlay._snapshot.blocks[1].text, timeout=15000)
    assert app.overlay.target_geometry().height() > 0
    assert app.overlay.windowFlags() & Qt.WindowType.WindowTransparentForInput
