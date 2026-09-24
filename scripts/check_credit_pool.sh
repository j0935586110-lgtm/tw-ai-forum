#!/usr/bin/env bash
# check_credit_pool.sh — BYOK 算力池設計文件機械閘（純 grep，不連網）
#
# 文件必須存在，且同時具備 5 條必要條款：
#   1. API-key-only（只收 API key，不收帳號）
#   2. training-risk notice（明示免費層內容會被用於訓練）
#   3. revocable keys（金鑰可隨時撤回）
#   4. G-coin non-convertible（G 幣不可換現／不可與台幣直接兌換）
#   5. no account sharing（帳號分享＝違規，不收帳號）
set -u

DOC="${CREDIT_POOL_DOC:-$HOME/.hermes/memories/knowledge/公會×j1×機器人-去中間化任務網路-設計與紅線-20260925.md}"

if [ ! -f "$DOC" ]; then
  echo "FAIL: 找不到 BYOK 池設計文件：$DOC"
  exit 1
fi

missing=()

check() {
  # $1 = 條款名稱, $2 = grep -E 樣式
  if grep -qE "$2" "$DOC"; then
    echo "  ok      - $1"
  else
    echo "  MISSING - $1"
    missing+=("$1")
  fi
}

echo "檢查 BYOK 池設計文件 5 條必要條款："
echo "  $DOC"
check "API-key-only（不收帳號）" "只收 API key"
check "training-risk notice（明示訓練風險）" "明示訓練風險"
check "revocable keys（金鑰可隨時撤回）" "金鑰可隨時撤回"
check "G-coin non-convertible（G 幣不可換現／不可與台幣兌換）" "G 幣不可換現|G 幣不可與台幣"
check "no account sharing（不收帳號／帳號分享違規）" "不收帳號|帳號分享"

if [ "${#missing[@]}" -gt 0 ]; then
  echo "FAIL: 缺少 ${#missing[@]} 條必要條款"
  exit 1
fi

echo "PASS: 5 條必要條款齊全"
exit 0
