import pytest

from livesub import hardware as hw
from livesub import models, profiles


def _info(gpus=(), ram=16.0, cores=8, os="windows", arch="x86_64"):
    return hw.HardwareInfo(os=os, arch=arch, cpu_name="x", physical_cores=cores, logical_cores=cores * 2,
                           ram_gb=ram, gpus=list(gpus))


def NV(mb):
    return hw.GPUInfo("nvidia", "RTX", mb)


INTEL = hw.GPUInfo("intel", "Intel(R) Arc(TM) Graphics", 0)


@pytest.mark.parametrize("info,ov,expected", [
    (_info([NV(24576)]), None, "nvidia-high"),
    (_info([NV(8188)]), None, "nvidia-high"),
    (_info([NV(7982)]), None, "nvidia-high"),
    (_info([NV(6144)]), None, "nvidia-mid"),
    (_info([NV(4096)]), None, "nvidia-mid"),
    (_info([NV(2048)]), None, "nvidia-low"),
    (_info([INTEL, NV(12288)]), None, "nvidia-high"),
    (_info([INTEL], ram=32), None, "intel-gpu"),
    (_info([INTEL], ram=32), True, "intel-gpu"),
    (_info([INTEL], ram=8), None, "intel-gpu"),
    (_info([INTEL], ram=4), None, "intel-gpu-lite"),
    (_info([INTEL], ram=32), False, "cpu-high"),
    (_info([INTEL], ram=32, arch="arm64"), None, "cpu-high"),
    (_info([hw.GPUInfo("amd", "Radeon", 0)], cores=8, ram=16), None, "cpu-high"),
    (_info([], cores=4, ram=16), None, "cpu-low"),
    (_info([], cores=8, ram=4), None, "cpu-low"),
    (_info([hw.GPUInfo("apple", "M2", 0)], os="darwin", arch="arm64", cores=8), None, "cpu-high"),
    (_info([NV(8192)], os="darwin"), None, "cpu-high"),
])
def test_select_profile_table(info, ov, expected):
    assert profiles.select_profile(info, openvino_gpu=ov).name == expected


def test_threads_leave_headroom():
    assert profiles.select_profile(_info([], cores=16, ram=32)).cpu_threads == 8
    assert profiles.select_profile(_info([], cores=2, ram=8)).cpu_threads == 2


def test_fallback_chains_end_on_cpu_and_terminate():
    for p in profiles.PROFILES.values():
        seen, cur = [], p
        while cur is not None:
            assert cur.name not in seen, "fallback 有循環"
            seen.append(cur.name)
            cur = profiles.downgrade(cur)
        assert seen[-1] == "cpu-low"


def test_all_profile_models_registered():
    for p in profiles.PROFILES.values():
        assert p.asr_model in models.REGISTRY
        kind = models.REGISTRY[p.asr_model].kind
        assert kind == ("asr-openvino" if p.asr_backend == "openvino" else "asr-ct2")
        if p.partial_model:  # 獨立 partial 引擎用同一個後端、同一個裝置
            assert p.partial_model in models.REGISTRY
            assert models.REGISTRY[p.partial_model].kind == kind
            assert p.partial_interval_s > 0


def test_get_unknown():
    with pytest.raises(KeyError):
        profiles.get("gpu-ultra")
