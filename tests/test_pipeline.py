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
    assert len(p._cur.final_q) <= 4
    assert wait_for(lambda: not p._cur.final_q and p.stats["merged_finals"] > 0, timeout=15)
    assert asr.n < 30  # 有合併
    p.stop(wait=True)


def test_long_run_memory_bounded():
    pattern = [(0.1, False)] + [(0.4, True), (0.7, False)] * 200
    p, rec, asr, mt = make(pattern)
    p.start()
    assert wait_for(lambda: asr.n >= 150, timeout=30)
    assert len(p.board.lines) <= p.board.keep_lines
    assert len(p._cur.utt_lang) <= 64
    assert len(p._cur.mt_q) <= 6
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


# ---- 審查回歸測試（docs/reviews/02-code-review-codex.md） --------------------------

def test_restart_accepts_partials_from_new_session():
    """重啟後 utt_id 從 1 重新開始：新工作階段的 partial 不能被上一場的紀錄擋掉。"""
    asr = FakeASR(partial_delay=0.01)
    p, rec, *_ = make([(0.3, False)] + [(2.0, True), (0.8, False)] * 3, asr=asr, profile="intel-gpu-lite",
                      mode="zh", rt=1.0)
    p.start()
    assert wait_for(lambda: asr.n >= 2, timeout=15)
    p.stop(wait=True)
    p.start()
    assert wait_for(lambda: p.state == PipelineState.RUNNING)
    assert wait_for(lambda: p.board.partial is not None or p.stats["partials"] > 0, timeout=10)
    assert p.board.partial is None or p.board.partial.utt_id >= 1
    assert wait_for(lambda: any(sp.text == "說話中" for b in rec.last.blocks for sp in b.spans), timeout=10)
    p.stop(wait=True)


def test_stuck_inference_blocks_restart_until_finished(monkeypatch):
    import livesub.core.pipeline as P

    monkeypatch.setattr(P, "JOIN_TIMEOUT_S", 0.5)
    gate = threading.Event()

    class StuckASR(FakeASR):
        def transcribe(self, audio, language=None, prompt=None, final=True):
            if final:
                gate.wait(10)
            return super().transcribe(audio, language, prompt, final)

    p, rec, *_ = make(SPEECH, asr=StuckASR())
    p.start()
    assert wait_for(lambda: p.state == PipelineState.RUNNING)
    time.sleep(0.5)  # 讓 ASR 卡在 final
    p.stop(wait=True)
    assert p.state == PipelineState.IDLE
    p.start()
    assert p.state == PipelineState.IDLE  # 舊推論沒結束，拒絕重啟
    assert "還在結束中" in rec.statuses[-1].message
    gate.set()
    assert wait_for(lambda: not any(t.is_alive() for t in p._lingering), timeout=5)
    p.start()
    assert wait_for(lambda: p.state == PipelineState.RUNNING)
    p.stop(wait=True)


def test_persistent_asr_failure_goes_failed_and_clears_partial():
    class BrokenASR(FakeASR):
        def transcribe(self, audio, language=None, prompt=None, final=True):
            if len(audio) == 16000 and not final:  # 暖機可以過
                return AsrResult("", "zh", None, None, 0.0)
            if not final:
                return AsrResult("說話中", "zh", -0.2, 0.01, 0.0)
            raise RuntimeError("GPU lost")

    p, rec, *_ = make([(0.3, False)] + [(1.5, True), (0.8, False)] * 5, asr=BrokenASR(),
                      profile="intel-gpu-lite", rt=1.0)
    p.start()
    assert wait_for(lambda: p.state == PipelineState.FAILED, timeout=20)
    assert "辨識持續失敗" in rec.statuses[-1].message
    assert rec.last.empty


