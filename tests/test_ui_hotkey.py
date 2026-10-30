"""全域熱鍵執行緒的生命週期（只在 Windows 跑，會短暫註冊真的熱鍵）。"""

import sys
import threading

import pytest

pytestmark = [pytest.mark.gui, pytest.mark.skipif(sys.platform != "win32", reason="Win32 熱鍵")]


def test_register_unregister_roundtrip(qtbot):
    from livesub.ui.hotkey import HotkeyManager

    hk = HotkeyManager()
    if not hk.register():
        pytest.skip("熱鍵被其他程式佔用")
    assert hk.ok and hk.thread_id
    hk.unregister()
    assert not hk.ok and hk._w is None
    assert hk.register()  # 撤銷乾淨：可以再註冊
    hk.unregister()


def test_registration_timeout_cancels_late_worker(qtbot, monkeypatch):
    """註冊等待逾時、worker 才開始跑：它必須看到取消旗標，不能留下活著的熱鍵執行緒。"""
    from livesub.ui import hotkey

    gate = threading.Event()
    real_loop = hotkey.HotkeyManager._loop

    def late_loop(self, w):
        gate.wait(5)
        real_loop(self, w)

    monkeypatch.setattr(hotkey.HotkeyManager, "_loop", late_loop)
    hk = hotkey.HotkeyManager()
    t_before = {t.ident for t in threading.enumerate()}
    assert hk.register(timeout=0.05) is False
    w = hk._w
    assert w is not None and w.cancel.is_set()  # 執行緒還沒結束：保留參照
    gate.set()
    w.thread.join(5)
    assert not w.thread.is_alive() and not w.ok
    hk.unregister()
    assert hk._w is None
    monkeypatch.setattr(hotkey.HotkeyManager, "_loop", real_loop)
    if hk.register():  # 晚啟動的 worker 沒有佔住熱鍵
        hk.unregister()
    assert not any(t.name == "livesub-hotkeys" and t.is_alive() for t in threading.enumerate()
                   if t.ident not in t_before)
