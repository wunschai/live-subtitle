"""Pipeline 狀態（UI 與核心共用，不依賴 Qt）。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class PipelineState(str, Enum):
    IDLE = "idle"
    LOADING = "loading"
    RUNNING = "running"
    STOPPING = "stopping"
    FAILED = "failed"


STATE_LABELS = {
    PipelineState.IDLE: "待命",
    PipelineState.LOADING: "載入模型中…",
    PipelineState.RUNNING: "收音中",
    PipelineState.STOPPING: "停止中…",
    PipelineState.FAILED: "發生錯誤",
}


@dataclass(frozen=True)
class PipelineStatus:
    state: PipelineState = PipelineState.IDLE
    profile: str = ""
    profile_label: str = ""
    asr_model: str = ""
    latency_s: float | None = None  # 最近一句 final 的延遲（句尾 → 出字）
    mt_latency_s: float | None = None  # 原文出現 → 譯文出現（中位數）
    level: float = 0.0  # 0–1 麥克風音量
    overflows: int = 0  # 音訊緩衝溢位（掉音）次數
    segmented: bool = False  # True = 分段字幕模式（CPU，沒有或很少 partial）
    message: str = ""  # 提示／警告

    @property
    def running(self) -> bool:
        return self.state in (PipelineState.LOADING, PipelineState.RUNNING)
