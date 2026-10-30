"""全域熱鍵（Windows RegisterHotKey）。其他平台回報不支援，改由系統匣操作。

實測 Qt 的事件迴圈收不到 RegisterHotKey 的執行緒訊息（WM_HOTKEY 在 native event filter 前就被吃掉），
所以熱鍵放在一條獨立執行緒，自己跑 Win32 訊息迴圈（GetMessageW），收到後用 Qt signal 轉回主執行緒。
"""

from __future__ import annotations

import logging
import sys
import threading

from PySide6.QtCore import QObject, Signal

log = logging.getLogger(__name__)

MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x0001, 0x0002, 0x4000
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012

# id → (修飾鍵, 虛擬鍵碼, 名稱)
HOTKEYS = {
    1: (MOD_CONTROL | MOD_ALT, ord("H"), "toggle_panel"),
    2: (MOD_CONTROL | MOD_ALT, ord("S"), "start_stop"),
}


class HotkeyManager(QObject):
    triggered = Signal(str)  # "toggle_panel" / "start_stop"；從熱鍵執行緒 emit，Qt 自動排回主執行緒

    def __init__(self, app=None):
        super().__init__()
        self._thread: threading.Thread | None = None
        self._tid = 0
        self._ok = False

    def register(self, timeout: float = 3.0) -> bool:
        """全部註冊成功才回傳 True（例如被其他程式佔用就會失敗，並全部撤銷）。"""
        if sys.platform != "win32" or self._thread is not None:
            return self._ok
        ready = threading.Event()
        self._thread = threading.Thread(target=self._loop, args=(ready,), name="livesub-hotkeys", daemon=True)
        self._thread.start()
        ready.wait(timeout)
        if not self._ok:
            self.unregister()
        return self._ok

    def _loop(self, ready: threading.Event) -> None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        self._tid = ctypes.windll.kernel32.GetCurrentThreadId()
        msg = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)  # 建立這條執行緒的訊息佇列
        registered = []
        for hid, (mods, vk, name) in HOTKEYS.items():
            if user32.RegisterHotKey(None, hid, mods | MOD_NOREPEAT, vk):
                registered.append(hid)
            else:
                log.warning("熱鍵 %s 註冊失敗（可能被其他程式佔用）", name)
                break
        self._ok = len(registered) == len(HOTKEYS)
        ready.set()
        try:
            if self._ok:
                while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                    if msg.message == WM_HOTKEY and int(msg.wParam) in HOTKEYS:
                        self.triggered.emit(HOTKEYS[int(msg.wParam)][2])
        finally:
            for hid in registered:  # RegisterHotKey 綁執行緒，必須在同一條執行緒撤銷
                user32.UnregisterHotKey(None, hid)

    @property
    def thread_id(self) -> int:
        return self._tid

    def unregister(self) -> None:
        t, self._thread = self._thread, None
        if t is None:
            return
        if t.is_alive() and self._tid:
            import ctypes

            ctypes.windll.user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
            t.join(timeout=2)
        self._ok = False
        self._tid = 0
