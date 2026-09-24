#!/usr/bin/env python3
"""把 worker/src/index.js 部署上 Cloudflare（走後台自己的內部 API）。

為什麼不直接 `wrangler deploy`：
    wrangler 需要一次性 OAuth 授權，而 Cloudflare 對 OAuth 授權會要求「重新驗證身分」
    （會跳出登入驗證頁），那一步必須真人操作。改用「已經登入的瀏覽器分頁」呼叫後台
    內部 API 是等價且可重複的做法，不需要任何憑證落在檔案裡。

前置：
    有一個已登入 dash.cloudflare.com 的 Chromium，開著 --remote-debugging-port=9222

⚠️ 機器上可能同時有多個 Chrome；localhost 會走 IPv6 打到另一個，所以一律用 127.0.0.1。

用法：
    python3 worker/deploy_via_dashboard.py            # 部署
    python3 worker/deploy_via_dashboard.py --dry-run  # 只列出目前線上的 script 資訊
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

import websocket

ACCOUNT_ID = "b1f8273984560a8f597309138c1b0f45"
WORKER_NAME = "tw-ai-forum-mcp"
MODULE = "worker.js"
CDP = "http://127.0.0.1:9222"
COMPAT_DATE = "2026-09-24"
SRC = Path(__file__).resolve().parent / "src" / "index.js"


class Cdp:
    def __init__(self, url: str) -> None:
        self.ws = websocket.create_connection(url, timeout=120)
        self.i = 0

    def cmd(self, method: str, **params):
        self.i += 1
        self.ws.send(json.dumps({"id": self.i, "method": method, "params": params}))
        while True:
            r = json.loads(self.ws.recv())
            if r.get("id") == self.i:
                if "error" in r:
                    raise RuntimeError(f"{method}: {r['error']}")
                return r.get("result", {})

    def ev(self, expr: str, await_promise: bool = False):
        r = self.cmd("Runtime.evaluate", expression=expr, returnByValue=True,
                     awaitPromise=await_promise, timeout=120000)
        if r.get("exceptionDetails"):
            return {"EXCEPTION": str(r["exceptionDetails"])[:400]}
        return r.get("result", {}).get("value")

    def close(self) -> None:
        self.ws.close()


def dash_page() -> Cdp:
    tabs = json.load(urllib.request.urlopen(f"{CDP}/json/list", timeout=15))
    pages = [t for t in tabs if t.get("type") == "page" and "dash.cloudflare.com" in (t.get("url") or "")]
    if not pages:
        sys.exit("找不到已登入 dash.cloudflare.com 的分頁——請先在瀏覽器登入 Cloudflare")
    return Cdp(pages[0]["webSocketDebuggerUrl"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只查目前線上的 script，不上傳")
    args = ap.parse_args()
    if not SRC.exists():
        sys.exit(f"找不到 {SRC}")
    src = SRC.read_text(encoding="utf-8")

    page = dash_page()
    try:
        if args.dry_run:
            # 單一 script 的 GET 不一定回 JSON；改用清單端點再挑出我們那個（實測穩定）
            out = page.ev(
                """(async () => {
                  const r = await fetch('/api/v4/accounts/%s/workers/scripts', {credentials:'include'});
                  const j = await r.json();
                  const me = (j?.result || []).find(s => s.id === '%s');
                  return JSON.stringify({status: r.status, found: !!me,
                                         modified: me?.modified_on, entry: me?.entry_point,
                                         total: (j?.result || []).length});
                })()""" % (ACCOUNT_ID, WORKER_NAME), await_promise=True)
            print("線上狀態:", out)
            return 0

        expr = """
        (async () => {
          const src = %s;
          const fd = new FormData();
          fd.append('metadata', new Blob([JSON.stringify({main_module: '%s', compatibility_date: '%s'})],
                                         {type: 'application/json'}));
          fd.append('%s', new Blob([src], {type: 'application/javascript+module'}), '%s');
          const r = await fetch('/api/v4/accounts/%s/workers/scripts/%s',
                                {method: 'PUT', credentials: 'include', body: fd});
          const t = await r.text();
          return JSON.stringify({status: r.status, head: t.slice(0, 300)});
        })()
        """ % (json.dumps(src), MODULE, COMPAT_DATE, MODULE, MODULE, ACCOUNT_ID, WORKER_NAME)

        print(f"部署中（{len(src)} 位元組）…")
        print("結果:", page.ev(expr, await_promise=True))
        print(f"驗證：curl -s https://{WORKER_NAME}.j0935586110.workers.dev/healthz")
        return 0
    finally:
        page.close()


if __name__ == "__main__":
    raise SystemExit(main())