def test_capture_error_goes_failed():
    class BadSource(FakeSource):
        def read(self, timeout=0.2):
            raise OSError("麥克風被拔掉")

    s = Settings()
    rec = Recorder()
    p = Pipeline(s, profiles.get("cpu-high"), rec.snap, rec.status, source_factory=lambda _s: BadSource([]),
                 asr_factory=lambda _p: FakeASR(), mt_factory=lambda _p, _s: FakeMT(), vad_factory=FakeVAD,
                 require_models=False)
    p.start()
    assert wait_for(lambda: p.state == PipelineState.FAILED)
    assert "收音錯誤" in rec.statuses[-1].message


def test_warmup_failure_falls_back_to_next_profile():
    good = FakeASR()

    class DeadOnInference(FakeASR):
        def transcribe(self, *a, **k):
            raise RuntimeError("kernel compile failed")

    p, rec, *_ = make(SPEECH, profile="intel-gpu",
                      asr_factory=lambda prof: DeadOnInference() if prof.name == "intel-gpu" else good)
    p.start()
    assert wait_for(lambda: p.state == PipelineState.RUNNING)
    assert p.active_profile.name == "intel-gpu-lite"
    p.stop(wait=True)


def test_translator_falls_back_to_cpu():
    devices = []

    def mt_factory(prof, _s):
        devices.append(prof.mt_device)
        if prof.mt_device == "cuda":
            raise RuntimeError("CUDA OOM")
        return FakeMT()

    s = Settings()
    s.display.clear_after_s = 0
    rec = Recorder()
    p = Pipeline(s, profiles.get("nvidia-mid"), rec.snap, rec.status,
                 source_factory=lambda _s: FakeSource(SPEECH), asr_factory=lambda _p: FakeASR(),
                 mt_factory=mt_factory, vad_factory=FakeVAD, require_models=False)
    p.start()
    assert wait_for(lambda: p.state == PipelineState.RUNNING)
    assert devices == ["cuda", "cpu"]
    assert wait_for(lambda: rec.last and "[en]" in rec.last.blocks[1].text)
    assert wait_for(lambda: any(st.mt_latency_s is not None for st in rec.statuses))
    p.stop(wait=True)


# ---- 最終審查（04）的回歸測試 ----------------------------------------------
def _pipeline(source_factory, asr=None, mt_factory=None, profile="cpu-high"):
    s = Settings()
    s.display.clear_after_s = 0
    rec = Recorder()
    asr = asr or FakeASR()
    p = Pipeline(s, profiles.get(profile), rec.snap, rec.status, source_factory=source_factory,
                 asr_factory=lambda _p: asr, mt_factory=mt_factory or (lambda _p, _s: FakeMT()),
                 vad_factory=FakeVAD, require_models=False)
    return p, rec


def test_loader_closes_source_opened_after_stop_timeout(monkeypatch):
    """開麥克風卡住超過停止期限：loader 稍後開好來源時必須自己關掉，並在那之前擋住重啟。"""
    import livesub.core.pipeline as P

    monkeypatch.setattr(P, "JOIN_TIMEOUT_S", 0.3)
    entered, gate = threading.Event(), threading.Event()
    made = []

    def slow_factory(_s):
        entered.set()
        gate.wait(10)
        src = FakeSource(SPEECH)
        made.append(src)
        return src

    p, rec = _pipeline(slow_factory)
    p.start()
    assert entered.wait(5)
    p.stop(wait=True)
    assert p.state == PipelineState.IDLE
    p.start()
    assert p.state == PipelineState.IDLE  # loader 還活著：拒絕重啟
    gate.set()
    assert wait_for(lambda: not any(t.is_alive() for t in p._lingering), timeout=5)
    assert made and made[0].stopped


def test_start_failure_goes_through_stopping_and_closes_source():
    class BadStart(FakeSource):
        def start(self):
            raise OSError("裝置被占用")

    src = BadStart(SPEECH)
    p, rec = _pipeline(lambda _s: src)
    p.start()
    assert wait_for(lambda: p.state == PipelineState.FAILED)
    states = [st.state for st in rec.statuses]
    assert PipelineState.STOPPING in states
    assert states.index(PipelineState.STOPPING) < len(states) - 1
    assert "啟動失敗" in rec.statuses[-1].message
    assert src.stopped
    assert not any(t.is_alive() for t in p._cur.threads)


