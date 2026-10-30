"""ASR 後端：faster-whisper（CUDA/CPU）與 OpenVINO GenAI（Intel 內顯）。

兩個後端能力不同（見 CAPABILITIES）；不支援的信心指標回傳 None，由呼叫端跳過相關規則。
"""

from __future__ import annotations

import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

from .. import models, paths

log = logging.getLogger(__name__)

ZH_PROMPT = "以下是台灣的演講逐字稿，使用繁體中文與全形標點。"


@dataclass(frozen=True)
class AsrResult:
    text: str
    language: str | None  # Whisper 判斷的語言碼（zh / en / ja …）
    avg_logprob: float | None = None
    no_speech_prob: float | None = None
    seconds: float = 0.0  # 推論耗時


class ASREngine(Protocol):
    name: str

    def transcribe(self, audio: np.ndarray, language: str | None = None, prompt: str | None = None,
                   final: bool = True) -> AsrResult: ...


CAPABILITIES = {
    "faster-whisper": {"confidence": True, "prompt": True, "language_detect": True},
    "openvino": {"confidence": False, "prompt": True, "language_detect": True},
}

_dll_handles: list = []  # 必須活到程式結束，否則 DLL 搜尋路徑會失效


def add_cuda_dll_dirs() -> list[str]:
    """Windows：把 pip 裝的 nvidia-cublas/cudnn DLL 資料夾加進搜尋路徑（需在載入 CT2 前呼叫）。"""
    if os.name != "nt":
        return []
    added = []
    for p in sys.path:
        base = Path(p) / "nvidia"
        if not base.is_dir():
            continue
        for sub in base.iterdir():
            b = sub / "bin"
            if b.is_dir():
                try:
                    _dll_handles.append(os.add_dll_directory(str(b)))
                    os.environ["PATH"] = str(b) + os.pathsep + os.environ.get("PATH", "")
                    added.append(str(b))
                except OSError:
                    pass
        break
    return added


_COMPUTE_FALLBACK = ("float16", "int8_float16", "int8", "int8_float32", "float32")


def pick_compute_type(device: str, wanted: str) -> str:
    """舊顯示卡（例如 Pascal）不支援 float16 / int8_float16：改用該裝置支援的最佳選項。"""
    try:
        import ctranslate2

        supported = ctranslate2.get_supported_compute_types(device)
    except Exception:
        return wanted
    if wanted in supported:
        return wanted
    start = _COMPUTE_FALLBACK.index(wanted) if wanted in _COMPUTE_FALLBACK else 0
    for ct in _COMPUTE_FALLBACK[start:] + _COMPUTE_FALLBACK[:start]:
        if ct in supported:
            log.warning("%s 不支援 %s，改用 %s", device, wanted, ct)
            return ct
    return wanted


def _restrict_lang(lang: str | None, probs: list[tuple[str, float]] | None) -> str | None:
    """只接受 zh / en；其他語言（ja、ko、yue…）改挑 zh/en 中機率較高者。"""
    if lang in ("zh", "en"):
        return lang
    if lang in ("yue", "wuu"):  # 粵語、吳語的文字通常仍是中文
        return "zh"
    if probs:
        d = dict(probs)
        return "zh" if d.get("zh", 0) >= d.get("en", 0) else "en"
    return None


class FasterWhisperEngine:
    def __init__(self, model_path: str | Path, device: str = "cpu", compute_type: str = "int8",
                 cpu_threads: int = 0, beam_final: int = 1):
        if device == "cuda":
            add_cuda_dll_dirs()
        from faster_whisper import WhisperModel

        compute_type = pick_compute_type(device, compute_type)
        self.name = f"faster-whisper:{Path(model_path).name}"
        self.beam_final = beam_final
        self.model = WhisperModel(str(model_path), device=device, compute_type=compute_type,
                                  cpu_threads=cpu_threads, num_workers=1)

    def transcribe(self, audio, language=None, prompt=None, final=True) -> AsrResult:
        t = time.perf_counter()
        kw = dict(
            beam_size=self.beam_final if final else 1,
            condition_on_previous_text=False, initial_prompt=prompt or None,
            without_timestamps=True, vad_filter=False,
            no_speech_threshold=0.6, compression_ratio_threshold=2.4, log_prob_threshold=-1.0,
            temperature=[0.0, 0.2, 0.4] if final else 0.0,
        )
        segs, info = self.model.transcribe(audio, language=language, task="transcribe", **kw)
        segs = list(segs)
        lang = info.language
        if language is None:
            fixed = _restrict_lang(lang, getattr(info, "all_language_probs", None))
            if fixed and fixed != lang:  # 誤判成 zh/en 以外：用 zh/en 重跑一次
                segs, info = self.model.transcribe(audio, language=fixed, task="transcribe", **kw)
                segs = list(segs)
            lang = fixed
        text = "".join(s.text for s in segs).strip()
        logp = float(np.mean([s.avg_logprob for s in segs])) if segs else None
        nosp = float(max(s.no_speech_prob for s in segs)) if segs else None
        return AsrResult(text, lang, logp, nosp, time.perf_counter() - t)


class OpenVINOWhisperEngine:
    def __init__(self, model_path: str | Path, device: str = "GPU"):
        import openvino_genai as ov_genai

        cache = paths.cache_dir() / "openvino"
        cache.mkdir(parents=True, exist_ok=True)
        self.name = f"openvino:{Path(model_path).name}@{device}"
        self.pipe = ov_genai.WhisperPipeline(str(model_path), device, CACHE_DIR=str(cache))

    def transcribe(self, audio, language=None, prompt=None, final=True) -> AsrResult:
        t = time.perf_counter()
        audio = np.ascontiguousarray(audio, np.float32)
        r = self._generate(audio, language, prompt)
        detected = getattr(r, "language", None) or language
        lang = _restrict_lang(detected, None)
        if lang is None:  # 判成日文、韓文等：OpenVINO 沒有語言機率可比，預設以中文重跑
            lang = "zh"
            r = self._generate(audio, lang, prompt)
        text = (r.texts[0] if r.texts else "").strip()
        return AsrResult(text, lang, None, None, time.perf_counter() - t)

    def _generate(self, audio, language, prompt):
        cfg = self.pipe.get_generation_config()
        cfg.task = "transcribe"
        cfg.return_timestamps = False
        cfg.language = f"<|{language}|>" if language else None
        cfg.initial_prompt = prompt or None
        return self.pipe.generate(audio, cfg)


def build_asr(profile) -> ASREngine:
    path = models.resolve(profile.asr_model)
    if profile.asr_backend == "openvino":
        return OpenVINOWhisperEngine(path, profile.asr_device)
    return FasterWhisperEngine(path, profile.asr_device, profile.asr_compute_type,
                               profile.cpu_threads, profile.beam_final)
