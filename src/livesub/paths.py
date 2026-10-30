"""App 資料夾。可用環境變數 LIVESUB_HOME 整個改到別處（測試、可攜版）。"""

from __future__ import annotations

import os
from pathlib import Path

APP = "live-subtitle"


def _home() -> Path | None:
    v = os.environ.get("LIVESUB_HOME")
    return Path(v) if v else None


def config_dir() -> Path:
    h = _home()
    if h:
        return h
    from platformdirs import user_config_dir

    return Path(user_config_dir(APP, appauthor=False, roaming=True))


def data_dir() -> Path:
    h = _home()
    if h:
        return h
    from platformdirs import user_data_dir

    return Path(user_data_dir(APP, appauthor=False))


def models_dir() -> Path:
    return data_dir() / "models"


def cache_dir() -> Path:
    return data_dir() / "cache"


def config_file() -> Path:
    return config_dir() / "config.toml"
