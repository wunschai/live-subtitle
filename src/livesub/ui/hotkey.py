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


class _Worker:
    """一次註冊對應一條執行緒；取消旗標只屬於這一次，晚啟動的執行緒也看得到。"""

    def __init__(self) -> None:
        self.ready = threading.Event()
        self.cancel = threading.Event()
        self.tid = 0  # 訊息佇列建立後才設定：有 tid 就代表 PostThreadMessage 送得到
        self.ok = False
        self.thread: threading.Thread | None = None


class HotkeyManager(QObject):
    triggered = Signal(str)  # "toggle_panel" / "start_stop"；從熱鍵執行緒 emit，Qt 自動排回主執行緒

    def __init__(self, app=None):
        super().__init__()
        self._w: _Worker | None = None

    def register(self, timeout: float = 3.0) -> bool:
        """全部註冊成功才回傳 True（例如被其他程式佔用就會失敗，並全部撤銷）。"""
        if sys.platform != "win32":
            return False
        if self._w is not None:
            return self.ok
        w = _Worker()
        w.thread = threading.Thread(target=self._loop, args=(w,), name="livesub-hotkeys", daemon=True)
        self._w = w
        w.thread.start()
        if not (w.ready.wait(timeout) and w.ok):
            self.unregister()
            return False
        return True

    @property
    def ok(self) -> bool:
        w = self._w
        return w is not None and w.ok and not w.cancel.is_set()

    def _loop(self, w: _Worker) -> None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        msg = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)  # 建立這條執行緒的訊息佇列
        w.tid = ctypes.windll.kernel32.GetCurrentThreadId()
        registered = []
        try:
            if not w.cancel.is_set():
                for hid, (mods, vk, name) in HOTKEYS.items():
                    if user32.RegisterHotKey(None, hid, mods | MOD_NOREPEAT, vk):
                        registered.append(hid)
                    else:
                        log.warning("熱鍵 %s 註冊失敗（可能被其他程式佔用）", name)
                        break
            # 先設好 tid 才檢查取消：unregister 若在這之後才取消，一定看得到 tid 並送出 WM_QUIT
            w.ok = len(registered) == len(HOTKEYS) and not w.cancel.is_set()
            w.ready.set()
            if w.ok:
                while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                    if w.cancel.is_set():
                        break
                    if msg.message == WM_HOTKEY and int(msg.wParam) in HOTKEYS:
                        self.triggered.emit(HOTKEYS[int(msg.wParam)][2])
        finally:
            w.ok = False
            w.ready.set()
            for hid in registered:  # RegisterHotKey 綁執行緒，必須在同一條執行緒撤銷
                user32.UnregisterHotKey(None, hid)

    @property
    def thread_id(self) -> int:
        w = self._w
        return w.tid if w is not None else 0

    def unregister(self, timeout: float = 2.0) -> None:
        w = self._w
        if w is None:
            return
        w.cancel.set()
        t = w.thread
        if t is not None and t.is_alive():
            if w.tid:
                import ctypes

                if not ctypes.windll.user32.PostThreadMessageW(w.tid, WM_QUIT, 0, 0):
                    log.debug("熱鍵執行緒的退出訊息送不到（執行緒可能正要結束）")
            t.join(timeout=timeout)
        if t is not None and t.is_alive():
            # 還沒退出就保留參照：它看到取消旗標後會自己撤銷熱鍵，也避免重複註冊互相衝突
            log.warning("熱鍵執行緒未在 %.1f 秒內結束", timeout)
            return
        self._w = None
