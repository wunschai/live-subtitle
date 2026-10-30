"""組裝：QApplication、字幕列、面板、系統匣、熱鍵與 Pipeline。"""

from __future__ import annotations

import copy
import logging
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from .. import config, profiles
from ..config import Settings
from ..core.audio import FileSource, MicSource, list_input_devices
from ..core.pipeline import Pipeline
from ..core.status import PipelineState, PipelineStatus
from .hotkey import HotkeyManager
from .overlay import OverlayWindow
from .panel import ControlPanel
from .tray import TrayIcon

log = logging.getLogger(__name__)


class _Bridge(QObject):
    """把 pipeline 執行緒的 callback 轉成 GUI 執行緒的 queued signal。"""

    snapshot = Signal(object)
    status = Signal(object)


class LiveSubApp(QObject):
    def __init__(self, qapp: QApplication, settings: Settings, profile: profiles.Profile,
                 config_path: Path | None = None, source_file: str | None = None, pipeline_kwargs=None):
        super().__init__()
        self.qapp = qapp
        self.settings = settings
        self.config_path = config_path
        self.source_file = source_file
        self._status = PipelineStatus(profile=profile.name, profile_label=profile.label)
        self._restart_pending = False
        self._quitting = False

        self.bridge = _Bridge()
        self.bridge.snapshot.connect(self._on_snapshot, Qt.ConnectionType.QueuedConnection)
        self.bridge.status.connect(self._on_status, Qt.ConnectionType.QueuedConnection)

        self.overlay = OverlayWindow(settings.display)

        self.tray: TrayIcon | None = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = TrayIcon()
            self.tray.toggleRequested.connect(self.toggle_panel)
            self.tray.startStopRequested.connect(self.start_stop)
            self.tray.quitRequested.connect(self.quit)

        self.hotkeys = HotkeyManager(qapp)
        hotkeys_ok = settings.runtime.hotkeys and self.hotkeys.register()
        self.hotkeys.triggered.connect(self._on_hotkey)

        can_hide = self.tray is not None or hotkeys_ok
        self.panel = ControlPanel(settings, can_hide=can_hide)
        self.panel.set_hotkey_ok(hotkeys_ok)
        self.panel.settingsChanged.connect(self._on_settings)
        self.panel.startRequested.connect(self.start)
        self.panel.stopRequested.connect(self.stop)
        self.panel.quitRequested.connect(self.quit)
        self.panel.set_devices([d.name for d in list_input_devices()], settings.audio.device)
        self.panel.set_status(self._status)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(500)
        self._save_timer.timeout.connect(self._save)

        kw = {"source_factory": self._make_source, **(pipeline_kwargs or {})}  # 測試可注入假元件
        self.pipeline = Pipeline(settings, profile, self.bridge.snapshot.emit, self.bridge.status.emit, **kw)
        qapp.aboutToQuit.connect(self._shutdown)

    # ---- 顯示 ----------------------------------------------------------
    def show(self) -> None:
        self.overlay.show()
        self.panel.show()
        if self.tray is not None:
            self.tray.show()

    def toggle_panel(self) -> None:
        if self.panel.isVisible():
            if self.panel.can_hide:
                self.panel.hide()
        else:
            self.panel.show()
            self.panel.raise_()
            self.panel.activateWindow()

    def _on_snapshot(self, snap) -> None:
        self.overlay.show_snapshot(snap)

    def _on_status(self, st: PipelineStatus) -> None:
        self._status = st
        self.panel.set_status(st)
        if self.tray is not None:
            self.tray.set_running(st.running)
        if self._restart_pending and st.state in (PipelineState.IDLE, PipelineState.FAILED):
            self._restart_pending = False
            self.pipeline.start()

    # ---- 控制 ----------------------------------------------------------
    def _make_source(self, s: Settings):
        if self.source_file:
            return FileSource(self.source_file, realtime=True)
        return MicSource(s.audio.device)

    def start(self) -> None:
        self.pipeline.start()

    def stop(self) -> None:
        self._restart_pending = False
        self.pipeline.stop()

    def start_stop(self) -> None:
        if self._status.state in (PipelineState.LOADING, PipelineState.STOPPING):
            return
        if self._status.running:
            self.stop()
        else:
            self.start()

    def _on_hotkey(self, name: str) -> None:
        if name == "toggle_panel":
            self.toggle_panel()
        elif name == "start_stop":
            self.start_stop()

    def _on_settings(self, s: Settings) -> None:
        device_changed = s.audio.device != self.settings.audio.device
        self.settings = copy.deepcopy(s)
        self.overlay.apply_settings(s.display)
        self.overlay.set_block_count(2 if s.display.mode == "bilingual" else 1)
        self.pipeline.update_settings(s)
        if device_changed and self.pipeline.state == PipelineState.RUNNING:
            self._restart_pending = True  # 停好之後在 _on_status 重新開始
            self.pipeline.stop()
        self._save_timer.start()

    def _save(self) -> None:
        try:
            config.save(self.settings, self.config_path)
        except OSError as e:
            log.warning("設定儲存失敗：%s", e)

    def quit(self) -> None:
        self.qapp.quit()

    def _shutdown(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        if self._save_timer.isActive():
            self._save_timer.stop()
            self._save()
        self.pipeline.stop(wait=True)
        self.hotkeys.unregister()
        if self.tray is not None:
            self.tray.hide()
