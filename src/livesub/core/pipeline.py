"""Pipeline：收音 → VAD → 斷句 → ASR → 後處理 → 字幕板 → 翻譯。

執行緒：
- capture：讀音訊、跑 VAD 與 Segmenter，把請求交給 ASR worker
- asr：final 優先；partial 只有一個待處理槽（新的覆蓋舊的）
- translate：有上限的佇列，滿了丟最舊的
- coordinator：唯一擁有 CaptionBoard 的執行緒；其他執行緒只送事件進來
UI 透過 on_snapshot / on_status callback 收到不可變的快照（在 coordinator 執行緒呼叫）。
"""

from __future__ import annotations

import copy
import logging
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from .. import models, profiles
from ..config import Settings
from ..profiles import Profile
from . import textproc
from .asr import ZH_PROMPT, AsrResult
from .audio import AudioSource, MicSource
from .captions import BoardSnapshot, CaptionBoard
from .segmenter import FRAME_S, SegEvent, Segmenter, SegmenterConfig
from .status import PipelineState, PipelineStatus
from .vad import Framer, SileroVAD

log = logging.getLogger(__name__)

PROMPT_MAX_CHARS = 120
CONTEXT_RESET_S = 10.0
FINAL_QUEUE_MAX = 4
TRANSLATE_QUEUE_MAX = 6
MAX_MERGED_S = 20.0


# ---- 事件（送進 coordinator） --------------------------------------------
@dataclass(frozen=True)
class _AsrDone:
    session: int
    kind: str  # "partial" / "final"
    utt_id: int
    result: AsrResult
    voiced_ratio: float
    emitted_at: float  # segmenter 發出請求的時間
    forced: bool


@dataclass(frozen=True)
class _Cancel:
    session: int
    utt_id: int


@dataclass(frozen=True)
class _MtDone:
    session: int
    line_id: int
    tgt: str
    text: str | None  # None = 失敗或被略過


@dataclass
class _Req:
    kind: str
    utt_id: int
    audio: np.ndarray
    voiced_ratio: float
    emitted_at: float
    forced: bool = False
    merged_utts: list[int] = field(default_factory=list)


def profile_chain(p: Profile) -> list[Profile]:
    chain, seen = [], set()
    while p is not None and p.name not in seen:
        chain.append(p)
        seen.add(p.name)
        p = profiles.downgrade(p)
    return chain


def default_asr_factory(p: Profile):
    from .asr import build_asr

    return build_asr(p)


def default_mt_factory(p: Profile, settings: Settings):
    from .translate import build_translator

    return build_translator(p, settings.runtime, settings.display.taiwan_phrases)


