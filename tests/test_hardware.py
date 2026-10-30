from livesub import hardware as hw


def test_parse_nvidia_smi():
    gpus = hw.parse_nvidia_smi("NVIDIA GeForce RTX 4070, 12282\nNVIDIA T400, 2048\n\n")
    assert [(g.name, g.vram_mb) for g in gpus] == [("NVIDIA GeForce RTX 4070", 12282), ("NVIDIA T400", 2048)]
    assert all(g.vendor == "nvidia" for g in gpus)


def test_parse_nvidia_smi_garbage():
    assert hw.parse_nvidia_smi("") == []
    assert hw.parse_nvidia_smi("No devices were found") == []


def test_parse_video_controllers_filters_virtual():
    gpus = hw.parse_video_controllers(["Intel(R) Arc(TM) Graphics", "Microsoft Basic Display Adapter",
                                       "AMD Radeon(TM) 780M", "Parsec Virtual Display Adapter", ""])
    assert [(g.vendor, g.name) for g in gpus] == [("intel", "Intel(R) Arc(TM) Graphics"),
                                                  ("amd", "AMD Radeon(TM) 780M")]


def _info(**kw):
    base = dict(os="windows", arch="x86_64", cpu_name="x", physical_cores=8, logical_cores=16, ram_gb=16.0,
                gpus=[])
    base.update(kw)
    return hw.HardwareInfo(**base)


def test_recommended_extra():
    assert hw.recommended_extra(_info(gpus=[hw.GPUInfo("nvidia", "RTX", 8192)])) == "cuda"
    assert hw.recommended_extra(_info(gpus=[hw.GPUInfo("intel", "Arc", 0)])) == "intel"
    assert hw.recommended_extra(_info(gpus=[hw.GPUInfo("amd", "Radeon", 0)])) == "none"
    assert hw.recommended_extra(_info(os="darwin", arch="arm64", gpus=[hw.GPUInfo("apple", "M2", 0)])) == "none"
    # NVIDIA 優先於內顯
    assert hw.recommended_extra(
        _info(gpus=[hw.GPUInfo("intel", "UHD", 0), hw.GPUInfo("nvidia", "RTX", 6144)])) == "cuda"


def test_roundtrip_dict():
    i = _info(gpus=[hw.GPUInfo("intel", "Arc", 0)])
    assert hw.HardwareInfo.from_dict(i.to_dict()) == i


def test_detect_runs_on_this_machine():
    i = hw.detect()
    assert i.logical_cores >= 1 and i.physical_cores >= 1
    assert i.os in ("windows", "linux", "darwin")
