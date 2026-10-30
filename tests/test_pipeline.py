"""Pipeline 整合測試：用假的音訊／VAD／ASR／翻譯，控制完成順序與延遲。"""

import threading
import time

import numpy as np
import pytest

from livesub import profiles
from livesub.config import Settings
from livesub.core.asr import AsrResult
from livesub.core.audio import AudioSource
from livesub.core.pipeline import Pipeline
from livesub.core.status import PipelineState
from livesub.core.vad import FRAME

SR = 16000


class FakeSource(AudioSource):
    """依 pattern [(秒數, 是否說話), ...] 產生音訊；說話段振幅 0.5，靜音為 0。"""

    def __init__(self, pattern, realtime_factor=0.0):
        parts = [np.full(int(SR * s), 0.5 if speech else 0.0, np.float32) for s, speech in pattern]
        self.audio = np.concatenate(parts + [np.zeros(SR, np.float32)])
        self.pos = 0
        self.rt = realtime_factor
        self.level = 0.0
        self.stopped = False

    def start(self):
        self.pos = 0

    def read(self, timeout=0.2):
        if self.pos >= len(self.audio):
            return None
        if self.rt:
            time.sleep(FRAME / SR * self.rt)
        c = self.audio[self.pos:self.pos + FRAME]
        self.pos += FRAME
        self.level = float(np.abs(c).mean())
        return c

    @property
    def finished(self):
        return self.pos >= len(self.audio)

    def stop(self):
        self.stopped = True


class FakeVAD:
    def probs(self, frames):
        return np.where(np.abs(frames).mean(axis=1) > 0.1, 0.95, 0.02).astype(np.float32)


class FakeASR:
    def __init__(self, delay=0.0, partial_delay=0.0, lang="zh"):
        self.name = "fake-asr"
        self.delay, self.partial_delay, self.lang = delay, partial_delay, lang
        self.calls = []
        self.n = 0

    def transcribe(self, audio, language=None, prompt=None, final=True):
        self.calls.append(("final" if final else "partial", len(audio), language, prompt))
        time.sleep(self.delay if final else self.partial_delay)
        if not final:
            return AsrResult("說話中", self.lang, -0.2, 0.01, self.partial_delay)
        self.n += 1
        secs = len(audio) / SR
        if self.lang == "zh":
            return AsrResult(f"第{self.n}句话长度{secs:.0f}秒", "zh", -0.2, 0.01, self.delay)
        return AsrResult(f"Sentence {self.n} lasting {secs:.0f} seconds.", "en", -0.2, 0.01, self.delay)


class FakeMT:
    def __init__(self, delay=0.0, fail=False):
        self.name = "fake-mt"
        self.delay, self.fail = delay, fail
        self.calls = []

    def translate(self, text, src, tgt, context=None):
        self.calls.append((text, src, tgt))
        time.sleep(self.delay)
        if self.fail:
            raise RuntimeError("boom")
        return f"[{tgt}] {text}"


class Recorder:
    def __init__(self):
        self.snaps = []
        self.statuses = []
        self.lock = threading.Lock()

    def snap(self, s):
        with self.lock:
            self.snaps.append(s)

    def status(self, s):
        with self.lock:
            self.statuses.append(s)

    @property
    def last(self):
        with self.lock:
            return self.snaps[-1] if self.snaps else None


def make(pattern, asr=None, mt=None, mode="bilingual", profile="cpu-high", rt=0.0, asr_factory=None,
         settings=None):
    s = settings or Settings()
    s.display.mode = mode
    s.display.clear_after_s = 0
    rec = Recorder()
    asr = asr or FakeASR()
    mt = mt or FakeMT()
    p = Pipeline(
        s, profiles.get(profile), rec.snap, rec.status,
        source_factory=lambda _s: FakeSource(pattern, rt),
        asr_factory=asr_factory or (lambda _p: asr),
        mt_factory=lambda _p, _s: mt,
        vad_factory=FakeVAD,
        require_models=False,
    )
    return p, rec, asr, mt


