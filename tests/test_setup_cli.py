"""setup_cli 的校正邏輯（用假引擎，不載入真模型）。"""

import numpy as np
import pytest

from livesub import profiles, setup_cli
from livesub.core.asr import AsrResult

AUDIO = np.zeros(16000 * 5, np.float32)


class Eng:
    def __init__(self, text, seconds, fail=False):
        self.text, self.seconds, self.fail = text, seconds, fail
        self.calls = 0

    def transcribe(self, audio, language=None, prompt=None, final=True):
        self.calls += 1
        if self.fail:
            raise RuntimeError("GPU 編譯失敗")
        return AsrResult(self.text, "zh", None, None, self.seconds)


@pytest.fixture
def engines(monkeypatch):
    made = {}

    def build(p):
        made[p.asr_model] = made.get(p.asr_model) or Eng(setup_cli.BENCH_TEXT, 0.3)
        return made[p.asr_model]

    monkeypatch.setattr("livesub.core.asr.build_asr", build)
    monkeypatch.setattr(setup_cli.models, "is_downloaded", lambda key: True)
    return made


def test_probe_loads_partial_engine_and_measures_under_load(engines):
    p = profiles.get("intel-gpu")
    r = setup_cli.probe(p, AUDIO, setup_cli.BENCH_TEXT)
    assert r["ok"] and r["partial_similarity"] == 1.0 and r["partial_s"] == 0.3
    assert engines[p.partial_model].calls >= 2  # 暖機＋定稿期間持續推論


def test_probe_fails_when_partial_engine_broken(engines):
    p = profiles.get("intel-gpu")
    engines[p.partial_model] = Eng("", 0.1, fail=True)
    r = setup_cli.probe(p, AUDIO, setup_cli.BENCH_TEXT)
    assert not r["ok"] and "即時辨識模型" in r["error"]


def test_profile_without_partial_model_skips_partial_probe(engines):
    r = setup_cli.probe(profiles.get("intel-gpu-lite"), AUDIO, setup_cli.BENCH_TEXT)
    assert r["ok"] and "partial_s" not in r


def test_partial_model_download_failure_does_not_abort(monkeypatch):
    p = profiles.get("intel-gpu")
    monkeypatch.setattr(setup_cli.models, "is_downloaded", lambda key: key != p.partial_model)
    monkeypatch.setattr(setup_cli.models, "verify", lambda key: [])

    def download(key, progress=None, force=False):
        raise OSError("網路中斷")

    monkeypatch.setattr(setup_cli.models, "download", download)
    setup_cli.ensure_models(p)  # 不丟例外


@pytest.mark.parametrize("text, ok", [
    ("大家好,欢迎参加今天的工作房。", True),  # 截斷、簡體、一個同音錯字：仍算正確
    ("大家好，歡迎參加今天的工作坊。我們今天", True),
    ("!!!!!!!!!!!!", False),  # 亂碼
    ("Thank you for watching.", False),
    ("", False),
    ("的", False),  # 每個字都對，但漏了大半
    ("大家好", False),
])
def test_similarity_tolerates_truncated_bench_audio(text, ok):
    sim = setup_cli._similarity(text, setup_cli.BENCH_TEXT)
    assert (sim >= setup_cli.MIN_SIMILARITY) == ok


class FailsWhenBusy(Eng):
    """暖機（第一次）可以過，開始和定稿並行之後就出錯。"""

    def transcribe(self, audio, language=None, prompt=None, final=True):
        self.calls += 1
        if self.calls > 1:
            raise RuntimeError("GPU 記憶體不足")
        return AsrResult(self.text, "zh", None, None, self.seconds)


def test_probe_fails_when_partial_breaks_under_load(engines):
    p = profiles.get("intel-gpu")
    engines[p.partial_model] = FailsWhenBusy(setup_cli.BENCH_TEXT, 0.1)
    r = setup_cli.probe(p, AUDIO, setup_cli.BENCH_TEXT)
    assert not r["ok"] and "並行時" in r["error"]


def test_probe_fails_when_partial_hangs(engines, monkeypatch):
    import threading

    gate = threading.Event()

    class Hangs(Eng):
        def transcribe(self, audio, language=None, prompt=None, final=True):
            self.calls += 1
            if self.calls > 1:
                gate.wait(5)
            return AsrResult(self.text, "zh", None, None, self.seconds)

    monkeypatch.setattr(setup_cli, "PARTIAL_JOIN_S", 0.2)
    p = profiles.get("intel-gpu")
    engines[p.partial_model] = Hangs(setup_cli.BENCH_TEXT, 0.1)
    try:
        r = setup_cli.probe(p, AUDIO, setup_cli.BENCH_TEXT)
    finally:
        gate.set()
    assert not r["ok"] and "沒有回應" in r["error"]
