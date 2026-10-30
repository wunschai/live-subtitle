"""python -m livesub [--source file.wav] [--profile NAME] [--config PATH] [--start]"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path


def _setup_logging(verbose: bool) -> None:
    from . import paths

    log_dir = paths.data_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [logging.FileHandler(log_dir / "livesub.log", encoding="utf-8")]
    if sys.stderr is not None:  # pythonw 沒有 stderr
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    os.environ.setdefault("HF_HUB_OFFLINE", "1")  # 執行期不連網，模型都在本機
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

    ap = argparse.ArgumentParser(prog="livesub", description="即時語音字幕與中英翻譯")
    ap.add_argument("--source", help="用 WAV 檔代替麥克風（示範／測試）")
    ap.add_argument("--profile", help="強制使用某個硬體等級（例如 cpu-high）")
    ap.add_argument("--config", type=Path, help="設定檔路徑")
    ap.add_argument("--start", action="store_true", help="啟動後立即開始收音")
    ap.add_argument("--screenshot", type=Path, help="（測試用）N 秒後截取字幕所在螢幕")
    ap.add_argument("--screenshot-after", type=float, default=8.0)
    ap.add_argument("--quit-after", type=float, help="（測試用）N 秒後自動結束")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    _setup_logging(args.verbose)
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from . import config, profiles
    from .ui.app import LiveSubApp

    settings = config.load(args.config)
    try:
        profile = profiles.resolve(settings.runtime, args.profile)
    except KeyError as e:
        print(e, file=sys.stderr)
        return 2

    qapp = QApplication(sys.argv[:1])
    qapp.setApplicationName("即時字幕")
    qapp.setQuitOnLastWindowClosed(False)
    app = LiveSubApp(qapp, settings, profile, args.config, args.source)
    app.show()
    if args.start or args.source:
        QTimer.singleShot(300, app.start)
    if args.screenshot:
        def shot():
            scr = app.overlay.screen() or qapp.primaryScreen()
            scr.grabWindow(0).save(str(args.screenshot))
            logging.getLogger("livesub").info("截圖：%s", args.screenshot)
        QTimer.singleShot(int(args.screenshot_after * 1000), shot)
    if args.quit_after:
        QTimer.singleShot(int(args.quit_after * 1000), qapp.quit)
    return qapp.exec()


if __name__ == "__main__":
    raise SystemExit(main())
