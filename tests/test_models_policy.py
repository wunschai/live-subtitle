import dataclasses

import pytest

from livesub import models


def test_registry_is_compliant():
    assert models.check_policy() == []


@pytest.mark.parametrize("name", [
    "Qwen/Qwen3-ASR", "FunAudioLLM/SenseVoiceSmall", "deepseek-ai/DeepSeek-V3", "THUDM/glm-4",
    "01-ai/Yi-1.5", "internlm/internlm2", "baichuan-inc/Baichuan2", "openbmb/MiniCPM", "TEA-ASR-1.1",
    "MediaTek-Research/Llama-Breeze2-8B-Instruct", "iic/SenseVoice", "qwen2.5:7b",
])
def test_forbidden_names(name):
    assert models.is_forbidden(name)


@pytest.mark.parametrize("name", ["openai/whisper-large-v3", "MediaTek-Research/Breeze-ASR-25",
                                  "quickmt/quickmt-zh-en", "gemma3:4b", "OpenVINO/whisper-small-int8-ov"])
def test_allowed_names(name):
    assert not models.is_forbidden(name)


def test_injected_bad_model_is_caught(monkeypatch):
    bad = models.ModelSpec("x", "Qwen/Qwen2-Audio", "a" * 40, "asr-ct2", "Apache", "Alibaba", "Qwen", "x", 1,
                           ("model.bin",))
    monkeypatch.setitem(models.REGISTRY, "x", bad)
    assert models.check_policy()
    with pytest.raises(ValueError):
        models.download("x")


def test_unpinned_revision_is_caught(monkeypatch):
    m = models.REGISTRY["whisper-base"]
    monkeypatch.setitem(models.REGISTRY, "whisper-base", dataclasses.replace(m, revision="main"))
    assert any("revision" in x for x in models.check_policy())


@pytest.mark.parametrize("name,ok", [
    ("gemma3:4b", True), ("gemma3", True), ("phi4-mini", True), ("ycchen/breeze-7b-instruct-v1_0", True),
    ("qwen3:8b", False), ("deepseek-r1:7b", False), ("llava:7b", False), ("gemma3-evil-qwen", False),
])
def test_ollama_allowlist(name, ok):
    assert models.ollama_model_allowed(name) is ok


def test_resolve_requires_download():
    with pytest.raises(FileNotFoundError):
        models.resolve("whisper-base")
    with pytest.raises(KeyError):
        models.resolve("nope")


def test_is_downloaded_checks_every_file():
    d = models.local_dir("whisper-base")
    d.mkdir(parents=True)
    files = models.REGISTRY["whisper-base"].files
    for f in files[:-1]:
        (d / f).write_bytes(b"x")
    assert not models.is_downloaded("whisper-base")
    (d / files[-1]).write_bytes(b"x")
    assert models.is_downloaded("whisper-base")
