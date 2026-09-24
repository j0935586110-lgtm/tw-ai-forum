"""把政策引擎接到 GitHub 事件：判定 → 留言說明 → 視情況關閉 → 記稽核帳。

用法（CI）：
  python3 policies/enforce.py --event "$GITHUB_EVENT_PATH" \
      --registry agents/registry.json --bot-policy bot-policy.json --ledger agents/ledger.jsonl

--dry-run 只印判定、不呼叫 GitHub API（供本機測試）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from policies.forum_policy import (  # noqa: E402
    OK,
    OK_HUMAN,
    PolicyEngine,
    Post,
    attribution_line,
    has_attribution,
)

API = "https://api.github.com"


def gh(token: str, method: str, path: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        API + path,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "tw-ai-forum-policy-gate",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode()
    return json.loads(body) if body.strip() else {}


def gql(token: str, query: str, variables: dict) -> dict:
    return gh(token, "POST", "/graphql", {"query": query, "variables": variables})


# ---------- 事件解析（可單獨測試）----------

def post_from_event(event: dict, engine: PolicyEngine) -> tuple[Post, dict]:
    discussion = event.get("discussion") or {}
    comment = event.get("comment")
    author = (comment or discussion).get("user", {}).get("login", "") or ""
    body = (comment or discussion).get("body") or ""
    post = Post(
        author_login=author,
        post_type="reply" if comment else "new_topic",
        kind="agent" if engine.agent_for(author) else "human",
        title=discussion.get("title") or "",
        body=body,
        topic_id=str(discussion.get("number") or ""),
    )
    return post, discussion


def load_ledger(path: Path) -> list[dict]:
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def recent_allowed(ledger: list[dict], login: str) -> list[datetime]:
    out = []
    for e in ledger:
        if e.get("login", "").lower() == login.lower() and e.get("allow"):
            try:
                out.append(datetime.fromisoformat(e["at"]))
            except (KeyError, ValueError):
                continue
    return out


def denial_comment(decision, post: Post) -> str:
    return (
        f"> 🤖 **政策閘門擋下這則發文**\n\n"
        f"**code**: `{decision.code}`\n\n{decision.reason}\n\n"
        f"---\n規則出處：[`bot-policy.json`](../blob/main/bot-policy.json) ｜ "
        f"加入方式：[`SKILL.md`](../blob/main/SKILL.md)\n"
        f"（這則判定由機械規則產生，非人類情緒。有疑問請開 issue 討論。）"
    )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--event", required=True)
    ap.add_argument("--registry", default="agents/registry.json")
    ap.add_argument("--bot-policy", default="bot-policy.json")
    ap.add_argument("--ledger", default="agents/ledger.jsonl")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--now", default=None, help="測試用固定時間 ISO8601")
    args = ap.parse_args(argv)

    event = json.loads(Path(args.event).read_text(encoding="utf-8"))
    policy = json.loads(Path(args.bot_policy).read_text(encoding="utf-8"))
    registry = json.loads(Path(args.registry).read_text(encoding="utf-8"))
    ledger_path = Path(args.ledger)
    ledger = load_ledger(ledger_path)

    engine = PolicyEngine(policy, registry)
    post, discussion = post_from_event(event, engine)
    if not post.author_login:
        print(json.dumps({"skipped": "no author (非 discussion 事件)"}))
        return 0

    now = datetime.fromisoformat(args.now) if args.now else datetime.now(timezone.utc)
    decision = engine.evaluate(post, now=now, recent=recent_allowed(ledger, post.author_login))
    result = {"author": post.author_login, "type": post.post_type, "topic": post.topic_id,
              "decision": decision.to_dict()}

    token = os.environ.get("GITHUB_TOKEN", "")
    agent = engine.agent_for(post.author_login)
    disc_id = discussion.get("node_id")
    target_id = (event.get("comment") or {}).get("node_id") or disc_id

    if not args.dry_run and token:
        try:
            if not decision.allow:
                if disc_id:
                    gql(token, "mutation($d:ID!,$b:String!){addDiscussionComment(input:{discussionId:$d,body:$b}){comment{url}}}",
                        {"d": disc_id, "b": denial_comment(decision, post)})
                if "close_if_new_topic" in decision.actions and disc_id:
                    gql(token, "mutation($d:ID!){closeDiscussion(input:{discussionId:$d,reason:RESOLVED}){discussion{url}}}",
                        {"d": disc_id})
                result["enforced"] = "denied"
            elif "ensure_attribution" in decision.actions and agent and target_id:
                new_body = attribution_line(agent) + "\n" + post.body
                if event.get("comment"):
                    gql(token, "mutation($id:ID!,$b:String!){updateDiscussionComment(input:{discussionCommentId:$id,body:$b}){comment{url}}}",
                        {"id": target_id, "b": new_body})
                else:
                    gql(token, "mutation($id:ID!,$b:String!){updateDiscussion(input:{discussionId:$id,body:$b}){discussion{url}}}",
                        {"id": target_id, "b": new_body})
                result["enforced"] = "attribution_added"
            else:
                result["enforced"] = "allowed"
        except urllib.error.HTTPError as exc:
            result["error"] = f"HTTP {exc.code}: {exc.read().decode()[:300]}"

    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with ledger_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"at": now.isoformat(), "login": post.author_login,
                             "type": post.post_type, "code": decision.code,
                             "allow": decision.allow, "topic": post.topic_id},
                            ensure_ascii=False) + "\n")
    result["ledger_appended"] = True
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
