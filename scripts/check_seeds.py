#!/usr/bin/env python3
"""check_seeds.py — 種子文章離線閘（純本機，不連網）

規則：drafts/ 內必須剛好有 10 個 .md 檔，且每個檔都具備
  1) 一行 title:（YAML frontmatter 標題）
  2) 一個 ## 來源 區段

通過時印出 PASS；不通過時印出 FAIL 與原因，並以非零狀態結束。
本程式不進行任何網路呼叫。
"""

from __future__ import annotations

import sys
from pathlib import Path

EXPECTED_COUNT = 10


def collect_drafts(drafts_dir: Path) -> list[Path]:
    return sorted(p for p in drafts_dir.glob("*.md") if p.is_file())


def check_file(path: Path) -> list[str]:
    """回傳這個檔的錯誤清單（空清單＝通過）。"""
    errors: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:  # 讀不到檔本身就是一種失敗
        return [f"無法讀取：{exc}"]

    has_title = any(line.strip().startswith("title:") for line in text.splitlines())
    if not has_title:
        errors.append("缺少 title: 這一行")

    has_source = any(line.strip() == "## 來源" for line in text.splitlines())
    if not has_source:
        errors.append("缺少 ## 來源 區段")

    return errors


def main(argv: list[str]) -> int:
    # 預設：<repo>/drafts；可用命令列第一個參數覆寫（方便測試）
    if len(argv) > 1:
        drafts_dir = Path(argv[1]).resolve()
    else:
        drafts_dir = Path(__file__).resolve().parent.parent / "drafts"

    if not drafts_dir.is_dir():
        print(f"FAIL 找不到 drafts 目錄：{drafts_dir}")
        return 1

    files = collect_drafts(drafts_dir)
    problems: list[str] = []

    if len(files) != EXPECTED_COUNT:
        problems.append(f"檔案數不是 {EXPECTED_COUNT}，實際為 {len(files)}")

    for path in files:
        for err in check_file(path):
            problems.append(f"{path.name}：{err}")

    if problems:
        print("FAIL")
        for p in problems:
            print(f"  - {p}")
        return 1

    print("PASS")
    print(f"  drafts 目錄：{drafts_dir}")
    print(f"  通過檔案數：{len(files)}")
    for path in files:
        print(f"  - {path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
