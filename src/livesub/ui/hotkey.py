"""全域熱鍵（Windows RegisterHotKey）。其他平台回報不支援，改由系統匣操作。"""

from __future__ import annotations

import logging
import sys

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, Signal

log = logging.getLogger(__name__)

MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x0001, 0x0002, 0x4000
WM_HOTKEY = 0x0312

# id → (修飾鍵, 虛擬鍵碼, 名稱)
HOTKEYS = {
    1: (MOD_CONTROL | MOD_ALT, ord("H"), "toggle_panel"),
    2: (MOD_CONTROL | MOD_ALT, ord("S"), "start_stop"),
}


class _Filter(QAbstractNativeEventFilter):
    def __init__(self, callback):
        super().__init__()
        self._cb = callback

    def nativeEventFilter(self, event_type, message):
        if event_type in (b"windows_generic_MSG", "windows_generic_MSG"):
            from ctypes import wintypes

            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY:
                self._cb(int(msg.wParam))
                return True, 0
        return False, 0


class HotkeyManager(QObject):
    triggered = Signal(str)  # "toggle_panel" / "start_stop"

    def __init__(self, app):
        super().__init__()
        self._app = app
        self._registered: list[int] = []
        self._filter = None

    def register(self) -> bool:
        """全部註冊成功才回傳 True（例如被其他程式佔用就會失敗）。"""
        if sys.platform != "win32":
            return False
        import ctypes

        user32 = ctypes.windll.user32
        self._filter = _Filter(self._on_hotkey)
        self._app.installNativeEventFilter(self._filter)
        ok = True
        for hid, (mods, vk, name) in HOTKEYS.items():
            if user32.RegisterHotKey(None, hid, mods | MOD_NOREPEAT, vk):
                self._registered.append(hid)
            else:
                log.warning("熱鍵 %s 註冊失敗（可能被其他程式佔用）", name)
                ok = False
        return ok

    def _on_hotkey(self, hid: int) -> None:
        if hid in HOTKEYS:
            self.triggered.emit(HOTKEYS[hid][2])

    def unregister(self) -> None:
        if sys.platform != "win32":
            return
        import ctypes

        for hid in self._registered:
            ctypes.windll.user32.UnregisterHotKey(None, hid)
        self._registered.clear()
        if self._filter is not None:
            self._app.removeNativeEventFilter(self._filter)
            self._filter = None
