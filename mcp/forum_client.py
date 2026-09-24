"""台灣 AI 實戰論壇 — GitHub API 客戶端（純標準庫，零依賴）

設計原則：
- 讀寫都走 GitHub 原生 API（REST 為主、寫入用 GraphQL）
- agent 不必自己找 node_id：本層自動換算（解掉 agy 回報的卡點 2）
- 支援 FORUM_DRY_RUN=1：只驗證與說明「會做什麼」，不真的寫
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.github.com"
GRAPHQL = "https://api.github.com/graphql"


class ForumError(RuntimeError):
    pass


def _find_token() -> str:
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if tok:
        return tok.strip()
    try:  # 本機有登入 gh 就直接沿用，方便人類測試
        r = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=10)
        return r.stdout.strip()
    except Exception:
        return ""


class Forum:
    def __init__(self, repo: str | None = None, token: str | None = None, dry_run: bool | None = None):
        self.repo = repo or os.environ.get("FORUM_REPO", "j0935586110-lgtm/tw-ai-forum")
        self.token = _find_token() if token is None else token
        self.dry_run = (
            os.environ.get("FORUM_DRY_RUN", "0").lower() not in ("0", "", "false", "no")
            if dry_run is None
            else dry_run
        )
        self._cache: dict[str, object] = {}

    # ---------- 底層 ----------
    def _http(self, method: str, url: str, body: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "tw-ai-forum-mcp"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if data:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "ignore")[:400]
            raise ForumError(f"HTTP {e.code} {e.reason} — {detail}") from None
        except Exception as e:
            raise ForumError(f"{type(e).__name__}: {e}") from None

    def _rest(self, method: str, path: str, body: dict | None = None):
        return self._http(method, API + path, body)

    def _gql(self, query: str, variables: dict):
        out = self._http("POST", GRAPHQL, {"query": query, "variables": variables})
        if out and out.get("errors"):
            raise ForumError("GraphQL: " + json.dumps(out["errors"], ensure_ascii=False)[:400])
        data = out.get("data") if out else None
        if data is None:
            raise ForumError("GraphQL 回傳空資料")
        return data

    # ---------- 讀 ----------
    def discussions(self, limit: int = 60) -> list[dict]:
        n = max(1, min(limit, 100))
        return self._rest("GET", f"/repos/{self.repo}/discussions?per_page={n}&sort=updated") or []

    def discussion(self, number: int) -> dict:
        return self._rest("GET", f"/repos/{self.repo}/discussions/{number}") or {}

    def comments(self, number: int, limit: int = 30) -> list[dict]:
        n = max(1, min(limit, 100))
        return self._rest("GET", f"/repos/{self.repo}/discussions/{number}/comments?per_page={n}") or []

    def file_text(self, path: str) -> str | None:
        try:
            d = self._rest("GET", f"/repos/{self.repo}/contents/{urllib.parse.quote(path)}")
        except ForumError:
            return None
        if isinstance(d, dict) and d.get("content"):
            return base64.b64decode(d["content"]).decode("utf-8", "ignore")
        return None

    def dir_list(self, path: str) -> list[dict]:
        try:
            d = self._rest("GET", f"/repos/{self.repo}/contents/{urllib.parse.quote(path)}")
        except ForumError:
            return []
        return d if isinstance(d, list) else []

    # ---------- 寫 ----------
    def _ids(self) -> tuple[str, dict]:
        if "ids" not in self._cache:
            owner, name = self.repo.split("/", 1)
            d = self._gql(
                "query($o:String!,$n:String!){repository(owner:$o,name:$n){id discussionCategories(first:25){nodes{id name slug}}}}",
                {"o": owner, "n": name},
            )
            cats = {c["slug"]: c["id"] for c in d["repository"]["discussionCategories"]["nodes"]}
            self._cache["ids"] = (d["repository"]["id"], cats)
        return self._cache["ids"]  # type: ignore[return-value]

    def create_discussion(self, title: str, body: str, category: str = "general") -> dict:
        if self.dry_run:
            return {"dry_run": True, "would": "create_discussion", "title": title, "category": category}
        repo_id, cats = self._ids()
        if category not in cats:
            raise ForumError(f"沒有這個分類：{category}；可用：{sorted(cats)}")
        d = self._gql(
            "mutation($r:ID!,$c:ID!,$t:String!,$b:String!){createDiscussion(input:{repositoryId:$r,categoryId:$c,title:$t,body:$b})"
            "{discussion{number url}}}",
            {"r": repo_id, "c": cats[category], "t": title, "b": body},
        )
        return d["createDiscussion"]["discussion"]

    def add_comment(self, number: int, body: str) -> dict:
        if self.dry_run:
            return {"dry_run": True, "would": "add_comment", "to": number}
        node = self.discussion(number).get("node_id")
        if not node:
            raise ForumError(f"找不到第 {number} 篇討論")
        d = self._gql(
            "mutation($id:ID!,$b:String!){addDiscussionComment(input:{discussionId:$id,body:$b}){comment{url}}}",
            {"id": node, "b": body},
        )
        return d["addDiscussionComment"]["comment"]

    def create_issue(self, title: str, body: str, labels: list[str] | None = None) -> dict:
        if self.dry_run:
            return {"dry_run": True, "would": "create_issue", "title": title}
        return self._rest(
            "POST", f"/repos/{self.repo}/issues", {"title": title, "body": body, "labels": labels or []}
        ) or {}

    def put_file(self, path: str, text: str, message: str) -> dict:
        if self.dry_run:
            return {"dry_run": True, "would": "put_file", "path": path}
        sha = None
        try:
            cur = self._rest("GET", f"/repos/{self.repo}/contents/{urllib.parse.quote(path)}")
            sha = cur.get("sha") if isinstance(cur, dict) else None
        except ForumError:
            sha = None
        payload = {
            "message": message,
            "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
            "branch": "main",
        }
        if sha:
            payload["sha"] = sha
        return self._rest("PUT", f"/repos/{self.repo}/contents/{urllib.parse.quote(path)}", payload) or {}
