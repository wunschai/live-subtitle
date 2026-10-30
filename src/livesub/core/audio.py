"""音訊來源：麥克風（sounddevice）與 WAV 檔（測試／示範）。

輸出契約：16 kHz、單聲道、float32、範圍約 -1..1 的 numpy 陣列區塊。
"""

from __future__ import annotations

import logging
import queue
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .vad import SAMPLE_RATE

log = logging.getLogger(__name__)

# 各平台偏好的 host API（同一支麥克風會在多個 API 下重複出現，只列一個）
_PREFERRED_HOSTAPIS = {
    "windows": ("Windows WASAPI", "MME", "Windows DirectSound"),
    "darwin": ("Core Audio",),
    "linux": ("PulseAudio", "ALSA", "JACK Audio Connection Kit"),
}


class Resampler:
    """有狀態的線性內插重採樣（保留區塊間的相位與最後一個樣本）。語音辨識夠用。"""

    def __init__(self, src_rate: int, dst_rate: int = SAMPLE_RATE):
        self.src = int(src_rate)
        self.dst = int(dst_rate)
        self._t = 0.0  # 下一個輸出樣本的位置（以輸入樣本的絕對索引表示）
        self._prev = np.zeros(0, np.float32)  # 上一塊的最後一個樣本
        self._prev_idx = 0  # _prev[0] 的絕對索引

    def process(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, np.float32).reshape(-1)
        if self.src == self.dst or x.size == 0:
            return x
        buf = np.concatenate([self._prev, x])
        buf_start = self._prev_idx
        last = buf_start + len(buf) - 1  # 目前可用的最後一個樣本
        step = self.src / self.dst
        if self._t > last:
            out = np.zeros(0, np.float32)
        else:
            n = int(np.floor((last - self._t) / step)) + 1
            t = self._t + step * np.arange(n)
            out = np.interp(t - buf_start, np.arange(len(buf)), buf).astype(np.float32)
            self._t += step * n
        self._prev = buf[-1:]
        self._prev_idx = last
        return out


@dataclass(frozen=True)
class InputDevice:
    index: int
    name: str
    hostapi: str
    default_samplerate: float
    channels: int


def list_input_devices() -> list[InputDevice]:
    """列出輸入裝置，只保留偏好 host API 下的項目（避免同一支麥克風出現 4 次）。"""
    import sys

    import sounddevice as sd

    try:
        devices = sd.query_devices()
        hostapis = sd.query_hostapis()
    except Exception as e:  # pragma: no cover - 沒有音訊子系統
        log.warning("無法列出音訊裝置：%s", e)
        return []
    plat = {"win32": "windows", "darwin": "darwin"}.get(sys.platform, "linux")
    names = [h["name"] for h in hostapis]
    chosen = None
    for pref in _PREFERRED_HOSTAPIS[plat]:
        if pref in names:
            idx = names.index(pref)
            if any(d["hostapi"] == idx and d["max_input_channels"] > 0 for d in devices):
                chosen = idx
                break
    out = []
    for i, d in enumerate(devices):
        if d["max_input_channels"] <= 0:
            continue
        if chosen is not None and d["hostapi"] != chosen:
            continue
        out.append(InputDevice(i, d["name"], names[d["hostapi"]], float(d["default_samplerate"]),
                               int(d["max_input_channels"])))
    return out


class AudioSource:
    """共同介面：start() 後 read() 取得區塊，stop() 結束。"""

    overflows = 0

    def start(self) -> None: ...

    def read(self, timeout: float = 0.2) -> np.ndarray | None:
        raise NotImplementedError

    def stop(self) -> None: ...

    @property
    def finished(self) -> bool:
        return False


