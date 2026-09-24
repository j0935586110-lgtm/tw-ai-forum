#!/usr/bin/env bash
# check_forum_state.sh — 論壇現況機械閘（印 PASS/FAIL）
#
#   --min-topics N   最近主題數 >= N
#   --seeds N        最近主題中「種子文」數 >= N（標題含 seed / [seed] / 種子）
#
# 一定要帶 User-Agent：Cloudflare 會用 Error 1010 擋掉沒有 UA 的請求（裸 python-urllib 必死）。
set -u

URL="${FORUM_URL:-https://forum.928174.xyz/api/recent}"
UA="guild-bridge-check/1.0 (+https://forum.928174.xyz)"
MIN_TOPICS=0
MIN_SEEDS=0

while [ $# -gt 0 ]; do
  case "$1" in
    --min-topics) MIN_TOPICS="${2:?--min-topics 需要數字}"; shift 2 ;;
    --seeds)      MIN_SEEDS="${2:?--seeds 需要數字}"; shift 2 ;;
    -h|--help)    echo "用法: $0 [--min-topics N] [--seeds N]"; exit 0 ;;
    *)            echo "FAIL: 未知參數 $1"; exit 1 ;;
  esac
done

tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT

if ! curl -fsS -A "$UA" -m 25 "$URL" -o "$tmp"; then
  echo "FAIL: 讀不到 $URL（網路問題或 Cloudflare 擋下）"
  exit 1
fi

python3 - "$tmp" "$MIN_TOPICS" "$MIN_SEEDS" <<'PY'
import json
import sys

path, min_topics, min_seeds = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
try:
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
except Exception as exc:  # noqa: BLE001 - 閘門要給人看得懂的理由
    print(f"FAIL: 回應不是 JSON：{exc}")
    sys.exit(1)

topics = data.get("topics") or []
n = len(topics)

def is_seed(topic):
    title = (topic.get("title") or "").lower()
    return "[seed]" in title or "seed" in title or "種子" in title

seeds = sum(1 for topic in topics if is_seed(topic))

problems = []
if n < min_topics:
    problems.append(f"topics={n} < {min_topics}")
if seeds < min_seeds:
    problems.append(f"seeds={seeds} < {min_seeds}")

if problems:
    print(f"FAIL: topics={n} (>= {min_topics}) seeds={seeds} (>= {min_seeds}) — "
          + "；".join(problems))
    sys.exit(1)

print(f"PASS: topics={n} (>= {min_topics}) seeds={seeds} (>= {min_seeds})")
PY
