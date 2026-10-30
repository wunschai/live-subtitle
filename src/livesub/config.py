"""設定：TOML 讀寫，讀入時把每個欄位夾到合法範圍，壞檔不會讓程式起不來。"""

from __future__ import annotations

import logging
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

import tomli_w

from . import paths

log = logging.getLogger(__name__)

DISPLAY_MODES = ("original", "zh", "en", "bilingual")
POSITIONS = ("top", "bottom")
STYLES = ("outline", "box")
SOURCE_LANGS = ("auto", "zh", "en")
TRANSLATORS = ("ct2", "ollama")

FONT_MIN, FONT_MAX = 16, 96


@dataclass
class AudioSettings:
    device: str = ""  # 裝置名稱；空字串 = 系統預設
    source_language: str = "auto"


@dataclass
class DisplaySettings:
    mode: str = "bilingual"
    position: str = "bottom"
    style: str = "box"
    box_opacity: float = 0.6  # 0–1
    font_size: int = 36
    secondary_scale: float = 0.8  # 雙語時第二行字級比例
    max_lines: int = 2  # 每種語言最多幾句
    width_pct: int = 80
    margin_px: int = 40
    screen: int = 0
    clear_after_s: float = 6.0
    font_family: str = ""  # 空字串 = 依系統挑（Microsoft JhengHei / Noto Sans TC …）
    taiwan_phrases: bool = True  # 簡轉繁時連台灣用語一起轉（s2twp）


@dataclass
class RuntimeSettings:
    profile: str = ""  # 安裝時寫入；空 = 執行時自動偵測
    translator: str = "ct2"
    ollama_model: str = "gemma3:4b"
    ollama_url: str = "http://127.0.0.1:11434"
    hotkeys: bool = True


@dataclass
class Settings:
    audio: AudioSettings = field(default_factory=AudioSettings)
    display: DisplaySettings = field(default_factory=DisplaySettings)
    runtime: RuntimeSettings = field(default_factory=RuntimeSettings)

    def normalized(self) -> "Settings":
        a, d, r = self.audio, self.display, self.runtime
        if a.source_language not in SOURCE_LANGS:
            a.source_language = "auto"
        if d.mode not in DISPLAY_MODES:
            d.mode = "bilingual"
        if d.position not in POSITIONS:
            d.position = "bottom"
        if d.style not in STYLES:
            d.style = "box"
        d.box_opacity = _clamp(_num(d.box_opacity, 0.6), 0.0, 1.0)
        d.font_size = int(_clamp(_num(d.font_size, 36), FONT_MIN, FONT_MAX))
        d.secondary_scale = _clamp(_num(d.secondary_scale, 0.8), 0.5, 1.5)
        d.max_lines = int(_clamp(_num(d.max_lines, 2), 1, 3))
        d.width_pct = int(_clamp(_num(d.width_pct, 80), 30, 100))
        d.margin_px = int(_clamp(_num(d.margin_px, 40), 0, 400))
        d.screen = int(_clamp(_num(d.screen, 0), 0, 16))
        d.clear_after_s = _clamp(_num(d.clear_after_s, 6.0), 0.0, 120.0)
        if r.translator not in TRANSLATORS:
            r.translator = "ct2"
        return self

    def to_dict(self) -> dict:
        return asdict(self)


def _num(v, default):
    if isinstance(v, bool):
        return default
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    return x if x == x and abs(x) != float("inf") else default


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def _fill(cls, data: dict):
    obj = cls()
    if not isinstance(data, dict):
        return obj
    for f in fields(cls):
        if f.name in data:
            v = data[f.name]
            cur = getattr(obj, f.name)
            if isinstance(cur, str) and not isinstance(v, str):
                continue
            if isinstance(cur, bool) and not isinstance(v, bool):
                continue
            setattr(obj, f.name, v)
    return obj


def from_dict(data: dict) -> Settings:
    return Settings(
        audio=_fill(AudioSettings, data.get("audio", {})),
        display=_fill(DisplaySettings, data.get("display", {})),
        runtime=_fill(RuntimeSettings, data.get("runtime", {})),
    ).normalized()


def load(path: Path | None = None) -> Settings:
    path = path or paths.config_file()
    try:
        with open(path, "rb") as f:
            return from_dict(tomllib.load(f))
    except FileNotFoundError:
        return Settings()
    except (tomllib.TOMLDecodeError, OSError, UnicodeDecodeError) as e:
        log.warning("設定檔讀取失敗，改用預設值：%s", e)
        return Settings()


def save(s: Settings, path: Path | None = None) -> None:
    path = path or paths.config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "wb") as f:
        tomli_w.dump(s.normalized().to_dict(), f)
    tmp.replace(path)