def test_stop_wait_while_already_stopping_waits_for_cleanup():
    release = threading.Event()

    class SlowStop(FakeSource):
        def stop(self):
            release.wait(5)
            super().stop()

    src = SlowStop(SPEECH, realtime_factor=1.0)
    p, rec = _pipeline(lambda _s: src)
    p.start()
    assert wait_for(lambda: p.state == PipelineState.RUNNING)
    p.stop()  # 使用者按停止
    assert p.state == PipelineState.STOPPING
    threading.Timer(0.3, release.set).start()
    p.stop(wait=True)  # 接著結束程式：必須等清理做完
    assert src.stopped
    assert p.state == PipelineState.IDLE


def test_translation_inference_failure_switches_to_cpu():
    devices = []

    class OomMT(FakeMT):
        def translate(self, text, src, tgt, context=None):
            raise RuntimeError("CUDA out of memory")

    def mt_factory(prof, _s):
        devices.append(prof.mt_device)
        return OomMT() if prof.mt_device == "cuda" else FakeMT()

    p, rec = _pipeline(lambda _s: FakeSource(SPEECH), mt_factory=mt_factory, profile="nvidia-mid")
    p.start()
    assert wait_for(lambda: rec.last and "[en]" in rec.last.blocks[1].text)
    assert devices == ["cuda", "cpu"]
    assert any("改用 CPU" in st.message for st in rec.statuses)
    p.stop(wait=True)


def test_partial_failures_do_not_block_segment_or_fail_pipeline():
    class FlakyPartials(FakeASR):
        def transcribe(self, audio, language=None, prompt=None, final=True):
            if not final and len(audio) != SR:  # 暖機（1 秒靜音）可以過，其餘 partial 隔一次失敗
                self.calls.append(("partial", len(audio), language, prompt))
                if len(self.calls) % 2:
                    raise RuntimeError("partial glitch")
                return AsrResult("說話中", self.lang, -0.2, 0.01, 0.0)
            return super().transcribe(audio, language, prompt, final)

    asr = FlakyPartials()
    pattern = [(0.3, False)] + [(2.5, True), (0.8, False)] * 4
    p, rec = _pipeline(lambda _s: FakeSource(pattern, 1.0), asr=asr, profile="intel-gpu-lite")
    p.start()
    assert wait_for(lambda: asr.n >= 4, timeout=25)
    assert p.state == PipelineState.RUNNING  # partial 一再失敗也不會讓整個工作失敗
    assert p.stats["partial_errors"] > 0 and p.stats["partials"] > 0 and p.stats["asr_errors"] == 0
    assert p.board.lines and all("第" in l.original for l in p.board.lines)
    p.stop(wait=True)



# ---- 邊講邊出字（獨立 partial 引擎）的回歸測試 ------------------------------
class TrackingASR(FakeASR):
    """記錄同時進行的推論數，以及 partial 開始時 final 佇列的狀態。"""

    def __init__(self, delay=0.0, partial_delay=0.0, name="fake-asr"):
        super().__init__(delay, partial_delay)
        self.name = name
        self.active = 0
        self.max_active = 0
        self.lock = threading.Lock()
        self.pipeline = None
        self.partial_starts_with_final_waiting = 0
        self.spans = []  # (kind, start, end)

    def transcribe(self, audio, language=None, prompt=None, final=True):
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            if not final and self.pipeline is not None and self.pipeline._cur is not None \
                    and self.pipeline._cur.final_q:
                self.partial_starts_with_final_waiting += 1
        t0 = time.monotonic()
        try:
            return super().transcribe(audio, language, prompt, final)
        finally:
            with self.lock:
                self.active -= 1
                self.spans.append(("final" if final else "partial", t0, time.monotonic()))


BURSTS = [(0.3, False)] + [(1.6, True), (0.6, False)] * 5


