import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch, request):
    # slow 測試要用真的模型資料夾，其他測試一律隔離
    if "slow" not in request.keywords:
        monkeypatch.setenv("LIVESUB_HOME", str(tmp_path / "home"))
