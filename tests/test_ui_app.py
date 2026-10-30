"""LiveSubApp 的整合行為（不跑真模型）。"""

import pytest

from livesub import profiles
from livesub.config import Settings
from livesub.core.status import PipelineState, PipelineStatus

from test_pipeline import FakeASR, FakeMT, FakeSource, FakeVAD

pytestmark = pytest.mark.gui


@pytest.fixture
def app(qtbot, tmp_path, monkeypatch):
    from livesub.ui import app as app_mod

    monkeypatch.setattr(app_mod, "list_input_devices", lambda: [])
    a = app_mod.LiveSubApp(
        app_mod.QApplication.instance(), Settings(), profiles.get("cpu-high"), tmp_path / "config.toml",
        pipeline_kwargs=dict(source_factory=lambda _s: FakeSource([(0.5, False)]),
                             asr_factory=lambda _p: FakeASR(), mt_factory=lambda _p, _s: FakeMT(),
                             vad_factory=FakeVAD, require_models=False),
    )
    a.hotkeys.unregister()
    yield a
    a._cancel_restart()
    a.pipeline.stop(wait=True)
    a.overlay.close()
    a.panel.hide()
    if a.tray:
        a.tray.hide()


def test_restart_retry_is_paced_not_a_feedback_loop(app, qtbot, monkeypatch):
    """舊推論還沒結束時，start() 會送出狀態事件；重試必須照 timer 節奏，不能被狀態事件連鎖觸發。"""
    calls = []

    def refused_start():
        calls.append(1)
        app.bridge.status.emit(PipelineStatus(state=PipelineState.IDLE, message="上一次的辨識還在結束中"))

    monkeypatch.setattr(app.pipeline, "start", refused_start)
    app._restart_pending = True
    app._on_status(PipelineStatus(state=PipelineState.IDLE))
    qtbot.wait(1300)
    assert 2 <= len(calls) <= 4  # 約每 0.5 秒一次
    app.stop()  # 使用者按停止：取消待重啟
    n = len(calls)
    qtbot.wait(700)
    assert len(calls) == n
    assert not app._restart_timer.isActive()
