"""公會任務 → Hermes 格式 SKILL.md → 技能 repo PR。

只做兩件事：
1. 把完成的公會任務渲染成 Hermes 可安裝的 `SKILL.md`（YAML frontmatter 有 `name` 與
   以 `Use when ` 開頭的 `description`）。
2. 印出 `gh pr create` 指令；**預設只印不跑**，要真的開 PR 必須明示 `--apply`。

安全：
- 渲染後若內容含疑似憑證（`sk-`、`ghp_`、`AIza`、`xoxb-`、PEM 私鑰標頭）一律拒印。
- 不把任何 token 寫進檔案；PR 指令由 `gh` 自己的認證處理。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

from .bridge import BridgeError, Quest, load_quest

DEFAULT_REPO = "j0935586110-lgtm/tw-ai-skills"

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

# 疑似憑證：寧可誤擋，不可外洩
_CREDENTIAL_PATTERNS = (
    ("OpenAI 風格金鑰（sk-）", re.compile(r"\bsk-[A-Za-z0-9_-]{8,}")),
    ("GitHub PAT（ghp_）", re.compile(r"\bghp_[A-Za-z0-9]{20,}")),
    ("Google API key（AIza）", re.compile(r"\bAIza[A-Za-z0-9_-]{20,}")),
    ("Slack bot token（xoxb-）", re.compile(r"\bxoxb-[A-Za-z0-9-]{10,}")),
    ("PEM 私鑰標頭", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")),
)


class SkillSinkError(RuntimeError):
    """技能產線拒絕產出（例如偵測到憑證）。"""


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")


def _skill_name(quest: Quest) -> str:
    name = slugify(f"{quest.quest_id} {quest.title}") or f"guild-quest-{slugify(quest.quest_id)}"
    name = name[:64].strip("-")
    if not SLUG_RE.match(name):
        name = f"guild-quest-{slugify(quest.quest_id)}"[:64].strip("-")
    return name


def find_credential(text: str) -> str | None:
    """回傳命中的憑證類型；沒有則回 None。"""
    for label, pattern in _CREDENTIAL_PATTERNS:
        if pattern.search(text or ""):
            return label
    return None


def assert_no_credential(text: str) -> None:
    hit = find_credential(text)
    if hit:
        raise SkillSinkError(f"渲染內容疑似含憑證（{hit}），拒絕產出／開 PR")


def _yaml_str(value: object) -> str:
    # JSON 字串是合法 YAML 純量，且能安全處理中文與引號
    return json.dumps(str(value), ensure_ascii=False)


def render_skill(quest: Quest) -> str:
    """渲染 Hermes 格式 SKILL.md；含憑證就 raise SkillSinkError。"""
    description = (
        f'Use when you need to repeat the guild quest "{quest.title}" '
        f"(quest {quest.quest_id}); verified evidence: {quest.evidence_url}"
    )
    frontmatter_lines = [
        "---",
        f"name: {_yaml_str(_skill_name(quest))}",
        f"description: {_yaml_str(description)}",
        f"version: {_yaml_str('1.0.0')}",
        f"license: {_yaml_str('MIT')}",
        "metadata:",
        f"  hermes_category: {_yaml_str('guild-quest')}",
        f"  source_quest: {_yaml_str(quest.quest_id)}",
        f"  evidence_url: {_yaml_str(quest.evidence_url)}",
        f"  agent_id: {_yaml_str(quest.agent_id)}",
        f"  model: {_yaml_str(quest.model)}",
        "---",
    ]

    body_lines = [
        "",
        f"# {quest.title}",
        "",
        "## When to Use",
        "",
        description,
        "",
        "## 任務內容",
        "",
        quest.description or "（公會任務未提供描述）",
        "",
        "## 怎麼驗的",
        "",
        f"- quest id：`{quest.quest_id}`",
        f"- 完成證據：{quest.evidence_url}",
        f"- 執行 agent：`{quest.agent_id}`（{quest.model}）",
        "",
        "## 來源",
        "",
        f"- 冒險者公會任務 `{quest.quest_id}`（https://adventurers-guild-tan.vercel.app）",
        "- 由 `python3 -m worker.guild_bridge.skill_sink` 自動沉澱",
        "",
    ]

    text = "\n".join(frontmatter_lines + body_lines)
    assert_no_credential(text)
    return text


def pr_create_command(*, repo: str, branch: str, title: str, body: str,
                      base: str = "main") -> list[str]:
    return [
        "gh", "pr", "create",
        "--repo", repo,
        "--base", base,
        "--head", branch,
        "--title", title,
        "--body", body,
    ]


def open_pr(*, repo: str, branch: str, title: str, body: str, base: str = "main",
            apply: bool = False, runner=None, printer=print) -> dict:
    """印出（必要時執行）`gh pr create`。runner=None 時才在 apply 時解析 subprocess.run。"""
    command = pr_create_command(repo=repo, branch=branch, title=title, body=body, base=base)
    printer("$ " + " ".join(shlex.quote(part) for part in command))
    if not apply:
        printer("🧪 未加 --apply：只印出指令，沒有執行 gh。")
        return {"applied": False, "command": command}

    run = runner or subprocess.run
    proc = run(command, capture_output=True, text=True)
    return {
        "applied": True,
        "command": command,
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m worker.guild_bridge.skill_sink",
        description="把完成的公會任務渲染成 Hermes SKILL.md，並印出 gh pr create（預設不執行）。",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--quest-file", help="本機 quest JSON fixture")
    source.add_argument("--quest-id", help="從公會 REST 讀取的 quest id")
    parser.add_argument("--out", default=None, help="寫出 SKILL.md 的路徑（預設印到 stdout）")
    parser.add_argument("--repo", default=os.environ.get("SKILLS_REPO") or DEFAULT_REPO,
                        help="技能 repo（owner/name）")
    parser.add_argument("--base", default="main", help="PR base 分支")
    parser.add_argument("--branch", default=None, help="PR head 分支（預設 skill/<name>）")
    parser.add_argument("--title", default=None, help="PR 標題")
    parser.add_argument("--apply", action="store_true",
                        help="真的執行 gh pr create（預設只印出）")
    args = parser.parse_args(argv)

    try:
        quest = load_quest(quest_file=args.quest_file, quest_id=args.quest_id)
        text = render_skill(quest)
    except (BridgeError, SkillSinkError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
        print(f"✅ 已寫出 {out_path}")
    else:
        print(text)

    branch = args.branch or f"skill/{_skill_name(quest)}"
    title = args.title or f"skill: {quest.title}"
    try:
        result = open_pr(repo=args.repo, branch=branch, title=title, body=text,
                         base=args.base, apply=args.apply)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"FAIL: gh 執行失敗：{exc}", file=sys.stderr)
        return 1
    if result.get("applied") and result.get("returncode") not in (0, None):
        print(f"FAIL: gh 回傳 {result['returncode']}：{result.get('stderr', '')}",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
