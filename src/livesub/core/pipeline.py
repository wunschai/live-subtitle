"""Pipeline：收音 → VAD → 斷句 → ASR → 後處理 → 字幕板 → 翻譯。

每次 start() 建立一個獨立的 _Session：佇列、字幕板、上下文、引擎都屬於該次工作階段，
舊工作階段的執行緒就算晚結束，也碰不到新工作階段的任何狀態。

執行緒（每個工作階段各一組）：
- loader：載入模型（逐級嘗試，必須通過實際推論才採用）、開麥克風
- capture：讀音訊、跑 VAD 與 Segmenter，把請求交給 ASR worker
- asr：final 優先；partial 只有一個待處理槽（新的覆蓋舊的）
- mt：有上限的翻譯佇列，滿了丟最舊的
- coordinator：唯一擁有 CaptionBoard 的執行緒；其他執行緒只送事件進來
UI 透過 on_snapshot / on_status callback 收到不可變的快照（在背景執行緒呼叫，UI 端需轉回主執行緒）。
"""

from __future__ import annotations

import copy
import logging
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass, field, replace
from typing import Callable

import numpy as np

from .. import models, profiles
from ..config import Settings
from ..profiles import Profile
from . import textproc
from .asr import ZH_PROMPT, AsrResult
from .audio import AudioSource, MicSource
from .captions import EMPTY_SNAPSHOT, BoardSnapshot, CaptionBoard
from .segmenter import SegEvent, Segmenter, SegmenterConfig
from .status import PipelineState, PipelineStatus
from .vad import Framer, SileroVAD

log = logging.getLogger(__name__)

PROMPT_MAX_CHARS = 120
CONTEXT_RESET_S = 10.0
FINAL_QUEUE_MAX = 4
TRANSLATE_QUEUE_MAX = 6
MAX_MERGED_S = 20.0
MAX_ASR_ERRORS = 3  # 連續推論失敗幾次就判定這次工作失敗
JOIN_TIMEOUT_S = 15.0


# ---- 事件（送進 coordinator） --------------------------------------------
@dataclass(frozen=True)
class _AsrDone:
    kind: str  # "partial" / "final"
    utt_id: int
    result: AsrResult
    voiced_ratio: float
    emitted_at: float  # segmenter 發出請求的時間
    forced: bool


@dataclass(frozen=True)
class _Cancel:
    utt_id: int


@dataclass(frozen=True)
class _MtDone:
    line_id: int
    tgt: str
    text: str | None  # None = 失敗或被略過
    queued_at: float


@dataclass
class _Req:
    kind: str
    utt_id: int
    audio: np.ndarray
    voiced_ratio: float
    emitted_at: float
    forced: bool = False
    merged_utts: list[int] = field(default_factory=list)


def _new_stats() -> dict:
    return {"finals": 0, "partials": 0, "dropped_partials": 0, "merged_finals": 0, "mt_skipped": 0,
            "filtered": 0, "asr_errors": 0}


