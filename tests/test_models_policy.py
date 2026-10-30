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


def _fake_install(key, content=b"x"):
    import json

    d = models.local_dir(key)
    d.mkdir(parents=True)
    files = {}
    for f in models.REGISTRY[key].files:
        (d / f).write_bytes(content)
        files[f] = {"size": len(content), "sha256": models._sha256(d / f)}
    (d / models.MANIFEST).write_text(json.dumps({"revision": models.REGISTRY[key].revision, "files": files}))
    return d


def test_is_downloaded_requires_manifest_and_sizes():
    d = _fake_install("whisper-base")
    assert models.is_downloaded("whisper-base")
    (d / "model.bin").write_bytes(b"xx")  # 大小變了
    assert not models.is_downloaded("whisper-base")


def test_missing_file_not_downloaded():
    d = _fake_install("whisper-base")
    (d / "tokenizer.json").unlink()
    assert not models.is_downloaded("whisper-base")


def test_revision_change_invalidates(monkeypatch):
    _fake_install("whisper-base")
    m = models.REGISTRY["whisper-base"]
    monkeypatch.setitem(models.REGISTRY, "whisper-base", dataclasses.replace(m, revision="b" * 40))
    assert not models.is_downloaded("whisper-base")  # 新 revision → 新資料夾


def test_verify_detects_same_size_corruption():
    d = _fake_install("whisper-base", b"abc")
    assert models.verify("whisper-base") == []
    (d / "model.bin").write_bytes(b"abd")  # 大小相同、內容損毀
    assert models.is_downloaded("whisper-base")  # 快速檢查抓不到
    assert models.verify("whisper-base") == ["model.bin"]  # 完整檢查抓得到
