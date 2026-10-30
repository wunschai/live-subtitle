"""模型 registry：專案能用的模型都列在這裡（allowlist），其餘一律不載入。

硬性規定：不使用中國政府／中國資本來源的模型。每個模型都要固定 revision、
列出必要檔案，並在 docs/MODELS.md 留下來源審查紀錄。`FORBIDDEN_SOURCES`
只是第二道防線：由 `check_policy()` 與測試再檢查一次名稱。
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from . import paths

# 名稱、repo、來源、基底、轉檔者中出現以下字串者一律禁用（不分大小寫）
FORBIDDEN_SOURCES = (
    "qwen", "alibaba", "damo", "modelscope", "funasr", "funaudiollm", "sensevoice", "iic/",
    "deepseek", "thudm", "zhipu", "chatglm", "glm-", "zai-org", "01-ai", "yi-", "internlm",
    "internvl", "shanghai", "baichuan", "openbmb", "minicpm", "baidu", "ernie", "tencent",
    "hunyuan", "bytedance", "doubao", "moonshot", "kimi", "stepfun", "minimax", "sensetime",
    "iflytek", "tea-asr", "llama-breeze2", "breeze2",
)


@dataclass(frozen=True)
class ModelSpec:
    key: str
    repo: str
    revision: str  # 固定的 commit hash；不接受 "main"
    kind: str  # "asr-ct2" / "asr-openvino" / "mt-ct2"
    license: str
    origin: str  # 原始權重的開發者
    base: str  # 基底模型
    converter: str  # 轉檔／上傳者
    size_mb: int
    files: tuple[str, ...]  # 必要檔案；下載後逐一驗證
    note: str = ""


_CT2_WHISPER = ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt")
_CT2_WHISPER_V3 = ("config.json", "model.bin", "preprocessor_config.json", "tokenizer.json", "vocabulary.json")
_OV_WHISPER = (
    "added_tokens.json", "config.json", "generation_config.json", "merges.txt", "normalizer.json",
    "openvino_config.json", "openvino_decoder_model.bin", "openvino_decoder_model.xml",
    "openvino_detokenizer.bin", "openvino_detokenizer.xml", "openvino_encoder_model.bin",
    "openvino_encoder_model.xml", "openvino_tokenizer.bin", "openvino_tokenizer.xml",
    "preprocessor_config.json", "special_tokens_map.json", "tokenizer.json", "tokenizer_config.json",
    "vocab.json",
)
_QUICKMT = ("config.json", "model.bin", "source_vocabulary.json", "target_vocabulary.json",
            "src.spm.model", "tgt.spm.model")

# 來源審查紀錄見 docs/MODELS.md；新增模型必須先在那裡留下證據
REGISTRY: dict[str, ModelSpec] = {m.key: m for m in [
    ModelSpec("whisper-tiny", "Systran/faster-whisper-tiny", "d90ca5fe260221311c53c58e660288d3deb8d356",
              "asr-ct2", "MIT", "OpenAI", "openai/whisper-tiny", "SYSTRAN（法國）", 78, _CT2_WHISPER),
    ModelSpec("whisper-base", "Systran/faster-whisper-base", "ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66",
              "asr-ct2", "MIT", "OpenAI", "openai/whisper-base", "SYSTRAN（法國）", 148, _CT2_WHISPER),
    ModelSpec("whisper-small", "Systran/faster-whisper-small", "536b0662742c02347bc0e980a01041f333bce120",
              "asr-ct2", "MIT", "OpenAI", "openai/whisper-small", "SYSTRAN（法國）", 486, _CT2_WHISPER),
    ModelSpec("whisper-large-v3-turbo", "dropbox-dash/faster-whisper-large-v3-turbo",
              "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf", "asr-ct2", "MIT", "OpenAI",
              "openai/whisper-large-v3-turbo", "Mobius Labs（德國，現屬 Dropbox）", 1622, _CT2_WHISPER_V3),
    ModelSpec("breeze-asr-25", "phate334/Breeze-ASR-25-ct2", "ac4ec98cf46e00218e29e704b3181a2d7bd94f77",
              "asr-ct2", "Apache-2.0", "MediaTek Research（台灣）", "openai/whisper-large-v2",
              "phate334（台灣）", 3092, _CT2_WHISPER_V3, "繁中與中英夾雜強化"),
    ModelSpec("ov-whisper-small", "OpenVINO/whisper-small-int8-ov", "5b831719e093f86e1970be663e524fe001489f9b",
              "asr-openvino", "Apache-2.0", "OpenAI", "openai/whisper-small", "Intel OpenVINO（美國）", 257,
              _OV_WHISPER),
    ModelSpec("ov-whisper-large-v3-turbo", "OpenVINO/whisper-large-v3-turbo-int8-ov",
              "b568445dd5dc8c695bde596f8acbb4694fd6ba64", "asr-openvino", "Apache-2.0", "OpenAI",
              "openai/whisper-large-v3-turbo", "Intel OpenVINO（美國）", 828, _OV_WHISPER),
    ModelSpec("mt-zh-en", "quickmt/quickmt-zh-en", "c27cc8024e01a047733a1e34796e2ab19d74b237", "mt-ct2",
              "CC-BY-4.0", "quickmt（Mark Franey，加拿大）", "自行訓練", "quickmt", 410, _QUICKMT, "需標示出處"),
    ModelSpec("mt-en-zh", "quickmt/quickmt-en-zh", "f48b69e53866b717607b59f94350e9aae3ed021c", "mt-ct2",
              "CC-BY-4.0", "quickmt（Mark Franey，加拿大）", "自行訓練", "quickmt", 410, _QUICKMT, "需標示出處"),
]}

# 選用的 Ollama LLM 翻譯模型（只允許這些前綴）
OLLAMA_ALLOWLIST = (
    "gemma3", "gemma3n", "gemma4", "llama3.1", "llama3.2", "phi4-mini", "phi4",
    "mistral", "ministral", "ycchen/breeze-7b-instruct", "jcai/breeze-7b-instruct",
)


def is_forbidden(name: str) -> bool:
    n = name.lower()
    return any(bad in n for bad in FORBIDDEN_SOURCES)


def ollama_model_allowed(name: str) -> bool:
    n = name.lower().strip()
    if is_forbidden(n):
        return False
    return any(n == p or n.startswith((p + ":", p + "-", p + "_")) for p in OLLAMA_ALLOWLIST)


def check_policy() -> list[str]:
    """回傳違規項目（空清單＝合規）。"""
    bad = []
    for m in REGISTRY.values():
        if any(is_forbidden(x) for x in (m.key, m.repo, m.origin, m.base, m.converter)):
            bad.append(f"{m.key}: 來源在禁用名單")
        if not re.fullmatch(r"[0-9a-f]{40}", m.revision):
            bad.append(f"{m.key}: revision 未固定")
        if not m.files:
            bad.append(f"{m.key}: 未列必要檔案")
    return bad


def local_dir(key: str) -> Path:
    spec = REGISTRY[key]
    return paths.models_dir() / spec.repo.replace("/", "--")


def is_downloaded(key: str) -> bool:
    d = local_dir(key)
    return d.is_dir() and all((d / f).is_file() and (d / f).stat().st_size > 0 for f in REGISTRY[key].files)


def download(key: str, progress=None) -> Path:
    """下載固定 revision 的必要檔案到暫存資料夾，驗證齊全後才換上（之後以本機路徑載入，可離線）。"""
    spec = REGISTRY[key]
    if check_policy():
        raise ValueError("模型 registry 不合規：" + "; ".join(check_policy()))
    if is_downloaded(key):
        return local_dir(key)
    from huggingface_hub import snapshot_download

    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    target = local_dir(key)
    tmp = target.with_name(target.name + ".partial")
    tmp.mkdir(parents=True, exist_ok=True)
    if progress:
        progress(f"下載 {spec.repo}（約 {spec.size_mb} MB）…")
    snapshot_download(spec.repo, revision=spec.revision, local_dir=str(tmp),
                      allow_patterns=list(spec.files) + ["README.md"])
    missing = [f for f in spec.files if not (tmp / f).is_file()]
    if missing:
        raise RuntimeError(f"{spec.repo} 下載不完整，缺少：{', '.join(missing)}")
    shutil.rmtree(tmp / ".cache", ignore_errors=True)
    if target.exists():
        shutil.rmtree(target)
    tmp.rename(target)
    return target


def resolve(key: str) -> Path:
    """回傳已下載模型的本機路徑；沒下載就丟錯（執行期不偷偷連網）。"""
    if key not in REGISTRY:
        raise KeyError(f"未登錄的模型：{key}")
    if not is_downloaded(key):
        raise FileNotFoundError(f"模型 {key} 尚未下載，請先執行 livesub-setup")
    return local_dir(key)