class _Session:
    """一次 start() 到 stop() 的所有狀態。"""

    def __init__(self, sid: int, clock):
        self.id = sid
        self.stop = threading.Event()
        self.events: queue.Queue = queue.Queue()
        self.final_q: deque[_Req] = deque()
        self.partial_slot: _Req | None = None
        self.asr_cv = threading.Condition()
        self.mt_q: deque[tuple] = deque()
        self.mt_cv = threading.Condition()
        self.board = CaptionBoard(clock=clock)
        self.latencies: deque[float] = deque(maxlen=20)
        self.mt_latencies: deque[float] = deque(maxlen=20)
        self.partial_cost = 0.0  # partial 推論耗時的指數平均
        self.prompt_ctx = ""
        self.prompt_lang: str | None = None
        self.last_final_t = 0.0
        self.utt_lang: dict[int, str] = {}
        self.asr_errors = 0
        self.level = 0.0
        self.asr = None
        self.mt = None
        self.source: AudioSource | None = None
        self.profile: Profile | None = None
        self.threads: list[threading.Thread] = []
        self.last_snapshot_key = None
        self.stats = _new_stats()

    def alive_threads(self) -> list[threading.Thread]:
        return [t for t in self.threads if t.is_alive()]

    def wake(self) -> None:
        with self.asr_cv:
            self.asr_cv.notify_all()
        with self.mt_cv:
            self.mt_cv.notify_all()


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
        self._next_sid = 0
        self._cur: _Session | None = None
        self._lingering: list[threading.Thread] = []  # 停止逾時仍未結束的舊執行緒
        self._state = PipelineState.IDLE
        self._message = ""
        self._active_profile: Profile | None = None
        self._last_status: PipelineStatus | None = None
        self._last_status_t = 0.0
        self._empty_board = CaptionBoard(clock=clock)

    # ---- 對外 API ------------------------------------------------------
    @property
    def state(self) -> PipelineState:
        return self._state

    @property
    def active_profile(self) -> Profile | None:
        return self._active_profile

    @property
    def board(self) -> CaptionBoard:
        s = self._cur
        return s.board if s is not None else self._empty_board

    @property
    def stats(self) -> dict:
        s = self._cur
        return s.stats if s is not None else _new_stats()

    def start(self) -> None:
        with self._lock:
            if self._state not in (PipelineState.IDLE, PipelineState.FAILED):
                return
            self._lingering = [t for t in self._lingering if t.is_alive()]
            if self._lingering:
                # 上一次的推論還卡著：不能開新工作階段，否則兩組執行緒會搶裝置
                self._message = "上一次的辨識還在結束中，請稍候再試"
                self._emit_status(force=True)
                return
            self._next_sid += 1
            s = _Session(self._next_sid, self._clock)
            self._cur = s
            self._set_state(PipelineState.LOADING, "")
            loader = threading.Thread(target=self._load_and_run, args=(s,), name="livesub-loader", daemon=True)
            s.threads.append(loader)
            loader.start()

    def stop(self, wait: bool = False) -> None:
        self._end_session(PipelineState.IDLE, "", wait)

    def update_settings(self, settings: Settings) -> None:
        """顯示相關設定立即生效（在任何執行緒呼叫都可以）。麥克風變更需重新 start。"""
        self.settings = copy.deepcopy(settings)
        s = self._cur
        if s is not None:
            s.events.put(("refresh",))

    def wait_idle(self, timeout: float = 10.0) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self._state in (PipelineState.IDLE, PipelineState.FAILED):
                return True
            time.sleep(0.02)
        return False

    # ---- 結束工作階段 ----------------------------------------------------
    def _end_session(self, final_state: PipelineState, message: str, wait: bool = False,
                     only: _Session | None = None) -> None:
        with self._lock:
            s = self._cur
            if s is None or (only is not None and s is not only):
                return
            if self._state in (PipelineState.IDLE, PipelineState.STOPPING) or \
                    (self._state == PipelineState.FAILED and final_state == PipelineState.FAILED):
                return
            self._set_state(PipelineState.STOPPING, "")
            s.stop.set()
        s.wake()
        t = threading.Thread(target=self._join, args=(s, final_state, message), name="livesub-stopper",
                             daemon=True)
        t.start()
        if wait:
            t.join(timeout=JOIN_TIMEOUT_S + 5)

    def _join(self, s: _Session, final_state: PipelineState, message: str) -> None:
        me = threading.current_thread()
        deadline = time.monotonic() + JOIN_TIMEOUT_S
        for t in list(s.threads):
            if t is not me:
                t.join(timeout=max(0.1, deadline - time.monotonic()))
        if s.source is not None:
            try:
                s.source.stop()
            except Exception:
                log.exception("關閉音訊來源失敗")
        alive = [t for t in s.alive_threads() if t is not me]
        if alive:
            log.warning("仍有 %d 個執行緒未結束：%s", len(alive), [t.name for t in alive])
        with self._lock:
            self._lingering += alive
            if self._cur is s:
                try:
                    self.on_snapshot(EMPTY_SNAPSHOT)  # 停止後清空字幕
                except Exception:
                    log.exception("on_snapshot callback 失敗")
                if alive and not message:
                    message = "部分背景工作仍在結束中"
                self._set_state(final_state, message)

    def _fail(self, s: _Session, message: str) -> None:
        log.error("工作階段失敗：%s", message)
        self._end_session(PipelineState.FAILED, message, only=s)

    # ---- 載入 ----------------------------------------------------------
    def _load_and_run(self, s: _Session) -> None:
        try:
            asr, prof, note = self._build_asr(s)
            if s.stop.is_set():
                return
            mt, mt_note = self._build_mt(prof)
            if mt_note:
                note = (note + "；" if note else "") + mt_note
            vad = self._vad_factory()
            if s.stop.is_set():
                return
            source = self._source_factory(self.settings)
            s.source = source  # 先登記，停止時才關得到
            source.start()
        except Exception as e:
            log.exception("啟動失敗")
            with self._lock:
                if self._cur is s and not s.stop.is_set():
                    s.stop.set()
                    self._set_state(PipelineState.FAILED, f"啟動失敗：{e}")
            if s.source is not None:
                s.source.stop()
            return

        with self._lock:
            if s.stop.is_set() or self._cur is not s:
                return  # stopper 會負責關 source
            s.asr, s.mt, s.profile = asr, mt, prof
            self._active_profile = prof
            seg_cfg = SegmenterConfig(max_segment_s=prof.max_segment_s, partial_interval_s=prof.partial_interval_s)
            workers = [
                threading.Thread(target=self._capture_loop, args=(s, source, vad, seg_cfg),
                                 name="livesub-capture", daemon=True),
                threading.Thread(target=self._asr_loop, args=(s,), name="livesub-asr", daemon=True),
                threading.Thread(target=self._mt_loop, args=(s,), name="livesub-mt", daemon=True),
                threading.Thread(target=self._coord_loop, args=(s,), name="livesub-coord", daemon=True),
            ]
            s.threads += workers
            for t in workers:
                t.start()
            self._set_state(PipelineState.RUNNING, note)

    def _build_asr(self, s: _Session):
        """沿 fallback 鏈逐級嘗試；必須「載入＋實際推論」都成功才採用。"""
        errors = []
        for p in profile_chain(self.profile):
            if s.stop.is_set():
                raise RuntimeError("已停止")
            if self._require_models and not models.is_downloaded(p.asr_model):
                errors.append(f"{p.label}：模型未下載")
                continue
            try:
                eng = self._asr_factory(p)
                eng.transcribe(np.zeros(16000, np.float32), language="zh", final=False)  # 暖機兼驗證
            except Exception as e:
                log.warning("ASR 等級 %s 無法使用：%s", p.name, e)
                errors.append(f"{p.label}：{e}")
                continue
            note = "" if p.name == self.profile.name else f"已自動退回「{p.label}」（{errors[0]}）"
            return eng, p, note
        raise RuntimeError("沒有可用的辨識模型：" + "；".join(errors))

    def _build_mt(self, p: Profile):
        try:
            return self._mt_factory(p, self.settings), ""
        except Exception as e:
            log.warning("翻譯器載入失敗（%s）：%s", p.mt_device, e)
            if p.mt_device != "cpu":  # 顯示卡上載不起來就改用 CPU
                try:
                    return self._mt_factory(replace(p, mt_device="cpu", mt_compute_type="int8"), self.settings), ""
                except Exception as e2:
                    e = e2
            return None, f"翻譯器無法載入（{e}），只顯示原文"

    # ---- capture -------------------------------------------------------
    def _capture_loop(self, s: _Session, source: AudioSource, vad, seg_cfg) -> None:
        seg = Segmenter(seg_cfg)
        framer = Framer()
        base_interval = seg_cfg.partial_interval_s
        try:
            while not s.stop.is_set():
                chunk = source.read(timeout=0.2)
                if chunk is None:
                    if source.finished:  # 檔案來源播完：收尾後停在 RUNNING，等使用者停止
                        for ev in seg.flush():
                            self._submit(s, ev)
                        s.stop.wait()
                        return
                    continue
                s.level = float(getattr(source, "level", 0.0))
                frames = framer.push(chunk)
                if len(frames) == 0:
                    continue
                probs = vad.probs(frames)
                if base_interval > 0:  # 自適應：partial 間隔不得短於近期推論耗時的 1.3 倍
                    seg.cfg.partial_interval_s = max(base_interval, 1.3 * s.partial_cost)
                for ev in seg.feed(frames, probs):
                    self._submit(s, ev)
            for ev in seg.flush():
                self._submit(s, ev)
        except Exception as e:
            log.exception("收音迴圈錯誤")
            self._fail(s, f"收音錯誤：{e}")

    def _submit(self, s: _Session, ev: SegEvent) -> None:
        now = self._clock()
        if ev.kind == "cancel":
            s.events.put(_Cancel(ev.utt_id))
            return
        req = _Req(ev.kind, ev.utt_id, ev.audio, ev.voiced_ratio, now, ev.forced)
        with s.asr_cv:
            if ev.kind == "partial":
                if s.partial_slot is not None:
                    s.stats["dropped_partials"] += 1
                s.partial_slot = req
            else:
                if s.partial_slot is not None and s.partial_slot.utt_id <= ev.utt_id:
                    s.partial_slot = None
                s.final_q.append(req)
                while len(s.final_q) > FINAL_QUEUE_MAX:
                    self._merge_oldest_finals(s)
            s.asr_cv.notify()

    @staticmethod
    def _merge_oldest_finals(s: _Session) -> None:
        """final 積欠過多：把最舊的兩段合併成一段（太長就丟掉最舊的）。呼叫端持有 asr_cv。"""
        a = s.final_q.popleft()
        b = s.final_q.popleft()
        s.stats["merged_finals"] += 1
        if (len(a.audio) + len(b.audio)) / 16000 > MAX_MERGED_S:
            for u in a.merged_utts + [a.utt_id]:
                s.events.put(_Cancel(u))
            s.final_q.appendleft(b)
            return
        merged = _Req("final", b.utt_id, np.concatenate([a.audio, b.audio]),
                      min(a.voiced_ratio, b.voiced_ratio), a.emitted_at, b.forced,
                      a.merged_utts + [a.utt_id] + b.merged_utts)
        s.final_q.appendleft(merged)

    # ---- ASR -----------------------------------------------------------
    def _asr_loop(self, s: _Session) -> None:
        while not s.stop.is_set():
            with s.asr_cv:
                while not s.stop.is_set() and not s.final_q and s.partial_slot is None:
                    s.asr_cv.wait(timeout=0.5)
                if s.stop.is_set():
                    return
                if s.final_q:
                    req = s.final_q.popleft()
                else:
                    req, s.partial_slot = s.partial_slot, None
            try:
                res = self._run_asr(s, req)
            except Exception as e:
                log.exception("辨識失敗")
                s.stats["asr_errors"] += 1
                s.asr_errors += 1
                for u in req.merged_utts + [req.utt_id]:  # 清掉這段的 partial，避免永遠不淡出
                    s.events.put(_Cancel(u))
                if s.asr_errors >= MAX_ASR_ERRORS:
                    self._fail(s, f"辨識持續失敗：{e}")
                    return
                s.events.put(("message", f"辨識失敗：{e}"))
                continue
            if req.kind == "final":  # 只有 final 成功才算恢復（partial 能跑不代表 final 能跑）
                s.asr_errors = 0
            for u in req.merged_utts:
                s.events.put(_Cancel(u))
            s.events.put(_AsrDone(req.kind, req.utt_id, res, req.voiced_ratio, req.emitted_at, req.forced))

    def _run_asr(self, s: _Session, req: _Req) -> AsrResult:
        src = self.settings.audio.source_language
        if src != "auto":
            language = hint = src
        else:
            # 自動模式：語言只信「這一句」不帶 prompt 的 partial 偵測結果。
            # 不知道語言時完全不給 prompt——中文提示會把英文句子帶偏（漏字、出現全形字元）。
            hint = s.utt_lang.get(req.utt_id)
            language = hint if req.kind == "partial" else None
        if self._clock() - s.last_final_t > CONTEXT_RESET_S:
            s.prompt_ctx, s.prompt_lang = "", None
        static = ZH_PROMPT if hint == "zh" else ""
        ctx = s.prompt_ctx if hint is not None and hint == s.prompt_lang else ""
        budget = PROMPT_MAX_CHARS - len(static) - 1
        ctx = ctx[-budget:] if budget > 0 else ""  # 固定提示保留，上下文從尾端截
        prompt = " ".join(x for x in (static, ctx) if x) or None
        res = s.asr.transcribe(req.audio, language=language, prompt=prompt, final=req.kind == "final")
        if req.kind == "partial":
            s.partial_cost = res.seconds if s.partial_cost == 0 else 0.7 * s.partial_cost + 0.3 * res.seconds
            if res.language in ("zh", "en"):
                s.utt_lang.setdefault(req.utt_id, res.language)
        else:
            for u in req.merged_utts + [req.utt_id]:
                s.utt_lang.pop(u, None)
            if len(s.utt_lang) > 64:
                s.utt_lang.clear()
        return res

    # ---- 翻譯 ----------------------------------------------------------
    def _queue_translation(self, s: _Session, line_id: int, text: str, src: str) -> None:
        tgt = "en" if src == "zh" else "zh"
        now = self._clock()
        if s.mt is None:
            s.events.put(_MtDone(line_id, tgt, None, now))
            return
        ctx = [l.original for l in s.board.lines[-3:-1]]
        with s.mt_cv:
            s.mt_q.append((line_id, text, src, tgt, ctx, now))
            while len(s.mt_q) > TRANSLATE_QUEUE_MAX:
                old = s.mt_q.popleft()
                s.stats["mt_skipped"] += 1
                s.events.put(_MtDone(old[0], old[3], None, old[5]))
            s.mt_cv.notify()

    def _mt_loop(self, s: _Session) -> None:
        while not s.stop.is_set():
            with s.mt_cv:
                while not s.stop.is_set() and not s.mt_q:
                    s.mt_cv.wait(timeout=0.5)
                if s.stop.is_set():
                    return
                line_id, text, src, tgt, ctx, queued = s.mt_q.popleft()
            try:
                out = s.mt.translate(text, src, tgt, ctx) or None
            except Exception as e:
                log.warning("翻譯失敗：%s", e)
                out = None
            s.events.put(_MtDone(line_id, tgt, out, queued))

    # ---- coordinator -----------------------------------------------------
    def _coord_loop(self, s: _Session) -> None:
        self._emit_status(force=True)
        while not s.stop.is_set():
            try:
                ev = s.events.get(timeout=0.2)
            except queue.Empty:
                ev = None
            try:
                if ev is not None:
                    self._handle(s, ev)
                self._emit_snapshot(s)
                self._emit_status()
            except Exception:
                log.exception("coordinator 處理事件失敗")

    def _handle(self, s: _Session, ev) -> None:
        if isinstance(ev, tuple):
            if ev[0] == "message" and self._cur is s:
                self._message = ev[1]
                self._emit_status(force=True)
            return
        if isinstance(ev, _Cancel):
            s.board.cancel_partial(ev.utt_id)
        elif isinstance(ev, _AsrDone):
            self._on_asr(s, ev)
        elif isinstance(ev, _MtDone):
            if s.board.set_translation(ev.line_id, ev.tgt, ev.text) and ev.text:
                s.mt_latencies.append(self._clock() - ev.queued_at)

    def _on_asr(self, s: _Session, ev: _AsrDone) -> None:
        r = ev.result
        tw = self.settings.display.taiwan_phrases
        if ev.kind == "partial":
            s.stats["partials"] += 1
            text = textproc.clean(r.text, confident=True, taiwan_phrases=tw)
            if text:
                s.board.set_partial(ev.utt_id, text, textproc.detect_lang(text, r.language))
            return
        s.stats["finals"] += 1
        low_conf = ((r.no_speech_prob is not None and r.no_speech_prob > 0.5)
                    or (r.avg_logprob is not None and r.avg_logprob < -0.8)
                    or ev.voiced_ratio < 0.4)
        text = textproc.clean(r.text, confident=not low_conf, taiwan_phrases=tw)
        if not text and r.text.strip():
            s.stats["filtered"] += 1
            log.debug("丟棄辨識結果 %r（low_conf=%s, voiced=%.2f）", r.text, low_conf, ev.voiced_ratio)
        lang = textproc.detect_lang(text, r.language) if text else (r.language or "zh")
        line = s.board.add_final(ev.utt_id, r.text, text, lang)
        if line is None:
            return
        silence = 0.0 if ev.forced else SegmenterConfig().end_silence_s
        s.latencies.append(self._clock() - ev.emitted_at + silence)
        log.info("final #%d [%s%s] %.2fs asr=%.2fs：%s", ev.utt_id, lang, "/切段" if ev.forced else "",
                 s.latencies[-1], r.seconds, text)
        self._remember_context(s, text, lang)
        self._queue_translation(s, line.id, text, lang)

    def _remember_context(self, s: _Session, text: str, lang: str) -> None:
        s.prompt_ctx = (s.prompt_ctx + " " + text).strip()[-PROMPT_MAX_CHARS:] \
            if lang == s.prompt_lang else text[-PROMPT_MAX_CHARS:]
        s.prompt_lang = lang
        s.last_final_t = self._clock()

    def _emit_snapshot(self, s: _Session) -> None:
        d = self.settings.display
        snap = s.board.snapshot(d.mode, d.clear_after_s)
        key = (snap.revision, snap.visible, d.mode)
        if key == s.last_snapshot_key:
            return
        s.last_snapshot_key = key
        with self._lock:
            if self._cur is not s or s.stop.is_set():
                return  # 舊工作階段不得再改畫面
            try:
                self.on_snapshot(snap)
            except Exception:
                log.exception("on_snapshot callback 失敗")

    # ---- 狀態 ----------------------------------------------------------
    @staticmethod
    def _median(xs) -> float | None:
        return float(np.median(xs)) if xs else None

    def latency_p50(self) -> float | None:
        s = self._cur
        return self._median(s.latencies) if s else None

    def _status(self) -> PipelineStatus:
        p = self._active_profile or self.profile
        s = self._cur
        return PipelineStatus(
            state=self._state, profile=p.name, profile_label=p.label,
            asr_model=getattr(s.asr if s else None, "name", p.asr_model),
            latency_s=self._median(s.latencies) if s else None,
            mt_latency_s=self._median(s.mt_latencies) if s else None,
            level=s.level if s else 0.0,
            overflows=int(getattr(s.source, "overflows", 0) or 0) if s else 0, segmented=p.partial_interval_s == 0 or p.asr_device == "cpu",
            message=self._message,
        )

    def _emit_status(self, force: bool = False) -> None:
        with self._lock:
            now = time.monotonic()
            if not force and now - self._last_status_t < 0.2:
                return
            st = self._status()
            if not force and st == self._last_status:
                return
            self._last_status = st
            self._last_status_t = now
            try:
                self.on_status(st)
            except Exception:
                log.exception("on_status callback 失敗")

    def _set_state(self, state: PipelineState, message: str) -> None:
        with self._lock:
            self._state = state
            self._message = message
            self._emit_status(force=True)