def _streaming(profile, final_eng, partial_eng=None, source_pattern=BURSTS):
    s = Settings()
    s.display.clear_after_s = 0
    rec = Recorder()

    def factory(prof):
        if partial_eng is not None and prof.asr_model == profiles.get(profile).partial_model:
            return partial_eng
        return final_eng

    p = Pipeline(s, profiles.get(profile), rec.snap, rec.status,
                 source_factory=lambda _s: FakeSource(source_pattern, 1.0), asr_factory=factory,
                 mt_factory=lambda _p, _s: FakeMT(), vad_factory=FakeVAD, require_models=False)
    final_eng.pipeline = p
    if partial_eng is not None:
        partial_eng.pipeline = p
    return p, rec


def test_shared_engine_never_overlaps_and_final_goes_first():
    """共用引擎：同一時間只有一個推論；final 已排隊時不會先跑 partial。"""
    eng = TrackingASR(delay=0.9, partial_delay=0.15)
    p, rec = _streaming("nvidia-mid", eng)
    p.start()
    assert wait_for(lambda: eng.n >= 5, timeout=30)
    assert eng.max_active == 1
    assert eng.partial_starts_with_final_waiting == 0
    assert p.stats["partials"] > 0
    names = {t.name for t in p._cur.threads}
    assert "livesub-asr" in names and "livesub-asr-partial" not in names
    p.stop(wait=True)


def test_separate_partial_engine_runs_in_parallel_with_final():
    final_eng = TrackingASR(delay=1.2, name="final")
    partial_eng = TrackingASR(partial_delay=0.1, name="partial")
    p, rec = _streaming("intel-gpu", final_eng, partial_eng)
    p.start()
    assert wait_for(lambda: final_eng.n >= 4, timeout=30)
    p.stop(wait=True)
    assert p._cur.asr_partial is partial_eng
    # 各自只跑自己的工作（暖機是 1 秒靜音的 partial 呼叫，排除）
    assert all(k == "partial" for k, *_ in partial_eng.spans)
    assert [k for k, *_ in final_eng.spans if k == "partial"] == ["partial"]  # 只有暖機
    finals = [(a, b) for k, a, b in final_eng.spans if k == "final"]
    overlapped = sum(1 for _, a, b in partial_eng.spans if any(fa < a < fb for fa, fb in finals))
    assert overlapped > 0  # final 推論期間 partial 照樣在跑
    assert p.stats["partials"] > 0
    st = rec.statuses[-1]
    assert any(x.partial_latency_s is not None for x in rec.statuses)


def test_partial_engine_init_failure_falls_back_to_shared_with_message():
    final_eng = TrackingASR()

    def factory(prof):
        if prof.asr_model == profiles.get("intel-gpu").partial_model:
            raise RuntimeError("GPU 編譯失敗")
        return final_eng

    s = Settings()
    rec = Recorder()
    p = Pipeline(s, profiles.get("intel-gpu"), rec.snap, rec.status,
                 source_factory=lambda _s: FakeSource(BURSTS, 1.0), asr_factory=factory,
                 mt_factory=lambda _p, _s: FakeMT(), vad_factory=FakeVAD, require_models=False)
    p.start()
    assert wait_for(lambda: p.state == PipelineState.RUNNING)
    assert p.active_profile.name == "intel-gpu"
    assert p._cur.asr_partial is None
    assert "即時辨識模型無法使用" in rec.statuses[-1].message
    assert "livesub-asr" in {t.name for t in p._cur.threads}
    p.stop(wait=True)


def test_broken_partial_engine_is_disabled_but_finals_continue():
    class DeadPartial(TrackingASR):
        def transcribe(self, audio, language=None, prompt=None, final=True):
            if len(audio) == SR:  # 暖機可以過
                return AsrResult("", "zh", None, None, 0.0)
            raise RuntimeError("device lost")

    final_eng = TrackingASR(name="final")
    partial_eng = DeadPartial(name="partial")
    p, rec = _streaming("intel-gpu", final_eng, partial_eng)
    p.start()
    assert wait_for(lambda: final_eng.n >= 4, timeout=30)
    assert p.state == PipelineState.RUNNING
    assert p._cur.partials_off
    assert p.stats["partial_errors"] == 3  # 停用後就不再送 partial
    assert any("即時辨識引擎故障" in st.message for st in rec.statuses)
    assert p.board.lines
    p.stop(wait=True)


