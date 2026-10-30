"""斷句狀態機：VAD 機率 → partial / final / cancel 事件。

純邏輯、以幀為單位，不碰執行緒與模型，方便用合成的機率序列測試。
同一次 feed() 內發生的多個事件（例如句尾 final 緊接新句開始）依序放在回傳的列表裡。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np

from .vad import FRAME, SAMPLE_RATE

FRAME_S = FRAME / SAMPLE_RATE  # 0.032


@dataclass
class SegmenterConfig:
    start_threshold: float = 0.5
    end_threshold: float = 0.35  # 遲滯：低於這個才算靜音
    start_frames: int = 3  # 連續幾幀才算開始說話（約 96 ms）
    preroll_s: float = 0.4
    end_silence_s: float = 0.5
    tail_pad_s: float = 0.2  # final 音訊保留的句尾靜音
    min_speech_s: float = 0.2
    max_segment_s: float = 8.0
    cut_search_s: float = 2.0
    cut_min_gap_frames: int = 3  # 強制切段時優先找的低語音區間長度
    partial_interval_s: float = 0.0  # 0 = 不出 partial
    partial_min_s: float = 0.4


@dataclass
class SegEvent:
    kind: str  # "partial" / "final" / "cancel"
    utt_id: int
    audio: np.ndarray | None = None
    forced: bool = False  # final 是否因長度上限被強制切段
    voiced_ratio: float = 1.0  # 本段中 VAD 判為語音的幀比例（給幻覺判斷用）

    @property
    def duration(self) -> float:
        return 0.0 if self.audio is None else len(self.audio) / SAMPLE_RATE


class Segmenter:
    def __init__(self, cfg: SegmenterConfig | None = None):
        self.cfg = cfg or SegmenterConfig()
        self._next_id = 1
        self.reset()

    def reset(self) -> None:
        c = self.cfg
        self._pre: deque[tuple[np.ndarray, float]] = deque(maxlen=max(1, round(c.preroll_s / FRAME_S)))
        self._run = 0  # 連續高機率幀數（idle 狀態用）
        self._frames: list[np.ndarray] = []
        self._probs: list[float] = []
        self._speaking = False
        self._silence = 0
        self._voiced = 0  # 本段實際語音幀數
        self._lead = 0  # 段首 pre-roll 中「觸發前」的幀數（不算進語音覆蓋率）
        self._since_partial = 0
        self._utt = 0
        self._partials_sent = False

    @property
    def speaking(self) -> bool:
        return self._speaking

    @property
    def current_utt(self) -> int:
        return self._utt

    # ------------------------------------------------------------------
    def feed(self, frames: np.ndarray, probs: np.ndarray) -> list[SegEvent]:
        events: list[SegEvent] = []
        for f, p in zip(frames, probs):
            self._step(f, float(p), events)
        return events

    def flush(self) -> list[SegEvent]:
        """停止時把正在說的句子收尾。"""
        events: list[SegEvent] = []
        if self._speaking:
            self._finish(events, trailing=self._silence)
        self.reset()
        return events

    # ------------------------------------------------------------------
    def _new_utt(self) -> None:
        self._utt = self._next_id
        self._next_id += 1
        self._partials_sent = False
        self._since_partial = 0

    def _step(self, frame: np.ndarray, p: float, ev: list[SegEvent]) -> None:
        c = self.cfg
        if not self._speaking:
            self._pre.append((frame, p))
            self._run = self._run + 1 if p >= c.start_threshold else 0
            if self._run >= c.start_frames:
                self._speaking = True
                self._frames = [f for f, _ in self._pre]
                self._probs = [q for _, q in self._pre]
                self._pre.clear()
                self._voiced = self._run
                self._lead = max(0, len(self._frames) - self._run)
                self._silence = 0
                self._run = 0
                self._new_utt()
            return

        self._frames.append(frame)
        self._probs.append(p)
        self._since_partial += 1
        if p < c.end_threshold:
            self._silence += 1
        else:
            self._silence = 0
            self._voiced += 1

        if self._silence * FRAME_S >= c.end_silence_s:
            self._finish(ev, trailing=self._silence)
            return

        if len(self._frames) * FRAME_S >= c.max_segment_s:
            self._force_cut(ev)
            return

        if (c.partial_interval_s > 0 and self._since_partial * FRAME_S >= c.partial_interval_s
                and len(self._frames) * FRAME_S >= c.partial_min_s and self._silence == 0):
            self._since_partial = 0
            self._partials_sent = True
            ev.append(SegEvent("partial", self._utt, np.concatenate(self._frames)))

    def _finish(self, ev: list[SegEvent], trailing: int) -> None:
        c = self.cfg
        keep = max(0, trailing - round(c.tail_pad_s / FRAME_S))
        frames = self._frames[: len(self._frames) - keep] if keep else self._frames
        if self._voiced * FRAME_S < c.min_speech_s or not frames:
            if self._partials_sent:
                ev.append(SegEvent("cancel", self._utt))
        else:
            ev.append(SegEvent("final", self._utt, np.concatenate(frames),
                               voiced_ratio=self._ratio(len(self._frames) - trailing)))
        self._speaking = False
        self._frames, self._probs = [], []
        self._silence = self._voiced = 0
        self._run = 0
        self._pre.clear()

    def _ratio(self, n_frames: int) -> float:
        """語音覆蓋率：只算 pre-roll 之後、句尾靜音之前的核心區段。"""
        core = n_frames - self._lead
        return min(1.0, self._voiced / core) if core > 0 else 0.0

    def _find_cut(self) -> int:
        """回傳切點（幀索引，切在該幀之前）。

        優先：最後 cut_search_s 秒內，連續 ≥ cut_min_gap_frames 幀低於 end_threshold 的區間中央；
        找不到才在機率最低的幀之後硬切。
        """
        c = self.cfg
        n = len(self._frames)
        lo = max(1, n - round(c.cut_search_s / FRAME_S))
        best: tuple[int, int] | None = None  # (長度, 中點)
        run_start = None
        for i in range(lo, n + 1):
            low = i < n and self._probs[i] < c.end_threshold
            if low and run_start is None:
                run_start = i
            elif not low and run_start is not None:
                length = i - run_start
                if length >= c.cut_min_gap_frames and (best is None or length >= best[0]):
                    best = (length, run_start + length // 2)
                run_start = None
        if best is not None:
            return best[1]
        window = self._probs[lo:n]
        return lo + int(np.argmin(window)) + 1 if window else n

    def _force_cut(self, ev: list[SegEvent]) -> None:
        """在找到的切點切開，剩下的延續成下一段。"""
        c = self.cfg
        cut = self._find_cut()
        head, tail = self._frames[:cut], self._frames[cut:]
        head_voiced = sum(1 for q in self._probs[:cut] if q >= c.end_threshold)
        ev.append(SegEvent("final", self._utt, np.concatenate(head), forced=True,
                           voiced_ratio=min(1.0, head_voiced / max(1, len(head)))))
        tail_probs = self._probs[cut:]
        self._new_utt()
        self._frames, self._probs = list(tail), list(tail_probs)
        self._voiced = sum(1 for q in tail_probs if q >= c.end_threshold)
        self._lead = 0
        self._silence = 0
        for q in reversed(tail_probs):
            if q < c.end_threshold:
                self._silence += 1
            else:
                break
