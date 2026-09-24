"""給 agent 用的 NodeBB 工具（MCP tools）。

設計原則（沿用 GitHub 版）：
- 讀多寫少：讀 6 個、寫 2 個、體檢 1 個。
- 寫入類一律支援 NODEBB_DRY_RUN=1 預演（完全不碰網路）。
- 輸出給人與 agent 都好看：純文字、一行一件事、附連結。
"""
from __future__ import annotations

import json
import os

import nodebb_client
from nodebb_client import NodeBB, NodeBBError, html_to_text

DRY_RUN = os.environ.get("NODEBB_DRY_RUN") == "1"


def _client() -> NodeBB:
    return NodeBB()


def _dry(what: str) -> str:
    return f"🧪 預演模式（NODEBB_DRY_RUN=1）：我「會」{what}，但沒有真的送出。"


class Tool:
    def __init__(self, name, description, schema, handler):
        self.name = name
        self.description = description
        self.schema = schema
        self.handler = handler

    def spec(self) -> dict:
        return {"name": self.name, "description": self.description, "inputSchema": self.schema}

    def run(self, args: dict) -> str:
        return self.handler(args or {})


def _obj(props: dict, required: list | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or [],
            "additionalProperties": False}


def _topic_line(t: dict) -> str:
    title = t.get("title") or "(無標題)"
    tid = t.get("tid")
    posts = t.get("postcount", t.get("posts", "?"))
    user = (t.get("user") or {}).get("username", "?")
    return f"#{tid} {title} ｜ 作者 {user} ｜ {posts} 篇 ｜ {self_url(tid)}"


def self_url(tid) -> str:
    base = os.environ.get("NODEBB_URL", nodebb_client.DEFAULT_BASE).rstrip("/")
    return f"{base}/topic/{tid}"


# ---------- 讀 ----------

def t_list_categories(_):
    cats = _client().categories()
    if not cats:
        return "這個論壇還沒有任何看板。"
    lines = ["看板清單（cid 是發文時要填的編號）："]
    for c in cats:
        lines.append(f"- cid={c.get('cid')}｜{c.get('name')}｜{c.get('topic_count', 0)} 個主題"
                     f"｜{c.get('description') or '（無說明）'}")
    return "\n".join(lines)


def t_recent(args):
    limit = int(args.get("limit") or 15)
    topics = _client().recent(limit)
    if not topics:
        return "最近沒有新主題。"
    return f"最近 {len(topics)} 個主題：\n" + "\n".join(_topic_line(t) for t in topics)


def t_search(args):
    q = args.get("query") or args.get("term")
    if not q:
        return "請給 query（要搜尋的字）。"
    limit = int(args.get("limit") or 10)
    res = _client().search(q, limit)
    posts = (res.get("posts") or [])[:limit]
    topics = (res.get("topics") or [])[:limit]
    users = (res.get("users") or [])[:limit]
    if not (posts or topics or users):
        return f"「{q}」沒有結果。"
    out = [f"搜尋「{q}」："]
    if topics:
        out.append("主題：")
        out += [f"  - {_topic_line(t)}" for t in topics]
    if posts:
        out.append("回覆：")
        for p in posts:
            body = html_to_text(p.get("content") or "")[:120].replace("\n", " ")
            out.append(f"  - #{p.get('tid')} {(p.get('topic') or {}).get('title', '')}｜"
                       f"{(p.get('user') or {}).get('username')}：{body}")
    if users:
        out.append("使用者：" + "、".join(u.get("username", "?") for u in users))
    return "\n".join(out)


def t_read_topic(args):
    tid = args.get("tid")
    if tid is None:
        return "請給 tid（主題編號）。"
    max_posts = int(args.get("max_posts") or 20)
    d = _client().topic(tid)
    posts = d.get("posts") or []
    # 注意：/api/topic 沒有頂層 user 欄位（實測為 null），作者要從第一篇貼文推
    author = ((posts[0].get("user") or {}).get("username") if posts else None) or "?"
    cat = d.get("category")
    cat_name = cat.get("name") if isinstance(cat, dict) else (cat or "（未分類）")
    head = [
        f"#{d.get('tid')} {d.get('title')}",
        f"看板：{cat_name}｜作者：{author}｜共 {d.get('postcount', len(posts))} 篇",
        self_url(d.get("tid")),
        "",
    ]
    body = []
    for p in posts[:max_posts]:
        who = (p.get("user") or {}).get("username", "?")
        body.append(f"— {who}（#{p.get('pid')}）—")
        body.append(html_to_text(p.get("content") or "") or "（空白）")
        body.append("")
    if len(posts) > max_posts:
        body.append(f"（還有 {len(posts) - max_posts} 篇沒顯示，可用 max_posts 調整）")
    return "\n".join(head + body).strip()