def _bare_session(p):
    from livesub.core.pipeline import _Req, _Session

    s = _Session(1, time.monotonic)
    s.asr = FakeASR()
    return s, _Req


def test_short_partial_does_not_lock_sentence_language():
    p, *_ = make(SPEECH)
    s, Req = _bare_session(p)
    p._run_asr(s, Req("partial", 1, np.zeros(int(SR * 0.7), np.float32), 1.0, 0.0))
    assert 1 not in s.utt_lang  # 太短：不採用語言判斷
    p._run_asr(s, Req("partial", 1, np.zeros(int(SR * 1.5), np.float32), 1.0, 0.0))
    assert s.utt_lang[1] == "zh"


def test_late_partial_after_final_does_not_revive_language_cache():
    p, *_ = make(SPEECH)
    s, Req = _bare_session(p)
    p._run_asr(s, Req("final", 1, np.zeros(SR * 2, np.float32), 1.0, 0.0))
    p._run_asr(s, Req("partial", 1, np.zeros(SR * 2, np.float32), 1.0, 0.0))  # 晚到
    assert 1 not in s.utt_lang


def test_missing_models_note_tells_user_to_reinstall(monkeypatch):
    import livesub.core.pipeline as P

    monkeypatch.setattr(P.models, "is_downloaded", lambda key: key == "whisper-base")
    s = Settings()
    rec = Recorder()
    p = Pipeline(s, profiles.get("intel-gpu"), rec.snap, rec.status,
                 source_factory=lambda _s: FakeSource(SPEECH), asr_factory=lambda _p: FakeASR(),
                 mt_factory=lambda _p, _s: FakeMT(), vad_factory=FakeVAD, require_models=True)
    p.start()
    assert wait_for(lambda: p.state == PipelineState.RUNNING)
    assert p.active_profile.name == "cpu-low"
    assert "重新執行安裝程式" in rec.statuses[-1].message
    p.stop(wait=True)


def test_cancelled_or_failed_segments_do_not_keep_language_cache():
    from livesub.core.segmenter import SegEvent

    p, *_ = make(SPEECH)
    s, Req = _bare_session(p)
    long = np.zeros(int(SR * 1.5), np.float32)
    p._run_asr(s, Req("partial", 3, long, 1.0, 0.0))
    assert s.utt_lang[3] == "zh"
    p._submit(s, SegEvent("cancel", 3))  # 雜音段被取消
    assert 3 not in s.utt_lang
    p._run_asr(s, Req("partial", 3, long, 1.0, 0.0))  # 晚到的 partial
    assert 3 not in s.utt_lang

    class Boom(FakeASR):
        def transcribe(self, audio, language=None, prompt=None, final=True):
            if final:
                raise RuntimeError("boom")
            return super().transcribe(audio, language, prompt, final)

    s.asr = Boom()
    p._run_asr(s, Req("partial", 5, long, 1.0, 0.0))
    assert p._do_final(s, Req("final", 5, long, 1.0, 0.0))  # 失敗一次，還不到門檻
    assert 5 not in s.utt_lang and 5 in s.ended_utts
    assert s.max_final_utt == 0  # 沒有推進，較舊的段落仍可寫入


def test_status_names_partial_model_when_separate():
    final_eng = TrackingASR(name="small")
    partial_eng = TrackingASR(name="base")
    p, rec = _streaming("intel-gpu", final_eng, partial_eng)
    p.start()
    assert wait_for(lambda: p.state == PipelineState.RUNNING)
    assert wait_for(lambda: rec.statuses and rec.statuses[-1].asr_model == "small＋即時 base")
    p.stop(wait=True)
