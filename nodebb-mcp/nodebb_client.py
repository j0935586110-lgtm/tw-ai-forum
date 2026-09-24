"""NodeBB REST API 客戶端（純標準庫、零依賴）。

實測過的 NodeBB 行為（別踩）：
1. 公開內容「讀取」不需要 token；但「搜尋」和「寫入」需要（否則 401）。
2. 打 API 一定要帶 User-Agent，否則 Cloudflare 會用 Error 1010 擋掉（裸 python-urllib 必死）。
3. 錯誤回應長這樣：{"status":{"code":"...","message":"..."}}，要轉成看得懂的中文訊息給 agent。
4. 貼文內容是 HTML，agent 讀起來很吵 → 讀取時一律轉純文字。
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_BASE = "https://forum.928174.xyz"
USER_AGENT = "nodebb-mcp/1.0 (+https://forum.928174.xyz)"
TOKEN_FILE = os.path.expanduser("~/.hermes/secrets/nodebb-agent-token")


class NodeBBError(RuntimeError):
    """NodeBB 回傳的錯誤（已翻成人看得懂的訊息）。"""


def read_token() -> str | None:
    """token 來源：環境變數 NODEBB_TOKEN → 本機 secrets 檔 → None（只能讀公開內容）。"""
    tok = os.environ.get("NODEBB_TOKEN")
    if tok:
        return tok.strip()
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, encoding="utf-8") as f:
            return f.read().strip()
    return None


_TAG = re.compile(r"<[^>]+>")
_BR = re.compile(r"<\s*br\s*/?\s*>", re.I)
_BLOCK = re.compile(r"</\s*(p|div|li|h[1-6]|blockquote)\s*>", re.I)


def html_to_text(html: str) -> str:
    """NodeBB 的貼文內容是 HTML；轉成純文字，保留換行。"""
    if not html:
        return ""
    txt = _BR.sub("\n", html)
    txt = _BLOCK.sub("\n", txt)
    txt = _TAG.sub("", txt)
    txt = (txt.replace("&nbsp;", " ").replace("&amp;", "&")
              .replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
              .replace("&#39;", "'"))
    txt = re.sub(r"\n{3,}", "\n\n", txt)
    return txt.strip()


class NodeBB:
    """對 NodeBB 的 REST API 說話。"""

    def __init__(self, base: str | None = None, token: str | None = None, timeout: int = 25):
        self.base = (base or os.environ.get("NODEBB_URL") or DEFAULT_BASE).rstrip("/")
        self.token = token if token is not None else read_token()
        self.timeout = timeout

    # ---------- 底層 ----------

    def _request(self, method: str, path: str, params: dict | None = None,
                 body: dict | None = None, need_auth: bool = False) -> dict:
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("User-Agent", USER_AGENT)  # 沒帶會被 Cloudflare 擋（Error 1010）
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")

        if need_auth and not self.token:
            raise NodeBBError(
                "這個操作需要 token，但找不到。請設定環境變數 NODEBB_TOKEN，"
                f"或把 token 放進 {TOKEN_FILE}。"
            )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            raise NodeBBError(self._explain(e.code, raw, path)) from None
        except urllib.error.URLError as e:
            raise NodeBBError(f"連不上 {self.base}（{e.reason}）") from None

        if not raw.strip():
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            raise NodeBBError(f"回應不是 JSON（{path}）：{raw[:120]}") from None

    @staticmethod
    def _explain(code: int, raw: str, path: str) -> str:
        msg = ""
        try:
            j = json.loads(raw)
            msg = (j.get("status") or {}).get("message") or j.get("error") or ""
        except Exception:
            msg = raw[:120]
        if code == 401:
            return (f"沒有權限（401）打 {path}：{msg or '需要有效的 token'}。"
                    "搜尋與發文都需要 token，讀公開內容不用。")
        if code == 403:
            return f"被拒絕（403）打 {path}：{msg or '權限不足'}"
        if code == 404:
            return f"找不到（404）：{path} 不存在，或該主題/使用者已刪除。"
        return f"NodeBB 回應 {code}（{path}）：{msg or raw[:120]}"

    # ---------- 讀 ----------

    def config(self) -> dict:
        return self._request("GET", "/api/config")

    def me(self) -> dict:
        return self._request("GET", "/api/self")

    def categories(self) -> list:
        return (self._request("GET", "/api/categories") or {}).get("categories", [])

    def recent(self, limit: int = 20) -> list:
        return (self._request("GET", "/api/recent") or {}).get("topics", [])[:limit]

    def search(self, query: str, limit: int = 10) -> dict:
        return self._request("GET", "/api/search", params={"term": query, "in": "titlesposts"}) or {}

    def topic(self, tid: int) -> dict:
        return self._request("GET", f"/api/topic/{int(tid)}")

    def user(self, slug: str) -> dict:
        return self._request("GET", f"/api/user/{urllib.parse.quote(str(slug))}")

    # ---------- 寫 ----------

    def create_topic(self, cid: int, title: str, content: str) -> dict:
        return self._request("POST", "/api/v3/topics",
                             body={"cid": int(cid), "title": title, "content": content},
                             need_auth=True)

    def reply(self, tid: int, content: str) -> dict:
        return self._request("POST", f"/api/v3/topics/{int(tid)}",
                             body={"content": content}, need_auth=True)