def t_read_user(args):
    slug = args.get("slug") or args.get("username")
    if not slug:
        return "請給 slug（使用者代號，例如 jianwei）。"
    u = _client().user(slug)
    if not u or u.get("uid") is None:
        return f"找不到使用者「{slug}」。"
    return (f"{u.get('username')}（uid {u.get('uid')}）\n"
            f"發文 {u.get('postcount', 0)} 篇｜聲望 {u.get('reputation', 0)}｜"
            f"加入 {u.get('joindateISO', '?')}\n"
            f"簡介：{html_to_text(u.get('aboutme') or '') or '（無）'}")


def t_whoami(_):
    c = _client()
    if not c.token:
        return ("目前沒有 token → 只能讀公開內容，不能搜尋、發文、回覆。\n"
                f"（要寫入請設定 NODEBB_TOKEN 或 {nodebb_client.TOKEN_FILE}）")
    me = c.me()
    return (f"我是 {me.get('username')}（uid {me.get('uid')}）\n"
            f"發文 {me.get('postcount', 0)} 篇｜聲望 {me.get('reputation', 0)}\n"
            f"站台：{c.base}")


def t_health(_):
    c = _client()
    cfg = c.config()
    # 注意：/api/config 沒有 version 欄位，站名是 siteTitle（實測）
    out = [f"站台：{c.base}",
           f"站名：{cfg.get('siteTitle') or '（未命名）'}",
           f"說明：{cfg.get('description') or '（無）'}"]
    try:
        cats = c.categories()
        out.append(f"看板數：{len(cats)}")
    except NodeBBError as e:
        out.append(f"看板讀取失敗：{e}")
    if not c.token:
        out.append("token：沒有（只能讀公開內容）")
    else:
        try:
            me = c.me()
            out.append(f"token：有效（身分 {me.get('username')}，uid {me.get('uid')}）")
        except NodeBBError as e:
            out.append(f"token：有但無效 → {e}")
    return "\n".join(out)


# ---------- 寫 ----------

def t_create_topic(args):
    cid, title, content = args.get("cid"), args.get("title"), args.get("content")
    if cid is None or not title or not content:
        return "需要 cid（看板編號）、title（標題）、content（內容）。先用 forum_list_categories 看看板。"
    if DRY_RUN:
        return _dry(f"在 cid={cid} 開新主題「{title}」（{len(content)} 字）")
    r = (_client().create_topic(cid, title, content) or {}).get("response") or {}
    return (f"✅ 已建立主題 #{r.get('tid')}「{r.get('title', title)}」\n"
            f"{self_url(r.get('tid'))}")


def t_reply(args):
    tid, content = args.get("tid"), args.get("content")
    if tid is None or not content:
        return "需要 tid（主題編號）、content（內容）。"
    if DRY_RUN:
        return _dry(f"回覆主題 #{tid}（{len(content)} 字）")
    r = (_client().reply(tid, content) or {}).get("response") or {}
    return f"✅ 已回覆 #{r.get('tid', tid)}：{r.get('url', self_url(tid))}"


TOOLS = [
    Tool("forum_health", "站台體檢：版本、看板數、token 是否有效。",
         _obj({}), t_health),
    Tool("forum_list_categories", "列出所有看板（含 cid，發文時要填這個編號）。",
         _obj({}), t_list_categories),
    Tool("forum_recent", "最近的主題（預設 15 個）。",
         _obj({"limit": {"type": "integer", "description": "最多幾個（預設 15）"}}), t_recent),
    Tool("forum_search", "搜尋主題與回覆（需要 token）。",
         _obj({"query": {"type": "string", "description": "要搜尋的字"},
               "limit": {"type": "integer", "description": "每類最多幾筆（預設 10）"}},
              ["query"]), t_search),
    Tool("forum_read_topic", "讀一個主題的內容與回覆（HTML 會自動轉純文字）。",
         _obj({"tid": {"type": "integer", "description": "主題編號"},
               "max_posts": {"type": "integer", "description": "最多讀幾篇（預設 20）"}},
              ["tid"]), t_read_topic),
    Tool("forum_read_user", "讀一位使用者的公開資料（發文數、聲望、簡介）。",
         _obj({"slug": {"type": "string", "description": "使用者代號，例如 jianwei"}},
              ["slug"]), t_read_user),
    Tool("forum_whoami", "確認我現在是用哪個身分在跟論壇說話（會驗證 token）。",
         _obj({}), t_whoami),
    Tool("forum_create_topic", "開一個新主題（需要 token）。",
         _obj({"cid": {"type": "integer", "description": "看板編號"},
               "title": {"type": "string", "description": "標題"},
               "content": {"type": "string", "description": "內容（可含 Markdown，NodeBB 會轉）"}},
              ["cid", "title", "content"]), t_create_topic),
    Tool("forum_reply", "回覆既有主題（需要 token）。",
         _obj({"tid": {"type": "integer", "description": "主題編號"},
               "content": {"type": "string", "description": "內容"}},
              ["tid", "content"]), t_reply),
]


def by_name(name: str) -> Tool | None:
    for t in TOOLS:
        if t.name == name:
            return t
    return None
