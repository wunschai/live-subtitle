"""串流版 Silero VAD（使用 faster-whisper 內附的 silero_vad_v6.onnx，不需要 torch）。"""

from __future__ import annotations

import os

import numpy as np

SAMPLE_RATE = 16000
FRAME = 512  # 32 ms
_CTX = 64


def _model_path() -> str:
    from faster_whisper.utils import get_assets_path

    return os.path.join(get_assets_path(), "silero_vad_v6.onnx")


class SileroVAD:
    """逐幀回傳語音機率。狀態（h/c 與前一幀尾端 context）跨呼叫延續。"""

    def __init__(self, path: str | None = None):
        import onnxruntime

        opts = onnxruntime.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        opts.enable_cpu_mem_arena = False
        opts.log_severity_level = 4
        self._sess = onnxruntime.InferenceSession(
            path or _model_path(), providers=["CPUExecutionProvider"], sess_options=opts)
        self.reset()

    def reset(self) -> None:
        self._h = np.zeros((1, 1, 128), np.float32)
        self._c = np.zeros((1, 1, 128), np.float32)
        self._ctx = np.zeros(_CTX, np.float32)

    def probs(self, frames: np.ndarray) -> np.ndarray:
        """frames: (n, 512) float32 → (n,) 機率。"""
        if frames.size == 0:
            return np.zeros(0, np.float32)
        frames = np.asarray(frames, np.float32).reshape(-1, FRAME)
        prev = np.vstack([self._ctx[None], frames[:-1, -_CTX:]])
        x = np.concatenate([prev, frames], axis=1)
        out, self._h, self._c = self._sess.run(None, {"input": x, "h": self._h, "c": self._c})
        self._ctx = frames[-1, -_CTX:].copy()
        return np.asarray(out, np.float32).reshape(-1)


class Framer:
    """把任意長度的音訊切成 512 樣本的幀，餘數留到下次。"""

    def __init__(self):
        self._rest = np.zeros(0, np.float32)

    def push(self, audio: np.ndarray) -> np.ndarray:
        a = np.concatenate([self._rest, np.asarray(audio, np.float32).reshape(-1)])
        n = len(a) // FRAME
        self._rest = a[n * FRAME:]
        return a[: n * FRAME].reshape(n, FRAME)

    def reset(self) -> None:
        self._rest = np.zeros(0, np.float32)
