"""辨識結果的文字後處理：簡轉繁、幻覺過濾、重複截斷、語言判斷。"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from functools import lru_cache

# Whisper 在靜音／雜音上常見的幻覺句（整段相似才丟，避免誤殺正常句子）
# 一律丟：幾乎不可能是講者真的說的
HALLUCINATIONS = [
    "謝謝觀看", "謝謝收看", "感謝觀看", "謝謝大家觀看",
    "請不吝點贊訂閱轉發打賞支持明鏡與點點欄目", "請不吝點贊訂閱轉發打賞", "點贊訂閱", "訂閱我的頻道",
    "字幕由Amara.org社區提供", "字幕由Amara.org社群提供", "字幕製作", "字幕志願者", "中文字幕",
    "明鏡與點點欄目", "優優獨播劇場", "本字幕由", "by索蘭婭",
    "thanks for watching", "thanks for watching!", "thank you for watching",
    "thank you for watching.", "please subscribe", "subscribe to my channel",
    "[blank_audio]", "(upbeat music)", "[music]", "(music)", "♪",
]
# 只有在辨識信心低時才丟：講者可能真的會說
WEAK_HALLUCINATIONS = [
    "謝謝", "謝謝你", "謝謝大家", "嗯", "啊", "thank you", "thank you.", "bye", "bye-bye", "you",
]

ZH_RATIO = 0.4

_PUNCT_RE = re.compile(r"[\s\W_]+", re.UNICODE)
_BRACKET_RE = re.compile(r"^\s*[\[(（【].{0,30}[\])）】]\s*$")


@lru_cache(maxsize=2)
def _opencc(config: str):
    try:
        from opencc import OpenCC

        return OpenCC(config)
    except Exception:  # pragma: no cover - 套件缺失時不轉換
        return None


def to_traditional(text: str, taiwan_phrases: bool = True) -> str:
    """簡體 → 台灣正體。taiwan_phrases=True 時連用語一起轉（s2twp，例如 软件→軟體）。"""
    cc = _opencc("s2twp" if taiwan_phrases else "s2tw")
    return cc.convert(text) if cc and text else text


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).lower()
    return _PUNCT_RE.sub("", s)


_HALLU_NORM = sorted({_norm(to_traditional(h)) for h in HALLUCINATIONS} - {""})
_WEAK_NORM = sorted({_norm(to_traditional(h)) for h in WEAK_HALLUCINATIONS} - {""})


def _similar(n: str, cands: list[str], threshold: float) -> bool:
    for h in cands:
        # 長度差太多就不可能相似，省掉計算
        if abs(len(h) - len(n)) > max(len(h), len(n)) * (1 - threshold) + 1:
            continue
        if n == h or SequenceMatcher(None, n, h).ratio() >= threshold:
            return True
    return False


def is_hallucination(text: str, confident: bool = True, threshold: float = 0.85) -> bool:
    t = text.strip()
    if not t:
        return True
    if _BRACKET_RE.match(t):  # [音樂]、(掌聲) 之類整段括號
        return True
    n = _norm(to_traditional(t))
    if not n:
        return True
    if _similar(n, _HALLU_NORM, threshold):
        return True
    return not confident and _similar(n, _WEAK_NORM, threshold)


def collapse_repeats(text: str) -> str:
    """截斷解碼迴圈：同一字元或 2–8 字的片段連續出現 ≥4 次只留 1 次（3 次以內視為正常強調）。"""
    t = re.sub(r"(.)\1{3,}", r"\1", text)
    t = re.sub(r"(.{2,8}?)(?:\s*\1){3,}", r"\1", t)
    # 英文以單字為單位：the the the the → the
    t = re.sub(r"\b((?:\w+\W+){1,4}?)(?:\1){3,}", r"\1", t + " ").rstrip()
    return t


def cjk_ratio(text: str) -> float:
    chars = [c for c in text if not c.isspace() and unicodedata.category(c)[0] in "LN"]
    if not chars:
        return 0.0
    # 以「詞」為單位比較：中文平均約 1.7 字一詞，英文以單字計
    cjk_words = sum(1 for c in chars if _is_cjk(c)) / 1.7
    latin_words = len(re.findall(r"[A-Za-z]+", text))
    denom = cjk_words + latin_words
    return cjk_words / denom if denom else 0.0


def _is_cjk(c: str) -> bool:
    o = ord(c)
    return (0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF or 0xF900 <= o <= 0xFAFF
            or 0x20000 <= o <= 0x2FA1F or 0x3040 <= o <= 0x30FF)


def detect_lang(text: str, hint: str | None = None) -> str:
    """回傳 "zh" 或 "en"。以詞計的 CJK 佔比 ≥ 40% 視為中文（中文夾英文術語仍算中文）。"""
    if not text.strip():
        return hint if hint in ("zh", "en") else "zh"
    return "zh" if cjk_ratio(text) >= ZH_RATIO else "en"


def fix_spacing(text: str) -> str:
    t = text.strip()
    t = re.sub(r"\s+", " ", t)
    # Whisper 中文常用半形逗號句號，換成全形
    t = re.sub(r"(?<=[一-鿿]),\s*", "，", t)
    t = re.sub(r"(?<=[一-鿿])\?\s*", "？", t)
    t = re.sub(r"(?<=[一-鿿])!\s*", "！", t)
    t = re.sub(r"(?<=[一-鿿])\.(?!\d)\s*", "。", t)
    return t


def clean(text: str, confident: bool = True, taiwan_phrases: bool = True) -> str:
    """完整後處理；回傳空字串代表應丟棄。confident=False 時連弱幻覺句也丟。"""
    t = fix_spacing(text)
    if not t:
        return ""
    t = collapse_repeats(t)
    if cjk_ratio(t) > 0:
        t = to_traditional(t, taiwan_phrases)
    if is_hallucination(t, confident=confident):
        return ""
    return t
