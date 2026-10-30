"""硬體等級（profile）表與自動選擇。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace

from .hardware import HardwareInfo


@dataclass(frozen=True)
class Profile:
    name: str
    label: str
    asr_backend: str  # "faster-whisper" / "openvino"
    asr_model: str  # models.REGISTRY key
    asr_device: str  # "cuda" / "cpu" / "GPU"（OpenVINO 裝置名）
    asr_compute_type: str  # faster-whisper 的 compute_type；OpenVINO 不用
    beam_final: int
    partial_interval_s: float  # 0 = 不出 partial
    mt_device: str  # "cuda" / "cpu"
    mt_compute_type: str
    cpu_threads: int = 0  # 0 = 自動
    max_segment_s: float = 8.0
    fallback: str | None = None  # 初始化或校正失敗時退到哪一級
    extra: str = "none"  # 需要的 pyproject extra

    def to_dict(self) -> dict:
        return asdict(self)


PROFILES: dict[str, Profile] = {p.name: p for p in [
    Profile("nvidia-high", "NVIDIA 高階（VRAM ≥ 8GB）", "faster-whisper", "breeze-asr-25", "cuda", "float16",
            beam_final=5, partial_interval_s=0.6, mt_device="cuda", mt_compute_type="float16",
            fallback="nvidia-mid", extra="cuda"),
    Profile("nvidia-mid", "NVIDIA 中階（VRAM 4–8GB）", "faster-whisper", "whisper-large-v3-turbo", "cuda",
            "int8_float16", beam_final=3, partial_interval_s=0.8, mt_device="cuda", mt_compute_type="int8_float16",
            fallback="nvidia-low", extra="cuda"),
    Profile("nvidia-low", "NVIDIA 入門（VRAM < 4GB）", "faster-whisper", "whisper-small", "cuda", "int8_float16",
            beam_final=3, partial_interval_s=0.8, mt_device="cpu", mt_compute_type="int8",
            fallback="cpu-high", extra="cuda"),
    Profile("intel-gpu", "Intel 內顯（OpenVINO）", "openvino", "ov-whisper-large-v3-turbo", "GPU", "",
            beam_final=1, partial_interval_s=1.5, mt_device="cpu", mt_compute_type="int8",
            fallback="intel-gpu-lite", extra="intel"),
    Profile("intel-gpu-lite", "Intel 內顯輕量（OpenVINO）", "openvino", "ov-whisper-small", "GPU", "",
            beam_final=1, partial_interval_s=1.0, mt_device="cpu", mt_compute_type="int8",
            fallback="cpu-high", extra="intel"),
    Profile("cpu-high", "CPU（6 核以上）", "faster-whisper", "whisper-small", "cpu", "int8",
            beam_final=1, partial_interval_s=0.0, mt_device="cpu", mt_compute_type="int8",
            max_segment_s=6.0, fallback="cpu-low"),
    Profile("cpu-low", "CPU 輕量", "faster-whisper", "whisper-base", "cpu", "int8",
            beam_final=1, partial_interval_s=2.0, mt_device="cpu", mt_compute_type="int8",
            max_segment_s=6.0, fallback=None),
]}


def select_profile(hw: HardwareInfo, openvino_gpu: bool | None = None) -> Profile:
    """依硬體挑等級。

    openvino_gpu：OpenVINO 是否真的看得到 GPU。None 表示尚未確認（安裝前），
    此時只要有 Intel 顯示卡就先假設可用。
    """
    nv = hw.nvidia
    if nv is not None and hw.os in ("windows", "linux"):
        if nv.vram_mb >= 7800:  # 標稱 8GB 的卡常回報 8188/7982 之類
            p = PROFILES["nvidia-high"]
        elif nv.vram_mb >= 3800:
            p = PROFILES["nvidia-mid"]
        else:
            p = PROFILES["nvidia-low"]
    elif (hw.intel_gpu is not None and hw.arch == "x86_64" and hw.os in ("windows", "linux")
          and openvino_gpu is not False):
        p = PROFILES["intel-gpu"] if hw.ram_gb >= 15 else PROFILES["intel-gpu-lite"]
    elif hw.physical_cores >= 6 and hw.ram_gb >= 7.5:
        p = PROFILES["cpu-high"]
    else:
        p = PROFILES["cpu-low"]
    return with_threads(p, hw)


def with_threads(p: Profile, hw: HardwareInfo) -> Profile:
    if p.cpu_threads:
        return p
    # CT2 在 CPU 上用實體核心數最穩；保留 2 核給 UI 與音訊
    return replace(p, cpu_threads=max(2, min(8, hw.physical_cores - 2)))


def downgrade(p: Profile) -> Profile | None:
    return PROFILES[p.fallback] if p.fallback else None


def get(name: str) -> Profile:
    if name not in PROFILES:
        raise KeyError(f"未知的等級：{name}（可用：{', '.join(PROFILES)}）")
    return PROFILES[name]
