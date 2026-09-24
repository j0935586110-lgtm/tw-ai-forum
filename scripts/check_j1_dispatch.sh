#!/usr/bin/env bash
# check_j1_dispatch.sh — j1 派工介面機械閘（F8，印 PASS/FAIL）
#
# PASS 條件：存在一個「只做 dry-run、不真的派工」的 j1 派工介面。
#
# 契約（任一位置成立即可）：
#   worker/guild_bridge/j1_dispatch.py
#   scripts/j1_dispatch.py
#   scripts/j1_dispatch.sh
#   scripts/j1-dispatch.sh
# 介面必須有 dry-run 開關（--dry-run / dry_run / DRY_RUN），
# 且不得含真實派工痕跡（ssh / scp / paramiko / j1ctl exec / controller exec）。
#
# 注意：真實的 j1 控制入口（~/.hermes/scripts/j1ctl.sh）會真的在客戶機執行命令，
# 不算 dry-run 介面。F8 尚未實作前，這個閘門的正確結果就是 FAIL。
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

candidates=(
  "$ROOT/worker/guild_bridge/j1_dispatch.py"
  "$ROOT/scripts/j1_dispatch.py"
  "$ROOT/scripts/j1_dispatch.sh"
  "$ROOT/scripts/j1-dispatch.sh"
)

found=""
for file in "${candidates[@]}"; do
  if [ -f "$file" ]; then
    found="$file"
    break
  fi
done

if [ -z "$found" ]; then
  echo "FAIL: 找不到 j1 dry-run 派工介面（F8 未實作）"
  echo "      預期位置之一：${candidates[*]}"
  exit 1
fi

if ! grep -qE -- "--dry-run|dry_run|DRY_RUN" "$found"; then
  echo "FAIL: $found 沒有 dry-run 開關"
  exit 1
fi

if grep -qE -- "paramiko|(^|[^a-z])ssh[[:space:]]|(^|[^a-z])scp[[:space:]]|j1ctl([.]sh)?[[:space:]]+exec|controller(-linux)?[[:space:]]+exec" "$found"; then
  echo "FAIL: $found 含真實派工痕跡（dry-run 介面不得真的派工）"
  exit 1
fi

echo "PASS: 找到 dry-run 派工介面且無真實派工：$found"
exit 0
