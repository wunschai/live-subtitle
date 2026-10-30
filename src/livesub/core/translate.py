"""翻譯：quickmt（CTranslate2，預設）與選用的 Ollama LLM。"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Protocol

from .. import models
from . import textproc

log = logging.getLogger(__name__)


class Translator(Protocol):
    name: str

    def translate(self, text: str, src: str, tgt: str, context: list[str] | None = None) -> str: ...


class CT2Translator:
    """quickmt zh-en / en-zh。en→zh 的輸出再轉台灣正體。"""

    def __init__(self, zh_en_path: str | Path, en_zh_path: str | Path, device: str = "cpu",
                 compute_type: str = "int8", threads: int = 2, taiwan_phrases: bool = True):
        import ctranslate2
        import sentencepiece as spm

        self.name = "quickmt"
        self.taiwan_phrases = taiwan_phrases
        from .asr import pick_compute_type

        compute_type = pick_compute_type(device, compute_type)
        self._m = {}
        for key, p in (("zh-en", Path(zh_en_path)), ("en-zh", Path(en_zh_path))):
            tr = ctranslate2.Translator(str(p), device=device, compute_type=compute_type,
                                        inter_threads=1, intra_threads=threads)
            src = spm.SentencePieceProcessor(model_file=str(p / "src.spm.model"))
            tgt = spm.SentencePieceProcessor(model_file=str(p / "tgt.spm.model"))
            self._m[key] = (tr, src, tgt)

    def translate(self, text, src, tgt, context=None) -> str:
        tr, sp_src, sp_tgt = self._m[f"{src}-{tgt}"]
        tokens = sp_src.encode(text, out_type=str)
        res = tr.translate_batch([tokens], beam_size=2, max_decoding_length=min(512, len(tokens) * 3 + 20),
                                 repetition_penalty=1.1, no_repeat_ngram_size=0)
        out = sp_tgt.decode(res[0].hypotheses[0]).strip()
        if tgt == "zh":
            out = textproc.fix_spacing(textproc.to_traditional(out, self.taiwan_phrases))
        return textproc.collapse_repeats(out)


_LANG_NAME = {"zh": "Traditional Chinese (Taiwan)", "en": "English"}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):  # 不跟隨轉址：資料只能留在本機
        return None


# 不經過系統代理、不跟隨轉址
_LOCAL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


def is_loopback_url(url: str) -> bool:
    import ipaddress
    from urllib.parse import urlparse

    try:
        u = urlparse(url)
    except ValueError:
        return False
    if u.scheme not in ("http", "https") or not u.hostname:
        return False
    if u.hostname == "localhost":
        return True
    try:
        return ipaddress.ip_address(u.hostname).is_loopback
    except ValueError:
        return False


def _post_json(url: str, payload: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, json.dumps(payload).encode(), {"Content-Type": "application/json"})
    with _LOCAL_OPENER.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


class OllamaTranslator:
    """本機 Ollama（選用）。只接受 allowlist 內的模型；逾時或失敗由呼叫端退回 CT2。"""

    def __init__(self, model: str, url: str = "http://127.0.0.1:11434", timeout: float = 8.0,
                 taiwan_phrases: bool = True):
        if not models.ollama_model_allowed(model):
            raise ValueError(f"Ollama 模型不在允許清單：{model}")
        if not is_loopback_url(url):
            raise ValueError(f"Ollama 只能連本機（localhost / 127.0.0.1）：{url}")
        self.name = f"ollama:{model}"
        self.model = model
        self.url = url.rstrip("/")
        self.timeout = timeout
        self.taiwan_phrases = taiwan_phrases
        # 名稱可被改，驗證實際權重的 sha256
        info = _post_json(self.url + "/api/show", {"model": model}, timeout=5)
        verified = models.ollama_weights_allowed(info.get("modelfile", ""))
        if verified is None:
            raise ValueError(f"Ollama 模型「{model}」的權重不在已審查清單（可能是改名的其他模型）")

    def _prompt(self, text, src, tgt, context):
        sys_msg = (
            f"You are a live subtitle translator. Translate the user's {_LANG_NAME[src]} line into "
            f"{_LANG_NAME[tgt]}. Output only the translation, one line, no quotes, no notes. "
            "Keep technical terms, product names and acronyms in their original form when that is customary."
        )
        if context:
            sys_msg += "\nPrevious lines for context (do not translate them): " + " / ".join(context[-2:])
        return [{"role": "system", "content": sys_msg}, {"role": "user", "content": text}]

    def translate(self, text, src, tgt, context=None) -> str:
        data = _post_json(self.url + "/api/chat", {
            "model": self.model, "stream": False, "messages": self._prompt(text, src, tgt, context),
            "options": {"temperature": 0.1, "num_predict": 256},
        }, self.timeout)
        out = data.get("message", {}).get("content", "").strip().splitlines()
        out = out[0].strip() if out else ""
        if tgt == "zh":
            out = textproc.fix_spacing(textproc.to_traditional(out, self.taiwan_phrases))
        return out


def ollama_available(url: str = "http://127.0.0.1:11434", timeout: float = 1.0) -> list[str]:
    """回傳 Ollama 已安裝且在 allowlist 內的模型；Ollama 沒跑就回傳空清單。"""
    if not is_loopback_url(url):
        return []
    try:
        with _LOCAL_OPENER.open(url.rstrip("/") + "/api/tags", timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return []
    return [m["name"] for m in data.get("models", []) if models.ollama_model_allowed(m.get("name", ""))]


class FallbackTranslator:
    """先用 primary，失敗就用 backup（例如 Ollama → quickmt）。連續失敗 3 次後 60 秒內直接走 backup。"""

    def __init__(self, primary: Translator, backup: Translator):
        self.primary, self.backup = primary, backup
        self.name = f"{primary.name}→{backup.name}"
        self._fails = 0
        self._skip_until = 0.0

    def translate(self, text, src, tgt, context=None) -> str:
        if time.monotonic() >= self._skip_until:
            try:
                out = self.primary.translate(text, src, tgt, context)
                if out:
                    self._fails = 0
                    return out
            except Exception as e:
                log.warning("主要翻譯器失敗，改用備援：%s", e)
            self._fails += 1
            if self._fails >= 3:
                self._skip_until = time.monotonic() + 60
                self._fails = 0
        return self.backup.translate(text, src, tgt, context)


def build_translator(profile, runtime, taiwan_phrases: bool = True) -> Translator:
    ct2 = CT2Translator(models.resolve("mt-zh-en"), models.resolve("mt-en-zh"), profile.mt_device,
                        profile.mt_compute_type, threads=2, taiwan_phrases=taiwan_phrases)
    if runtime.translator == "ollama":
        try:
            return FallbackTranslator(OllamaTranslator(runtime.ollama_model, runtime.ollama_url,
                                                       taiwan_phrases=taiwan_phrases), ct2)
        except (ValueError, OSError) as e:  # 不合規、連不上、權重不符
            log.warning("%s；改用 quickmt", e)
    return ct2
