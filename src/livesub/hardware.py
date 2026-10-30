"""硬體偵測。

只用標準函式庫，安裝前就能直接執行：

    python src/livesub/hardware.py            # 印出偵測結果（JSON）
    python src/livesub/hardware.py --print-extra   # 印出要安裝的 extra：cuda / intel / none
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field


@dataclass
class GPUInfo:
    vendor: str  # "nvidia" / "intel" / "amd" / "apple" / "other"
    name: str
    vram_mb: int = 0  # 只有 NVIDIA 可靠；內顯共用記憶體記 0


@dataclass
class HardwareInfo:
    os: str  # "windows" / "linux" / "darwin"
    arch: str  # "x86_64" / "arm64"
    cpu_name: str
    physical_cores: int
    logical_cores: int
    ram_gb: float
    gpus: list[GPUInfo] = field(default_factory=list)

    @property
    def nvidia(self) -> GPUInfo | None:
        cands = [g for g in self.gpus if g.vendor == "nvidia"]
        return max(cands, key=lambda g: g.vram_mb) if cands else None

    @property
    def intel_gpu(self) -> GPUInfo | None:
        return next((g for g in self.gpus if g.vendor == "intel"), None)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "HardwareInfo":
        d = dict(d)
        d["gpus"] = [GPUInfo(**g) for g in d.get("gpus", [])]
        return cls(**d)


def _run(cmd: list[str], timeout: float = 15) -> str:
    try:
        kw = {}
        if os.name == "nt":
            kw["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        out = subprocess.run(cmd, capture_output=True, timeout=timeout, **kw)
        if out.returncode != 0:
            return ""
        return out.stdout.decode("utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError):
        return ""


def vendor_of(name: str) -> str:
    n = name.lower()
    if "nvidia" in n or "geforce" in n or "quadro" in n or "rtx" in n:
        return "nvidia"
    if "intel" in n:
        return "intel"
    if "amd" in n or "radeon" in n:
        return "amd"
    if "apple" in n:
        return "apple"
    return "other"


def parse_nvidia_smi(text: str) -> list[GPUInfo]:
    """解析 `nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits`。"""
    gpus = []
    for line in text.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2 or not parts[0]:
            continue
        m = re.search(r"\d+", parts[1])
        gpus.append(GPUInfo("nvidia", parts[0], int(m.group()) if m else 0))
    return gpus


def parse_video_controllers(names: list[str]) -> list[GPUInfo]:
    """WMI / lspci 取得的顯示卡名稱 → GPUInfo（NVIDIA 另由 nvidia-smi 提供 VRAM）。"""
    out = []
    for n in names:
        n = n.strip()
        if not n:
            continue
        low = n.lower()
        if "basic display" in low or "remote display" in low or "virtual" in low:
            continue
        out.append(GPUInfo(vendor_of(n), n, 0))
    return out


def _windows_video_names() -> list[str]:
    ps = shutil.which("powershell") or shutil.which("pwsh")
    if not ps:
        return []
    txt = _run([ps, "-NoProfile", "-Command",
                "Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name }"])
    return txt.splitlines()


def _linux_video_names() -> list[str]:
    txt = _run(["lspci"])
    return [l.split(":", 2)[-1] for l in txt.splitlines()
            if "VGA" in l or "3D controller" in l or "Display controller" in l]


def _mac_video_names() -> list[str]:
    txt = _run(["system_profiler", "SPDisplaysDataType"])
    return [l.split(":", 1)[1] for l in txt.splitlines() if "Chipset Model" in l]


def _ram_gb() -> float:
    try:
        if os.name == "nt":
            import ctypes

            class MEMSTAT(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            st = MEMSTAT()
            st.dwLength = ctypes.sizeof(MEMSTAT)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
            return round(st.ullTotalPhys / 1024**3, 1)
        if sys.platform == "darwin":
            return round(int(_run(["sysctl", "-n", "hw.memsize"]) or 0) / 1024**3, 1)
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal"):
                    return round(int(line.split()[1]) / 1024**2, 1)
    except Exception:
        pass
    return 0.0


def _physical_cores(logical: int) -> int:
    try:
        if os.name == "nt":
            ps = shutil.which("powershell")
            if ps:
                txt = _run([ps, "-NoProfile", "-Command",
                            "(Get-CimInstance Win32_Processor | Measure-Object NumberOfCores -Sum).Sum"])
                if txt.strip().isdigit():
                    return int(txt.strip())
        elif sys.platform == "darwin":
            txt = _run(["sysctl", "-n", "hw.physicalcpu"])
            if txt.strip().isdigit():
                return int(txt.strip())
        else:
            pairs = set()
            phys = core = None
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if line.startswith("physical id"):
                        phys = line.split(":")[1].strip()
                    elif line.startswith("core id"):
                        core = line.split(":")[1].strip()
                        pairs.add((phys, core))
            if pairs:
                return len(pairs)
    except Exception:
        pass
    return max(1, logical // 2)


def _cpu_name() -> str:
    if os.name == "nt":
        ps = shutil.which("powershell")
        if ps:
            txt = _run([ps, "-NoProfile", "-Command", "(Get-CimInstance Win32_Processor).Name"]).strip()
            if txt:
                return txt.splitlines()[0].strip()
    elif sys.platform == "darwin":
        txt = _run(["sysctl", "-n", "machdep.cpu.brand_string"]).strip()
        if txt:
            return txt
    return platform.processor() or platform.machine()


def detect() -> HardwareInfo:
    sysname = {"win32": "windows", "darwin": "darwin"}.get(sys.platform, "linux")
    machine = platform.machine().lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else "x86_64"
    logical = os.cpu_count() or 1

    gpus: list[GPUInfo] = []
    smi = shutil.which("nvidia-smi")
    if smi:
        gpus += parse_nvidia_smi(_run([smi, "--query-gpu=name,memory.total",
                                        "--format=csv,noheader,nounits"]))
    names = {"windows": _windows_video_names, "linux": _linux_video_names,
             "darwin": _mac_video_names}[sysname]()
    for g in parse_video_controllers(names):
        if g.vendor == "nvidia" and any(x.vendor == "nvidia" for x in gpus):
            continue  # 已由 nvidia-smi 提供（含 VRAM）
        gpus.append(g)
    if sysname == "darwin" and arch == "arm64" and not any(g.vendor == "apple" for g in gpus):
        gpus.append(GPUInfo("apple", "Apple Silicon", 0))

    return HardwareInfo(
        os=sysname, arch=arch, cpu_name=_cpu_name(),
        physical_cores=_physical_cores(logical), logical_cores=logical,
        ram_gb=_ram_gb(), gpus=gpus,
    )


def recommended_extra(hw: HardwareInfo) -> str:
    """安裝前決定要裝哪一組額外套件。"""
    if hw.nvidia is not None and hw.os in ("windows", "linux"):
        return "cuda"
    if hw.intel_gpu is not None and hw.os in ("windows", "linux") and hw.arch == "x86_64":
        return "intel"
    return "none"


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    hw = detect()
    if "--print-extra" in argv:
        print(recommended_extra(hw))
    else:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        print(json.dumps(hw.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