def wait_for(cond, timeout=8.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


SPEECH = [(0.5, False), (1.2, True), (0.9, False), (1.5, True), (0.9, False)]


def test_end_to_end_bilingual():
    p, rec, asr, mt = make(SPEECH)
    p.start()
    assert wait_for(lambda: p.state == PipelineState.RUNNING)
    assert wait_for(lambda: len(mt.calls) == 2 and rec.last and "[en]" in rec.last.blocks[1].text
                    and rec.last.blocks[1].text.count("[en]") == 2)
    zh, en = rec.last.blocks
    assert "第1句話" in zh.text and "第2句話" in zh.text  # 簡轉繁
    assert all(c[1:] == ("zh", "en") for c in mt.calls)
    p.stop(wait=True)
    assert p.state == PipelineState.IDLE
    assert rec.last.empty  # 停止後清空字幕


def test_english_source_translates_to_chinese():
    p, rec, asr, mt = make(SPEECH, asr=FakeASR(lang="en"), mode="zh")
    p.start()
    assert wait_for(lambda: len(mt.calls) == 2)
    assert all(c[1:] == ("en", "zh") for c in mt.calls)
    assert wait_for(lambda: rec.last.blocks[0].text.count("[zh]") == 2)
    p.stop(wait=True)


def test_status_reports_profile_and_latency():
    p, rec, *_ = make(SPEECH)
    p.start()
    assert wait_for(lambda: any(s.latency_s is not None for s in rec.statuses))
    st = [s for s in rec.statuses if s.state == PipelineState.RUNNING][-1]
    assert st.profile == "cpu-high" and st.segmented and st.asr_model == "fake-asr"
    p.stop(wait=True)


def test_mode_switch_rerenders_without_restart():
    p, rec, *_ = make(SPEECH, mode="zh")
    p.start()
    assert wait_for(lambda: rec.last and rec.last.blocks[0].text.count("句") == 2)
    s = Settings()
    s.display.mode = "en"
    s.display.clear_after_s = 0
    p.update_settings(s)
    assert wait_for(lambda: rec.last.blocks[0].lang == "en")
    p.stop(wait=True)


def test_translation_failure_shows_original_dim():
    p, rec, asr, mt = make(SPEECH, mt=FakeMT(fail=True), mode="en")
    p.start()
    assert wait_for(lambda: len(mt.calls) == 2)
    assert wait_for(lambda: rec.last.blocks[0].text.count("句") == 2)
    assert all(sp.dim for sp in rec.last.blocks[0].spans if sp.text.strip())
    p.stop(wait=True)


def test_stop_during_slow_translation_then_restart():
    p, rec, asr, mt = make(SPEECH, mt=FakeMT(delay=0.6))
    p.start()
    assert wait_for(lambda: len(mt.calls) >= 1)
    p.stop(wait=True)
    assert p.state == PipelineState.IDLE
    time.sleep(0.8)  # 晚到的翻譯結果不能讓字幕復活
    assert rec.last.empty
    p.start()
    assert wait_for(lambda: p.state == PipelineState.RUNNING)
    assert wait_for(lambda: len(asr.calls) >= 3)
    p.stop(wait=True)


def test_start_stop_spam_is_safe():
    p, rec, *_ = make(SPEECH * 3, rt=1.0)
    for _ in range(15):
        p.start()
        p.stop()
    p.stop(wait=True)
    assert p.wait_idle()
    p.start()
    assert wait_for(lambda: p.state == PipelineState.RUNNING)
    p.stop(wait=True)
    assert p.state == PipelineState.IDLE


def test_runtime_fallback_when_asr_init_fails():
    asr = FakeASR()

    def factory(prof):
        if prof.name == "intel-gpu":
            raise RuntimeError("GPU 不見了")
        return asr

    p, rec, *_ = make(SPEECH, profile="intel-gpu", asr_factory=factory)
    p.start()
    assert wait_for(lambda: p.state == PipelineState.RUNNING)
    assert p.active_profile.name == "intel-gpu-lite"
    assert "退回" in rec.statuses[-1].message or any("退回" in s.message for s in rec.statuses)
    p.stop(wait=True)


def test_all_asr_fail_goes_to_failed():
    def factory(prof):
        raise RuntimeError("nope")

    p, rec, *_ = make(SPEECH, asr_factory=factory)
    p.start()
    assert wait_for(lambda: p.state == PipelineState.FAILED)
    assert "沒有可用的辨識模型" in rec.statuses[-1].message
    p.start()  # FAILED 之後可以再試
    assert wait_for(lambda: p.state == PipelineState.FAILED)


def test_partials_are_emitted_and_replaced_by_final():
    asr = FakeASR(partial_delay=0.01)
    p, rec, *_ = make([(0.3, False), (3.0, True), (1.0, False)], asr=asr, profile="intel-gpu-lite",
                      mode="zh", rt=1.0)
    p.start()
    assert wait_for(lambda: any(any(sp.text == "說話中" and sp.dim for b in s.blocks for sp in b.spans)
                                for s in list(rec.snaps)))
    assert wait_for(lambda: "第1句" in rec.last.blocks[0].text and "說話中" not in rec.last.blocks[0].text)
    p.stop(wait=True)


def test_slow_asr_backlog_is_bounded():
    # 30 個短句、每句辨識 0.15 秒，但音訊是瞬間送完的：final 佇列必須合併、不能無限增長
    pattern = [(0.1, False)] + [(0.5, True), (0.7, False)] * 30
    asr = FakeASR(delay=0.15)
    p, rec, *_ = make(pattern, asr=asr)
    p.start()
    assert wait_for(lambda: p.state == PipelineState.RUNNING)
    time.sleep(0.5)
    assert len(p._final_q) <= 4
    assert wait_for(lambda: not p._final_q and p.stats["merged_finals"] > 0, timeout=15)
    assert asr.n < 30  # 有合併
    p.stop(wait=True)


def test_long_run_memory_bounded():
    pattern = [(0.1, False)] + [(0.4, True), (0.7, False)] * 200
    p, rec, asr, mt = make(pattern)
    p.start()
    assert wait_for(lambda: asr.n >= 150, timeout=30)
    assert len(p.board.lines) <= p.board.keep_lines
    assert len(p._utt_lang) <= 64
    assert len(p._mt_q) <= 6
    assert len(p.board._finalized_utts) <= 320
    p.stop(wait=True)


def test_prompt_uses_previous_chinese_context_and_resets():
    asr = FakeASR()
    s = Settings()
    s.audio.source_language = "zh"
    p, rec, *_ = make(SPEECH, asr=asr, settings=s, rt=1.0)
    p.start()
    assert wait_for(lambda: asr.n >= 2)
    finals = [c for c in asr.calls if c[0] == "final"]
    assert finals[0][2] == "zh"
    assert "繁體中文" in finals[0][3]
    assert "第1句話" in finals[1][3]  # 上一句當上下文
    for c in asr.calls:
        assert c[3] is None or len(c[3]) <= 120
    p.stop(wait=True)


@pytest.mark.parametrize("bad", ["", "謝謝觀看", "字幕由Amara.org社區提供"])
def test_hallucinated_finals_are_not_shown(bad):
    class HalluASR(FakeASR):
        def transcribe(self, audio, language=None, prompt=None, final=True):
            self.n += 1
            return AsrResult(bad, "zh", -0.3, 0.1, 0.0)

    p, rec, asr, mt = make(SPEECH, asr=HalluASR())
    p.start()
    assert wait_for(lambda: asr.n >= 3)
    time.sleep(0.3)
    assert rec.last.empty and mt.calls == []
    p.stop(wait=True)
