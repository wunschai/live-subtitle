"""字幕板：字幕行的狀態與「要顯示什麼」的純邏輯。

只由 pipeline 的 coordinator 執行緒操作；UI 只拿到不可變的 BoardSnapshot。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from . import textproc

PENDING_MARK = "…"


@dataclass
class CaptionLine:
    id: int
    utt_id: int
    raw: str  # ASR 原始輸出（後處理前）
    original: str  # 後處理後的原文
    lang: str  # 原文語言 "zh" / "en"
    composition: str  # "zh" / "en" / "mixed"
    zh: str | None = None  # 中文版（原文是中文就等於 original）
    en: str | None = None
    translation_failed: bool = False
    ts: float = 0.0


@dataclass(frozen=True)
class Span:
    text: str
    dim: bool = False  # partial 或等待譯文中


@dataclass(frozen=True)
class Block:
    lang: str  # "zh" / "en" / "orig"
    spans: tuple[Span, ...]

    @property
    def text(self) -> str:
        return "".join(s.text for s in self.spans)


@dataclass(frozen=True)
class BoardSnapshot:
    blocks: tuple[Block, ...]
    visible: bool  # False = 該淡出（太久沒有新內容）
    revision: int

    @property
    def empty(self) -> bool:
        return not any(b.text.strip() for b in self.blocks)


EMPTY_SNAPSHOT = BoardSnapshot(blocks=(), visible=False, revision=0)


@dataclass
class _Partial:
    utt_id: int
    text: str
    lang: str


def _joiner(lang: str) -> str:
    return "" if lang == "zh" else " "


class CaptionBoard:
    """保留最近幾句 final，加上目前的一句 partial。"""

    def __init__(self, keep_lines: int = 6, clock=time.monotonic):
        self.keep_lines = keep_lines
        self._clock = clock
        self.lines: list[CaptionLine] = []
        self.partial: _Partial | None = None
        self._next_id = 1
        self._finalized_utts: set[int] = set()
        self._max_final_utt = 0
        self.last_activity = clock()
        self.revision = 0

    # ---- 修改 ---------------------------------------------------------
    def _touch(self) -> None:
        self.last_activity = self._clock()
        self.revision += 1

    def set_partial(self, utt_id: int, text: str, lang: str) -> bool:
        """回傳是否被接受（已 final 或更舊的 utt 一律拒收）。"""
        if utt_id in self._finalized_utts or utt_id <= self._max_final_utt:
            return False
        text = text.strip()
        if not text:
            return False
        self.partial = _Partial(utt_id, text, lang)
        self._touch()
        return True

    def cancel_partial(self, utt_id: int) -> None:
        if self.partial and self.partial.utt_id == utt_id:
            self.partial = None
            self._touch()
        self._finalized_utts.add(utt_id)
        self._max_final_utt = max(self._max_final_utt, utt_id)

    def add_final(self, utt_id: int, raw: str, text: str, lang: str) -> CaptionLine | None:
        """加入一句確認的字幕；text 為空表示這段被丟棄（只清掉 partial）。"""
        if self.partial and self.partial.utt_id <= utt_id:
            self.partial = None
        self._finalized_utts.add(utt_id)
        self._max_final_utt = max(self._max_final_utt, utt_id)
        if len(self._finalized_utts) > 256:
            self._finalized_utts = {u for u in self._finalized_utts if u > self._max_final_utt - 64}
        if not text.strip():
            self._touch()
            return None
        comp = composition(text)
        line = CaptionLine(
            id=self._next_id, utt_id=utt_id, raw=raw, original=text, lang=lang, composition=comp,
            zh=text if lang == "zh" else None, en=text if lang == "en" else None, ts=self._clock(),
        )
        self._next_id += 1
        self.lines.append(line)
        if len(self.lines) > self.keep_lines:
            del self.lines[: len(self.lines) - self.keep_lines]
        self._touch()
        return line

    def set_translation(self, line_id: int, lang: str, text: str | None) -> bool:
        """回填譯文；行已被淘汰就回傳 False。text=None 表示翻譯失敗。"""
        line = self.get(line_id)
        if line is None:
            return False
        if text is None:
            line.translation_failed = True
        elif lang == "zh":
            line.zh = text
        else:
            line.en = text
        self._touch()
        return True

    def get(self, line_id: int) -> CaptionLine | None:
        return next((l for l in self.lines if l.id == line_id), None)

    def clear(self) -> None:
        self.lines.clear()
        self.partial = None
        self._touch()

    # ---- 顯示 ---------------------------------------------------------
    def snapshot(self, mode: str, clear_after_s: float = 0.0) -> BoardSnapshot:
        if mode == "bilingual":
            blocks = (self._block("zh", single=False), self._block("en", single=False))
        elif mode in ("zh", "en"):
            blocks = (self._block(mode, single=True),)
        else:
            blocks = (self._block("orig", single=True),)
        idle = self._clock() - self.last_activity
        visible = not (clear_after_s > 0 and idle >= clear_after_s and self.partial is None)
        return BoardSnapshot(blocks=blocks, visible=visible, revision=self.revision)

    @staticmethod
    def _line_span(line: CaptionLine, target: str, single: bool) -> Span:
        if target == "orig":
            return Span(line.original)
        have = line.zh if target == "zh" else line.en
        if have is not None:
            return Span(have)
        if single or line.translation_failed:
            # 單語模式等譯文、或翻譯失敗：以淡色原文暫代，不留空白
            return Span(line.original, dim=True)
        return Span(PENDING_MARK, dim=True)  # 雙語模式另一行已有原文

    def _block(self, target: str, single: bool) -> Block:
        spans: list[Span] = []

        def add(span: Span, lang: str) -> None:
            if spans:
                spans.append(Span(_joiner(lang), dim=span.dim))
            spans.append(span)

        for line in self.lines:
            add(self._line_span(line, target, single), line.lang if target == "orig" else target)
        p = self.partial
        # 單語模式：任何語言的 partial 都先淡色顯示；雙語模式：只出現在自己語言那一行
        if p is not None and (single or target == p.lang):
            add(Span(p.text, dim=True), p.lang)
        return Block(target, tuple(spans))


def composition(text: str) -> str:
    """文字組成：zh（幾乎全中文）、en（幾乎全英文）、mixed（中英夾雜）。"""
    r = textproc.cjk_ratio(text)
    if r >= 0.85:
        return "zh"
    if r < 0.15:
        return "en"
    return "mixed"
