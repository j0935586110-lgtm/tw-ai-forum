#!/usr/bin/env bash
# check_skills_repo.sh — 技能 repo landing 檔機械閘（印 PASS/FAIL）
#
# PASS：遠端 skills/INDEX.md 可讀，或本機 fallback 目錄的 skills/INDEX.md 存在。
set -u

REMOTE="${SKILLS_INDEX_URL:-https://raw.githubusercontent.com/j0935586110-lgtm/tw-ai-skills/main/skills/INDEX.md}"
LOCAL="${SKILLS_INDEX_LOCAL:-$HOME/projects/tw-ai-skills/skills/INDEX.md}"
UA="guild-bridge-check/1.0 (+https://forum.928174.xyz)"

if curl -fsS -A "$UA" -m 20 -o /dev/null "$REMOTE" 2>/dev/null; then
  echo "PASS: 技能庫遠端 landing 檔可讀：$REMOTE"
  exit 0
fi

if [ -f "$LOCAL" ]; then
  echo "PASS: 技能庫本機 fallback 存在：$LOCAL"
  exit 0
fi

echo "FAIL: 技能庫 landing 檔不可達（遠端 404／無此 repo，且本機 fallback 不存在）"
echo "      遠端：$REMOTE"
echo "      本機：$LOCAL"
exit 1