class Pipeline:
    def __init__(
        self,
        settings: Settings,
        profile: Profile,
        on_snapshot: Callable[[BoardSnapshot], None],
        on_status: Callable[[PipelineStatus], None],
        source_factory: Callable[[Settings], AudioSource] | None = None,
        asr_factory: Callable[[Profile], object] | None = None,
        mt_factory: Callable[[Profile, Settings], object] | None = None,
        vad_factory: Callable[[], object] | None = None,
        clock: Callable[[], float] = time.monotonic,
        require_models: bool = True,
    ):
        self.settings = copy.deepcopy(settings)
        self.profile = profile
        self.on_snapshot = on_snapshot
        self.on_status = on_status
        self._source_factory = source_factory or (lambda s: MicSource(s.audio.device))
        self._asr_factory = asr_factory or default_asr_factory
        self._mt_factory = mt_factory or default_mt_factory
        self._vad_factory = vad_factory or SileroVAD
        self._clock = clock
        self._require_models = require_models

        self._lock = threading.RLock()
        self._session = 0
        self._state = PipelineState.IDLE
        self._message = ""
        self._active_profile: Profile | None = None
        self._asr = None
        self._mt = None
        self._source: AudioSource | None = None
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()

        self._events: queue.Queue = queue.Queue()
        self._final_q: deque[_Req] = deque()
        self._partial_slot: _Req | None = None
        self._asr_cv = threading.Condition()
        self._mt_q: deque[tuple] = deque()
        self._mt_cv = threading.Condition()

        self.board = CaptionBoard(clock=clock)
        self._latencies: deque[float] = deque(maxlen=20)
        self._partial_cost = 0.0  # partial 推論耗時的指數平均
        self._level = 0.0
        self._last_snapshot_key = None
        self._last_status: PipelineStatus | None = None
        self._last_status_t = 0.0
        self._prompt_ctx = ""
        self._prompt_lang: str | None = None
        self._last_final_t = 0.0
        self._utt_lang: dict[int, str] = {}
        self.stats = {"finals": 0, "partials": 0, "dropped_partials": 0, "merged_finals": 0,
                      "mt_skipped": 0, "filtered": 0}

    # ---- 對外 API ------------------------------------------------------
    @property
    def state(self) -> PipelineState:
        return self._state

    @property
    def active_profile(self) -> Profile | None:
        return self._active_profile

    def start(self) -> None:
        with self._lock:
            if self._state not in (PipelineState.IDLE, PipelineState.FAILED):
                return
            self._session += 1
            self._stop = threading.Event()
            self._set_state(PipelineState.LOADING, "")
        threading.Thread(target=self._load_and_run, args=(self._session, self._stop),
                         name="livesub-loader", daemon=True).start()

    def stop(self, wait: bool = False) -> None:
        with self._lock:
            if self._state in (PipelineState.IDLE, PipelineState.STOPPING):
                return
            self._set_state(PipelineState.STOPPING, "")
            self._session += 1  # 讓所有晚到的結果失效
            self._stop.set()
            threads = list(self._threads)
        with self._asr_cv:
            self._asr_cv.notify_all()
        with self._mt_cv:
            self._mt_cv.notify_all()
        t = threading.Thread(target=self._join, args=(threads,), name="livesub-stopper", daemon=True)
        t.start()
        if wait:
            t.join(timeout=10)

    def update_settings(self, settings: Settings) -> None:
        """顯示相關設定立即生效（在任何執行緒呼叫都可以）。麥克風變更需重新 start。"""
        self.settings = copy.deepcopy(settings)
        self._events.put(("refresh",))

    def wait_idle(self, timeout: float = 10.0) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self._state in (PipelineState.IDLE, PipelineState.FAILED):
                return True
            time.sleep(0.02)
        return False

    # ---- 載入 ----------------------------------------------------------
    def _load_and_run(self, session: int, stop: threading.Event) -> None:
        try:
            asr, prof, note = self._build_asr()
            if stop.is_set():
                return
            try:
                mt = self._mt_factory(prof, self.settings)
            except Exception as e:
                log.exception("翻譯器載入失敗")
                mt = None
                note = (note + "；" if note else "") + f"翻譯器無法載入（{e}），只顯示原文"
            vad = self._vad_factory()
            self._warmup(asr)
            source = self._source_factory(self.settings)
            source.start()
        except Exception as e:
            log.exception("啟動失敗")
            with self._lock:
                if self._session == session:
                    self._set_state(PipelineState.FAILED, f"啟動失敗：{e}")
            return

        with self._lock:
            if stop.is_set() or self._session != session:
                source.stop()
                return
            self._asr, self._mt, self._source, self._active_profile = asr, mt, source, prof
            self._final_q.clear()
            self._partial_slot = None
            self._mt_q.clear()
            self._events = queue.Queue()
            self.board.clear()
            self._prompt_ctx, self._prompt_lang, self._utt_lang = "", None, {}
            self._partial_cost = 0.0
            seg_cfg = SegmenterConfig(max_segment_s=prof.max_segment_s,
                                      partial_interval_s=prof.partial_interval_s)
            self._threads = [
                threading.Thread(target=self._capture_loop, args=(session, stop, source, vad, seg_cfg),
                                 name="livesub-capture", daemon=True),
                threading.Thread(target=self._asr_loop, args=(session, stop), name="livesub-asr", daemon=True),
                threading.Thread(target=self._mt_loop, args=(session, stop), name="livesub-mt", daemon=True),
                threading.Thread(target=self._coord_loop, args=(session, stop), name="livesub-coord", daemon=True),
            ]
            for t in self._threads:
                t.start()
            self._set_state(PipelineState.RUNNING, note)

    def _build_asr(self):
        errors = []
        for p in profile_chain(self.profile):
            if self._require_models and not models.is_downloaded(p.asr_model):
                errors.append(f"{p.name}：模型未下載")
                continue
            try:
                eng = self._asr_factory(p)
            except Exception as e:
                log.warning("ASR 等級 %s 初始化失敗：%s", p.name, e)
                errors.append(f"{p.name}：{e}")
                continue
            note = "" if p.name == self.profile.name else f"已自動退回「{p.label}」（{errors[0]}）"
            return eng, p, note
        raise RuntimeError("沒有可用的辨識模型：" + "；".join(errors))

    @staticmethod
    def _warmup(asr) -> None:
        try:
            asr.transcribe(np.zeros(16000, np.float32), language="zh", final=False)
        except Exception as e:  # 暖機失敗不致命，真正推論時會再報錯
            log.warning("暖機失敗：%s", e)

    def _join(self, threads) -> None:
        for t in threads:
            t.join(timeout=8)
        with self._lock:
            src, self._source = self._source, None
            self._threads = []
        if src is not None:
            src.stop()
        with self._lock:
            if self._state == PipelineState.STOPPING:
                self._set_state(PipelineState.IDLE, "")

    # ---- capture -------------------------------------------------------
    def _capture_loop(self, session, stop, source: AudioSource, vad, seg_cfg) -> None:
        seg = Segmenter(seg_cfg)
        framer = Framer()
        base_interval = seg_cfg.partial_interval_s
        try:
            while not stop.is_set():
                chunk = source.read(timeout=0.2)
                if chunk is None:
                    if source.finished:
                        for ev in seg.flush():
                            self._submit(session, ev)
                        # 檔案來源播完：等最後的結果出來就停在 RUNNING（由使用者停止）
                        while not stop.is_set():
                            time.sleep(0.1)
                        break
                    continue
                self._level = float(getattr(source, "level", 0.0))
                frames = framer.push(chunk)
                if len(frames) == 0:
                    continue
                probs = vad.probs(frames)
                if base_interval > 0:  # 自適應：partial 間隔不得短於近期推論耗時的 1.3 倍
                    seg.cfg.partial_interval_s = max(base_interval, 1.3 * self._partial_cost)
                for ev in seg.feed(frames, probs):
                    self._submit(session, ev)
            for ev in seg.flush():
                self._submit(session, ev)
        except Exception as e:
            log.exception("收音迴圈錯誤")
            self._events.put(("error", session, f"收音錯誤：{e}"))

    def _submit(self, session: int, ev: SegEvent) -> None:
        now = self._clock()
        if ev.kind == "cancel":
            self._events.put(_Cancel(session, ev.utt_id))
            return
        req = _Req(ev.kind, ev.utt_id, ev.audio, ev.voiced_ratio, now, ev.forced)
        with self._asr_cv:
            if ev.kind == "partial":
                if self._partial_slot is not None:
                    self.stats["dropped_partials"] += 1
                self._partial_slot = req
            else:
                if self._partial_slot is not None and self._partial_slot.utt_id <= ev.utt_id:
                    self._partial_slot = None
                self._final_q.append(req)
                while len(self._final_q) > FINAL_QUEUE_MAX:
                    self._merge_oldest_finals(session)
            self._asr_cv.notify()

    def _merge_oldest_finals(self, session: int) -> None:
        """final 積欠過多：把最舊的兩段合併成一段（太長就丟掉最舊的）。"""
        a = self._final_q.popleft()
        b = self._final_q.popleft()
        self.stats["merged_finals"] += 1
        if (len(a.audio) + len(b.audio)) / 16000 > MAX_MERGED_S:
            self._events.put(_Cancel(session, a.utt_id))
            self._final_q.appendleft(b)
            return
        merged = _Req("final", b.utt_id, np.concatenate([a.audio, b.audio]),
                      min(a.voiced_ratio, b.voiced_ratio), a.emitted_at, b.forced,
                      a.merged_utts + [a.utt_id] + b.merged_utts)
        self._final_q.appendleft(merged)

    # ---- ASR -----------------------------------------------------------
    def _asr_loop(self, session, stop) -> None:
        while not stop.is_set():
            with self._asr_cv:
                while not stop.is_set() and not self._final_q and self._partial_slot is None:
                    self._asr_cv.wait(timeout=0.5)
                if stop.is_set():
                    return
                if self._final_q:
                    req = self._final_q.popleft()
                else:
                    req, self._partial_slot = self._partial_slot, None
            try:
                res = self._run_asr(req)
            except Exception as e:
                log.exception("辨識失敗")
                self._events.put(("error", session, f"辨識失敗：{e}"))
                continue
            for u in req.merged_utts:
                self._events.put(_Cancel(session, u))
            self._events.put(_AsrDone(session, req.kind, req.utt_id, res, req.voiced_ratio,
                                      req.emitted_at, req.forced))

    def _run_asr(self, req: _Req) -> AsrResult:
        src = self.settings.audio.source_language
        if src != "auto":
            language = hint = src
        else:
            # 自動模式：語言只信「這一句」不帶 prompt 的 partial 偵測結果。
            # 不知道語言時完全不給 prompt——中文提示會把英文句子帶偏（漏字、出現全形字元）。
            hint = self._utt_lang.get(req.utt_id)
            language = hint if req.kind == "partial" else None
        now = self._clock()
        if now - self._last_final_t > CONTEXT_RESET_S:
            self._prompt_ctx, self._prompt_lang = "", None
        static = ZH_PROMPT if hint == "zh" else ""
        ctx = self._prompt_ctx if hint is not None and hint == self._prompt_lang else ""
        budget = PROMPT_MAX_CHARS - len(static) - 1
        ctx = ctx[-budget:] if budget > 0 else ""  # 固定提示保留，上下文從尾端截
        prompt = " ".join(x for x in (static, ctx) if x) or None
        res = self._asr.transcribe(req.audio, language=language, prompt=prompt, final=req.kind == "final")
        if req.kind == "partial":
            self._partial_cost = res.seconds if self._partial_cost == 0 else 0.7 * self._partial_cost + 0.3 * res.seconds
            if res.language in ("zh", "en"):
                self._utt_lang.setdefault(req.utt_id, res.language)
        else:
            self._utt_lang.pop(req.utt_id, None)
            if len(self._utt_lang) > 64:
                self._utt_lang.clear()
        return res

    def _remember_context(self, text: str, lang: str) -> None:
        self._prompt_ctx = (self._prompt_ctx + " " + text).strip()[-PROMPT_MAX_CHARS:] \
            if lang == self._prompt_lang else text[-PROMPT_MAX_CHARS:]
        self._prompt_lang = lang
        self._last_final_t = self._clock()

    # ---- 翻譯 ----------------------------------------------------------
    def _queue_translation(self, session: int, line_id: int, text: str, src: str) -> None:
        if self._mt is None:
            self._events.put(_MtDone(session, line_id, "en" if src == "zh" else "zh", None))
            return
        ctx = [l.original for l in self.board.lines[-3:-1]]
        with self._mt_cv:
            self._mt_q.append((session, line_id, text, src, "en" if src == "zh" else "zh", ctx))
            while len(self._mt_q) > TRANSLATE_QUEUE_MAX:
                old = self._mt_q.popleft()
                self.stats["mt_skipped"] += 1
                self._events.put(_MtDone(old[0], old[1], old[4], None))
            self._mt_cv.notify()

    def _mt_loop(self, session, stop) -> None:
        while not stop.is_set():
            with self._mt_cv:
                while not stop.is_set() and not self._mt_q:
                    self._mt_cv.wait(timeout=0.5)
                if stop.is_set():
                    return
                sess, line_id, text, src, tgt, ctx = self._mt_q.popleft()
            try:
                out = self._mt.translate(text, src, tgt, ctx) or None
            except Exception as e:
                log.warning("翻譯失敗：%s", e)
                out = None
            self._events.put(_MtDone(sess, line_id, tgt, out))

    # ---- coordinator -----------------------------------------------------
    def _coord_loop(self, session, stop) -> None:
        self._emit_status(force=True)
        while not stop.is_set():
            try:
                ev = self._events.get(timeout=0.2)
            except queue.Empty:
                ev = None
            try:
                if ev is not None:
                    self._handle(session, ev)
                self._emit_snapshot()
                self._emit_status()
            except Exception:
                log.exception("coordinator 處理事件失敗")
        # 停止：清空字幕
        self.board.clear()
        self._emit_snapshot(force=True)

    def _handle(self, session: int, ev) -> None:
        if isinstance(ev, tuple):
            if ev[0] == "error" and ev[1] == session:
                self._message = ev[2]
                self._emit_status(force=True)
            return
        if getattr(ev, "session", None) != session:
            return  # 舊工作階段的結果
        if isinstance(ev, _Cancel):
            self.board.cancel_partial(ev.utt_id)
        elif isinstance(ev, _AsrDone):
            self._on_asr(session, ev)
        elif isinstance(ev, _MtDone):
            self.board.set_translation(ev.line_id, ev.tgt, ev.text)

    def _on_asr(self, session: int, ev: _AsrDone) -> None:
        r = ev.result
        tw = self.settings.display.taiwan_phrases
        if ev.kind == "partial":
            self.stats["partials"] += 1
            text = textproc.clean(r.text, confident=True, taiwan_phrases=tw)
            if text:
                self.board.set_partial(ev.utt_id, text, textproc.detect_lang(text, r.language))
            return
        self.stats["finals"] += 1
        low_conf = ((r.no_speech_prob is not None and r.no_speech_prob > 0.5)
                    or (r.avg_logprob is not None and r.avg_logprob < -0.8)
                    or ev.voiced_ratio < 0.4)
        text = textproc.clean(r.text, confident=not low_conf, taiwan_phrases=tw)
        if not text and r.text.strip():
            self.stats["filtered"] += 1
            log.debug("丟棄辨識結果 %r（low_conf=%s, voiced=%.2f）", r.text, low_conf, ev.voiced_ratio)
        lang = textproc.detect_lang(text, r.language) if text else (r.language or "zh")
        line = self.board.add_final(ev.utt_id, r.text, text, lang)
        if line is None:
            return
        silence = 0.0 if ev.forced else SegmenterConfig().end_silence_s
        self._latencies.append(self._clock() - ev.emitted_at + silence)
        log.info("final #%d [%s%s] %.2fs asr=%.2fs：%s", ev.utt_id, lang, "/切段" if ev.forced else "",
                 self._latencies[-1], r.seconds, text)
        self._remember_context(text, lang)
        self._queue_translation(session, line.id, text, lang)

    def _emit_snapshot(self, force: bool = False) -> None:
        d = self.settings.display
        snap = self.board.snapshot(d.mode, d.clear_after_s)
        key = (snap.revision, snap.visible, d.mode)
        if force or key != self._last_snapshot_key:
            self._last_snapshot_key = key
            try:
                self.on_snapshot(snap)
            except Exception:
                log.exception("on_snapshot callback 失敗")

    def latency_p50(self) -> float | None:
        if not self._latencies:
            return None
        return float(np.median(self._latencies))

    def _status(self) -> PipelineStatus:
        p = self._active_profile or self.profile
        return PipelineStatus(
            state=self._state, profile=p.name, profile_label=p.label,
            asr_model=getattr(self._asr, "name", p.asr_model), latency_s=self.latency_p50(),
            level=self._level, segmented=p.partial_interval_s == 0 or p.asr_device == "cpu",
            message=self._message,
        )

    def _emit_status(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_status_t < 0.2:
            return
        st = self._status()
        if force or st != self._last_status:
            self._last_status = st
            self._last_status_t = now
            try:
                self.on_status(st)
            except Exception:
                log.exception("on_status callback 失敗")

    def _set_state(self, state: PipelineState, message: str) -> None:
        self._state = state
        self._message = message
        self._emit_status(force=True)
