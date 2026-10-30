import wave

import numpy as np
import pytest

from livesub.core.audio import FileSource, Resampler, read_wav


def _tone(rate, secs=1.0, freq=440):
    t = np.arange(int(rate * secs)) / rate
    return (0.5 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


@pytest.mark.parametrize("rate", [8000, 16000, 22050, 44100, 48000])
def test_resampler_length_and_continuity(rate):
    x = _tone(rate, 2.0)
    whole = Resampler(rate).process(x)
    r = Resampler(rate)
    parts = np.concatenate([r.process(c) for c in np.array_split(x, 37)])
    assert abs(len(whole) - 32000) <= 2
    assert abs(len(parts) - len(whole)) <= 2
    n = min(len(parts), len(whole))
    assert np.max(np.abs(parts[:n] - whole[:n])) < 1e-3  # 分塊處理與一次處理一致


def test_resampler_tiny_chunks():
    r = Resampler(48000)
    out = [r.process(np.ones(1, np.float32)) for _ in range(300)]
    assert sum(len(o) for o in out) in range(99, 102)


def _write_wav(path, data: np.ndarray, rate, ch=1, width=2):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(ch)
        w.setsampwidth(width)
        w.setframerate(rate)
        if width == 2:
            w.writeframes((np.clip(data, -1, 1) * 32767).astype("<i2").tobytes())
        elif width == 4:
            w.writeframes((np.clip(data, -1, 1) * 2147483000).astype("<i4").tobytes())


def test_read_wav_stereo_44k_unicode_path(tmp_path):
    mono = _tone(44100, 1.0)
    stereo = np.stack([mono, mono], axis=1).reshape(-1)
    p = tmp_path / "測試 音檔.wav"
    _write_wav(p, stereo, 44100, ch=2)
    a = read_wav(p)
    assert abs(len(a) - 16000) <= 2 and a.dtype == np.float32
    assert 0.4 < np.max(np.abs(a)) <= 0.51


def test_read_wav_32bit(tmp_path):
    p = tmp_path / "a.wav"
    _write_wav(p, _tone(16000, 0.5), 16000, width=4)
    assert len(read_wav(p)) == 8000


def test_file_source_blocks_and_finish(tmp_path):
    p = tmp_path / "a.wav"
    _write_wav(p, _tone(16000, 0.5), 16000)
    src = FileSource(p, realtime=False, tail_silence_s=0.5)
    src.start()
    total = 0
    while True:
        c = src.read()
        if c is None:
            break
        total += len(c)
    assert src.finished and abs(total - 16000) <= 512