class MicSource(AudioSource):
    def __init__(self, device_name: str = "", block_ms: int = 32, max_blocks: int = 200):
        self.device_name = device_name
        self.block_ms = block_ms
        self._q: queue.Queue[np.ndarray] = queue.Queue(maxsize=max_blocks)
        self._stream = None
        self._resampler: Resampler | None = None
        self.overflows = 0
        self.level = 0.0
        self._last_data = time.monotonic()

    def _resolve_device(self):
        if not self.device_name:
            return None, None
        for d in list_input_devices():
            if d.name == self.device_name:
                return d.index, d
        log.warning("找不到麥克風「%s」，改用系統預設", self.device_name)
        return None, None

    def start(self) -> None:
        import sounddevice as sd

        idx, dev = self._resolve_device()
        if dev is not None:
            rate = int(dev.default_samplerate)
        else:
            info = sd.query_devices(kind="input")
            rate = int(info["default_samplerate"])
        self._resampler = Resampler(rate, SAMPLE_RATE)

        def callback(indata, frames, time_info, status):  # 在音訊執行緒：只複製、不阻塞
            if status and status.input_overflow:
                self.overflows += 1
            mono = indata[:, 0].copy() if indata.ndim > 1 else indata.copy()
            try:
                self._q.put_nowait(mono)
            except queue.Full:
                self.overflows += 1

        self._stream = sd.InputStream(device=idx, channels=1, samplerate=rate, dtype="float32",
                                      blocksize=int(rate * self.block_ms / 1000), callback=callback)
        self._stream.start()
        self._last_data = time.monotonic()

    STALL_S = 3.0  # 這麼久沒有任何音訊區塊就檢查串流是否還活著

    def read(self, timeout: float = 0.2) -> np.ndarray | None:
        try:
            raw = self._q.get(timeout=timeout)
        except queue.Empty:
            if self._stream is not None and time.monotonic() - self._last_data > self.STALL_S:
                if not self._stream.active:  # 裝置被拔掉或驅動停了：callback 不會再來
                    raise OSError("麥克風串流已中斷（裝置被拔除？）")
                self._last_data = time.monotonic()
            return None
        self._last_data = time.monotonic()
        out = self._resampler.process(raw) if self._resampler else raw
        if out.size:
            self.level = float(min(1.0, np.sqrt(np.mean(out ** 2)) * 6))
        return out

    def stop(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception as e:  # pragma: no cover
                log.warning("關閉麥克風失敗：%s", e)
            self._stream = None


def read_wav(path: str | Path) -> np.ndarray:
    """讀 PCM WAV（8/16/24/32-bit、任意聲道與取樣率）→ 16 kHz 單聲道 float32。"""
    with wave.open(str(path), "rb") as w:
        ch, width, rate, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        data = w.readframes(n)
    if width == 1:
        a = (np.frombuffer(data, np.uint8).astype(np.float32) - 128) / 128
    elif width == 2:
        a = np.frombuffer(data, "<i2").astype(np.float32) / 32768
    elif width == 3:
        b = np.frombuffer(data, np.uint8).reshape(-1, 3)
        i = (b[:, 0].astype(np.int32) | (b[:, 1].astype(np.int32) << 8) | (b[:, 2].astype(np.int32) << 16))
        i = np.where(i >= 1 << 23, i - (1 << 24), i)
        a = i.astype(np.float32) / (1 << 23)
    elif width == 4:
        a = np.frombuffer(data, "<i4").astype(np.float32) / (1 << 31)
    else:
        raise ValueError(f"不支援的 WAV 位元深度：{width * 8}")
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    return Resampler(rate, SAMPLE_RATE).process(a) if rate != SAMPLE_RATE else a.astype(np.float32)


class FileSource(AudioSource):
    """把 WAV 當麥克風播放。realtime=True 時依實際時間速度送出（模擬現場）。"""

    def __init__(self, path: str | Path, realtime: bool = True, block_ms: int = 32,
                 tail_silence_s: float = 1.5):
        audio = read_wav(path)
        self._audio = np.concatenate([audio, np.zeros(int(SAMPLE_RATE * tail_silence_s), np.float32)])
        self.realtime = realtime
        self.block = int(SAMPLE_RATE * block_ms / 1000)
        self._pos = 0
        self._t0 = 0.0
        self.level = 0.0
        self.overflows = 0
        self._lock = threading.Lock()

    def start(self) -> None:
        self._pos = 0
        self._t0 = time.monotonic()

    def read(self, timeout: float = 0.2) -> np.ndarray | None:
        with self._lock:
            if self._pos >= len(self._audio):
                return None
            if self.realtime:
                due = self._t0 + (self._pos + self.block) / SAMPLE_RATE
                wait = due - time.monotonic()
                if wait > timeout:
                    time.sleep(timeout)
                    return None
                if wait > 0:
                    time.sleep(wait)
            chunk = self._audio[self._pos: self._pos + self.block]
            self._pos += self.block
        if chunk.size:
            self.level = float(min(1.0, np.sqrt(np.mean(chunk ** 2)) * 6))
        return chunk

    @property
    def finished(self) -> bool:
        return self._pos >= len(self._audio)

    @property
    def position_s(self) -> float:
        return self._pos / SAMPLE_RATE
