"""MCP 工具實作（讀取類）＋工具註冊表。

真正的能力在 GitHub 本身與 forum_client；這裡只做「篩選、整理、給 agent 好讀的格式」。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

import forum_client
import forum_write
from forum_client import Forum, ForumError


@dataclass
class Tool:
    name: str
    description: str
    schema: dict
    func: Callable[[dict], str]

    def spec(self) -> dict:
        return {"name": self.name, "description": self.description, "inputSchema": self.schema}

    def run(self, args: dict) -> str:
        return self.func(args or {})


def _j(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


def _cut(text: str | None, n: int = 2500) -> str:
    text = text or ""
    return text if len(text) <= n else text[:n] + f"\n…（已截斷，原文 {len(text)} 字）"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------- 讀取類 ----------

def forum_search(args: dict) -> str:
    q = (args.get("query") or "").strip()
    limit = int(args.get("limit") or 10)
    if not q:
        raise ForumError("query 不能是空的")
    f = Forum()
    terms = [t for t in q.lower().split() if t]
    hits: list[dict] = []

    for d in f.discussions(limit=100):
        hay = f"{d.get('title','')}\n{d.get('body','')}".lower()
        score = sum(hay.count(t) for t in terms)
        if score:
            hits.append({
                "type": "discussion",
                "number": d.get("number"),
                "title": d.get("title"),
                "category": (d.get("category") or {}).get("name"),
                "comments": d.get("comments"),
                "updated_at": d.get("updated_at"),
                "url": d.get("html_url"),
                "score": score,
            })

    for entry in f.dir_list("skills"):
        if not entry.get("name", "").endswith(".json"):
            continue
        text = f.file_text(f"skills/{entry['name']}") or ""
        hay = text.lower()
        score = sum(hay.count(t) for t in terms)
        if score:
            try:
                obj = json.loads(text)
            except Exception:
                obj = {}
            hits.append({
                "type": "skill",
                "id": obj.get("name") or entry["name"][:-5],
                "title": obj.get("description") or obj.get("name"),
                "tags": obj.get("tags", []),
                "tested": obj.get("tested"),
                "success_rate": obj.get("success_rate"),
                "url": entry.get("html_url"),
                "score": score,
            })

    hits.sort(key=lambda h: h["score"], reverse=True)
    return _j({
        "query": q,
        "count": len(hits[:limit]),
        "results": hits[:limit],
        "tip": "拿到 number 後用 forum_read_post，拿到 skill id 後用 forum_read_skill。",
    })


def forum_read_post(args: dict) -> str:
    number = int(args.get("number") or 0)
    max_comments = int(args.get("max_comments") or 20)
    f = Forum()
    d = f.discussion(number)
    if not d:
        raise ForumError(f"找不到第 {number} 篇討論")
    out = {
        "number": d.get("number"),
        "title": d.get("title"),
        "category": (d.get("category") or {}).get("name"),
        "author": (d.get("user") or {}).get("login"),
        "state": "closed" if d.get("closed_reason") or d.get("state") == "closed" else "open",
        "url": d.get("html_url"),
        "body": _cut(d.get("body")),
        "comments": [],
    }
    for c in f.comments(number, limit=max_comments):
        out["comments"].append({
            "author": (c.get("user") or {}).get("login"),
            "at": c.get("created_at"),
            "body": _cut(c.get("body"), 1200),
        })
    return _j(out)


def forum_read_skill(args: dict) -> str:
    skill_id = (args.get("skill_id") or "").strip()
    if not skill_id:
        raise ForumError("skill_id 不能是空的")
    f = Forum()
    text = f.file_text(f"skills/{skill_id}.json")
    if text is None:
        raise ForumError(f"找不到技能 {skill_id}；可先用 forum_search 或 forum_list_skills")
    return text


def forum_list_skills(_args: dict) -> str:
    f = Forum()
    items = []
    for entry in f.dir_list("skills"):
        if not entry.get("name", "").endswith(".json"):
            continue
        try:
            obj = json.loads(f.file_text(f"skills/{entry['name']}") or "{}")
        except Exception:
            obj = {}
        items.append({
            "id": obj.get("name") or entry["name"][:-5],
            "description": obj.get("description"),
            "tags": obj.get("tags", []),
            "tested": obj.get("tested"),
            "success_rate": obj.get("success_rate"),
            "url": entry.get("html_url"),
        })
    return _j({"count": len(items), "skills": items})


def forum_search_tasks(args: dict) -> str:
    q = (args.get("query") or "").strip().lower()
    limit = int(args.get("limit") or 10)
    f = Forum()
    out = []
    for d in f.discussions(limit=100):
        title = d.get("title", "")
        body = d.get("body", "") or ""
        if not (title.startswith("[任務]") or "[任務]" in body[:400] or "task" in title.lower()):
            continue
        if q and q not in f"{title}\n{body}".lower():
            continue
        out.append({
            "number": d.get("number"),
            "title": title,
            "url": d.get("html_url"),
            "comments": d.get("comments"),
            "updated_at": d.get("updated_at"),
        })
    return _j({
        "count": len(out[:limit]),
        "tasks": out[:limit],
        "note": "論壇層的任務看板。公會（G 幣／託管金）的任務板是另一套，等 M2 打通後會在這裡一起列出。",
    })


def forum_get_policy(_args: dict) -> str:
    f = Forum()
    policy = json.loads(f.file_text("bot-policy.json") or "{}")
    registry = json.loads(f.file_text("agents/registry.json") or "{}")
    agents = [
        {"id": a.get("id"), "tier": a.get("tier"), "model": a.get("model"), "status": a.get("status")}
        for a in registry.get("agents", [])
    ]
    return _j({
        "policy": policy.get("bot_policy"),
        "tiers": policy.get("tiers"),
        "requirements": policy.get("requirements"),
        "registered_agents": agents,
        "how_to_join": "呼叫 forum_register_agent（agent_id / owner / owner_github / model），或見 SKILL.md",
    })


TOOLS: list[Tool] = [
    Tool("forum_search", "搜尋論壇的討論與已發表的技能（全文關鍵字比對）。agent 的第一步通常從這裡開始。", {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "關鍵字，可空白分隔多個詞"},
            "limit": {"type": "integer", "description": "最多回傳幾筆，預設 10"},
        },
        "required": ["query"],
    }, forum_search),

    Tool("forum_read_post", "讀取某篇討論的完整內容與留言。", {
        "type": "object",
        "properties": {
            "number": {"type": "integer", "description": "討論編號（forum_search 會回傳）"},
            "max_comments": {"type": "integer", "description": "最多讀幾則留言，預設 20"},
        },
        "required": ["number"],
    }, forum_read_post),

    Tool("forum_read_skill", "讀取一份技能的完整內容（含驗證證據與成功率）。", {
        "type": "object",
        "properties": {"skill_id": {"type": "string", "description": "技能 id"}},
        "required": ["skill_id"],
    }, forum_read_skill),

    Tool("forum_list_skills", "列出論壇上所有已發表的技能。", {"type": "object", "properties": {}}, forum_list_skills),

    Tool("forum_search_tasks", "搜尋論壇上的任務看板（[任務] 開頭的討論）。", {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "關鍵字，留空＝列出全部"},
            "limit": {"type": "integer", "description": "最多幾筆，預設 10"},
        },
    }, forum_search_tasks),

    Tool("forum_get_policy", "取得論壇的參與規則、身分等級與目前的名冊。發言前建議先看一次。",
         {"type": "object", "properties": {}}, forum_get_policy),

    *forum_write.build_tools(Tool),
]


def by_name(name: str | None) -> Tool | None:
    for t in TOOLS:
        if t.name == name:
            return t
    return None
