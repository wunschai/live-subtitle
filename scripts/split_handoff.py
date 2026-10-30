"""把 codex 交接檔（=====FILE: 路徑 / 內容 / =====END）拆成檔案。

只允許寫入白名單路徑，避免交接內容覆蓋核心程式碼：
    python scripts/split_handoff.py docs/handoffs/ui-handoff.txt
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ALLOWED = re.compile(r"^(src/livesub/ui/[\w]+\.py|tests/test_ui_[\w]+\.py|docs/reviews/[\w.-]+\.md)$")
ROOT = Path(__file__).resolve().parent.parent


def main(path: str) -> int:
    text = Path(path).read_text(encoding="utf-8")
    n = 0
    for m in re.finditer(r"=====FILE:\s*(\S+)\s*\r?\n(.*?)\r?\n=====END", text, re.S):
        rel = m.group(1).replace("\\", "/")
        if not ALLOWED.match(rel):
            print("skip", rel)
            continue
        out = ROOT / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(m.group(2).rstrip() + "\n", encoding="utf-8", newline="\n")
        print("wrote", rel, len(m.group(2)))
        n += 1
    print("files", n)
    return 0 if n else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
