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
    ModelSpec("ov-whisper-base", "OpenVINO/whisper-base-int8-ov", "0606293f0511136ada21755a265492f623a934b8",
              "asr-openvino", "Apache-2.0", "OpenAI", "openai/whisper-base", "Intel OpenVINO（美國）", 85,
              _OV_WHISPER, "邊講邊出字用（速度優先）"),
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

# 選用的 Ollama LLM 翻譯：只接受「權重層 sha256」在下表中的模型。
# 名稱可以被本機 `ollama cp` 任意改，權重 digest 才是內容本身；digest 取自 registry.ollama.ai 的
# manifest（application/vnd.ollama.image.model 層），2026-10-30 抓取。
OLLAMA_WEIGHT_DIGESTS = {
    "2f8c3d137f2496dcb4019e17a50f2974b89c1be1a742db3c80cc86f91cafeeef": "gemma3:1b",
    "f06e91e18fc74295d0c781bf91622508c75655e2c18ed869d52447ffe1e667e1": "gemma3:4b",
    "13576eb6dd8d9d04b4da04d015ff343968628d3c2984195569d9d3585651d4f1": "gemma3:12b",
    "d6138de197b83fafa8ced29005f4b12c47cc4adfca8fbcdf3e4e523774d023bc": "gemma3n:e2b",
    "4e3c80c945d2db3ad542bc133e0465cb59332aa9b7e6376e38ab25782559a599": "gemma3n:e4b",
    "3c168af1dea0a414299c7d9077e100ac763370e5a98b3c53801a958a47f0a5db": "phi4-mini",
    "dde5aa3fc5ffc17176b5e8bdc82f587b24b2678c6c66101bf7da77af9f7ccdff": "llama3.2:3b",
    "667b0c1932bc6ffc593ed1d03f895bf2dc8dc6df21db3042284a6f4416b06a29": "llama3.1:8b",
    "8e17af7e8a471e086f2c78ca99098016eb331049ec266de4df87b5db9bce6989": "ycchen/breeze-7b-instruct-v1_0",
}
OLLAMA_ALLOWLIST = tuple(sorted({v.split(":")[0] for v in OLLAMA_WEIGHT_DIGESTS.values()}))


def is_forbidden(name: str) -> bool:
    n = name.lower()
    return any(bad in n for bad in FORBIDDEN_SOURCES)


def ollama_model_allowed(name: str) -> bool:
    """名稱初篩（設定檔驗證用）；真正放行要再通過 ollama_weights_allowed()。"""
    n = name.lower().strip()
    if is_forbidden(n):
        return False
    return any(n == p or n.startswith(p + ":") for p in OLLAMA_ALLOWLIST)


def ollama_weights_allowed(modelfile: str) -> str | None:
    """從 /api/show 的 modelfile（FROM …/blobs/sha256-<digest>）取出權重 digest，在清單內就回傳對應名稱。"""
    m = re.search(r"^FROM\s+.*?sha256[-:]([0-9a-f]{64})", modelfile, re.M)
    return OLLAMA_WEIGHT_DIGESTS.get(m.group(1)) if m else None


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


MANIFEST = ".manifest.json"


def local_dir(key: str) -> Path:
    """每個 revision 一個資料夾：registry 換 revision 後舊權重不會被誤用。"""
    spec = REGISTRY[key]
    return paths.models_dir() / f"{spec.repo.replace('/', '--')}@{spec.revision[:12]}"


def _sha256(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_manifest(d: Path) -> dict | None:
    import json

    try:
        return json.loads((d / MANIFEST).read_text("utf-8"))
    except (OSError, ValueError):
        return None


def is_downloaded(key: str) -> bool:
    """快速檢查（每次啟動都會跑）：manifest 的 revision 相符，且每個必要檔案存在、大小相符。"""
    spec = REGISTRY[key]
    d = local_dir(key)
    m = _read_manifest(d)
    if not m or m.get("revision") != spec.revision:
        return False
    files = m.get("files", {})
    for f in spec.files:
        p = d / f
        if f not in files or not p.is_file() or p.stat().st_size != files[f]["size"]:
            return False
    return True


def verify(key: str) -> list[str]:
    """完整檢查（setup 時跑）：重算 sha256 與 manifest 比對，回傳損壞的檔名。"""
    d = local_dir(key)
    m = _read_manifest(d) or {}
    files = m.get("files", {})
    return [f for f in REGISTRY[key].files
            if f not in files or not (d / f).is_file() or _sha256(d / f) != files[f]["sha256"]]


def _remote_lfs_hashes(spec: ModelSpec) -> dict[str, str]:
    """Hugging Face 對該 revision 公布的 LFS 檔案 sha256（大檔都是 LFS）。"""
    from huggingface_hub import HfApi

    info = HfApi().model_info(spec.repo, revision=spec.revision, files_metadata=True)
    out = {}
    for sib in info.siblings or []:
        lfs = getattr(sib, "lfs", None)
        sha = getattr(lfs, "sha256", None) if lfs is not None else None
        if sha:
            out[sib.rfilename] = sha
    return out


def download(key: str, progress=None, force: bool = False) -> Path:
    """下載固定 revision 的必要檔案到暫存資料夾；比對官方 sha256、寫入 manifest 後才換上。"""
    import json

    spec = REGISTRY[key]
    if check_policy():
        raise ValueError("模型 registry 不合規：" + "; ".join(check_policy()))
    if not force and is_downloaded(key):
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
    remote = _remote_lfs_hashes(spec)
    files = {}
    for f in spec.files:
        sha = _sha256(tmp / f)
        if f in remote and remote[f] != sha:
            shutil.rmtree(tmp, ignore_errors=True)
            raise RuntimeError(f"{spec.repo}/{f} 雜湊不符（下載損毀或遭竄改）")
        files[f] = {"size": (tmp / f).stat().st_size, "sha256": sha}
    (tmp / MANIFEST).write_text(json.dumps({"repo": spec.repo, "revision": spec.revision, "files": files},
                                           indent=1), "utf-8")
    shutil.rmtree(tmp / ".cache", ignore_errors=True)
    if target.exists():
        shutil.rmtree(target)
    tmp.rename(target)
    return target


def resolve(key: str) -> Path:
    """回傳已下載模型的本機路徑；沒下載就丟錯（執行期不偷偷連網）。"""
    if key not in REGISTRY:
        raise KeyError(f"未登錄的模型：{key}")
    spec = REGISTRY[key]
    if any(is_forbidden(x) for x in (spec.key, spec.repo, spec.origin, spec.base, spec.converter)):
        raise ValueError(f"禁用的模型來源：{spec.repo}")
    if not is_downloaded(key):
        raise FileNotFoundError(f"模型 {key} 尚未下載或不完整，請重新執行 livesub-setup")
    return local_dir(key)
