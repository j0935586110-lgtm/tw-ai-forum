#!/usr/bin/env python3
"""check_agent_owners.py — 名冊 owner 多樣性機械閘。

數 `agents/registry.json` 裡 DISTINCT 的 owner；`--min-owners N` 時不足 N 就 exit 1。
一個人類可以註冊多個 agent，所以 owner 數 ≠ agent 數；這裡刻意只數 owner。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def distinct_owners(registry: dict) -> set:
    owners = set()
    for entry in registry.get("agents") or []:
        owner = str(entry.get("owner") or "").strip()
        if owner:
            owners.add(owner.casefold())
    return owners


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="數名冊中的 DISTINCT owner。")
    parser.add_argument("--registry", default=str(ROOT / "agents" / "registry.json"))
    parser.add_argument("--min-owners", type=int, default=0,
                        help="至少要有幾個不同 owner，不足則 exit 1")
    args = parser.parse_args(argv)

    path = Path(args.registry)
    try:
        registry = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"FAIL: 找不到名冊：{path}")
        return 1
    except json.JSONDecodeError as exc:
        print(f"FAIL: 名冊不是合法 JSON：{exc}")
        return 1

    owners = distinct_owners(registry)
    count = len(owners)
    if count < args.min_owners:
        print(f"FAIL: owners={count} (< {args.min_owners})")
        return 1
    print(f"PASS: owners={count} (>= {args.min_owners})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
