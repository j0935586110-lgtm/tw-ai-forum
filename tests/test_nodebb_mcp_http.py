"""HTTP 版 MCP（給雲端 agent）的測試 — 完全離線。

做法：把 http_server 載進來，用假的 NodeBB client，起一個真的 HTTP 伺服器在
127.0.0.1 的隨機埠，然後用 urllib 打自己（loopback，不碰外網）。
同時封死 nodebb_client 的 urlopen，任何真的想連外網的呼叫都會直接爆掉。
"""
from __future__ import annotations

import importlib.util
import json
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "nodebb-mcp"))

import nodebb_client  # noqa: E402
import nodebb_tools  # noqa: E402


def _load_unique(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


http_server = _load_unique("nodebb_mcp_http_server", ROOT / "nodebb-mcp" / "http_server.py")


class FakeNodeBB:
    """假的 NodeBB；寫入會被記錄下來，用來驗證「沒帶存取碼時不該寫入」。"""

    writes: list = []

    def __init__(self, *a, **k):
        self.base = "https://fake.test"
        self.token = "fake"

    def config(self):
        return {"siteTitle": "Fake", "description": "d"}

    def categories(self):
        return [{"cid": 2, "name": "G", "topic_count": 1, "description": "d"}]

    def recent(self, limit=20):
        return [{"tid": 2, "title": "T", "postcount": 1, "user": {"username": "u"}}]

    def search(self, query, limit=10):
        return {"topics": [], "posts": []}

    def topic(self, tid):
        return {"tid": tid, "title": "T", "postcount": 1, "category": {"name": "G"},
                "posts": [{"pid": 1, "user": {"username": "u"}, "content": "<p>hi</p>"}]}

    def user(self, slug):
        return {"uid": 1, "username": slug, "postcount": 1, "reputation": 0}

    def me(self):
        return {"uid": 1, "username": "u", "postcount": 1, "reputation": 0}

    def create_topic(self, cid, title, content):
        FakeNodeBB.writes.append(("create_topic", cid, title))
        return {"response": {"tid": 9}}

    def reply(self, tid, content):
        FakeNodeBB.writes.append(("reply", tid))
        return {"response": {"tid": tid}}


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch, tmp_path):
    """① 換掉 NodeBB 類別 ② 封死對外網路 ③ 用暫存的存取碼檔。"""
    FakeNodeBB.writes = []
    monkeypatch.setattr(nodebb_tools, "NodeBB", FakeNodeBB)
    monkeypatch.setattr(nodebb_tools, "_client", lambda: FakeNodeBB())
    monkeypatch.setattr(nodebb_tools, "DRY_RUN", False)

    def _no_net(*a, **k):
        raise AssertionError("測試不得連外網")

    monkeypatch.setattr(nodebb_client, "_OPEN", _no_net)

    tok = tmp_path / "access-token"
    tok.write_text("secret-token-123\n")
    monkeypatch.setattr(http_server, "ACCESS_TOKEN_FILE", str(tok))
    yield


@pytest.fixture(scope="module")
def base_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), http_server.Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    host, port = server.server_address[:2]
    yield f"http://127.0.0.1:{port}"
    server.shutdown()
    server.server_close()


def _post(base, payload, token=None, path="/mcp"):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(base + path, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status, json.loads(r.read().decode())


def _get(base, path):
    req = urllib.request.Request(base + path, method="GET")
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status, r.read().decode()


def test_healthz(base_url):
    st, body = _get(base_url, "/healthz")
    assert st == 200
    j = json.loads(body)
    assert j["ok"] is True and j["tools"] == len(nodebb_tools.TOOLS)


def test_skill_md(base_url):
    st, body = _get(base_url, "/skill.md")
    assert st == 200 and "POST /mcp" in body and "Bearer" in body


def test_initialize_and_tools_list(base_url):
    st, j = _post(base_url, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert st == 200 and j["result"]["protocolVersion"]
    st, j = _post(base_url, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert len(j["result"]["tools"]) == len(nodebb_tools.TOOLS)


def test_public_tool_without_token(base_url):
    st, j = _post(base_url, {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                             "params": {"name": "forum_list_categories", "arguments": {}}})
    assert st == 200 and j["result"]["isError"] is False
    assert "cid=2" in j["result"]["content"][0]["text"]


def test_write_without_token_is_refused_and_does_not_write(base_url):
    st, j = _post(base_url, {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                             "params": {"name": "forum_create_topic",
                                        "arguments": {"cid": 2, "title": "x", "content": "y"}}})
    text = j["result"]["content"][0]["text"]
    assert j["result"]["isError"] is True
    assert "Bearer" in text
    assert FakeNodeBB.writes == []  # 最關鍵：沒帶存取碼時不能真的寫入


def test_write_with_token_works(base_url):
    st, j = _post(base_url, {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                             "params": {"name": "forum_reply",
                                        "arguments": {"tid": 2, "content": "hello"}}},
                  token="secret-token-123")
    assert j["result"]["isError"] is False
    assert FakeNodeBB.writes == [("reply", 2)]


def test_wrong_token_refused(base_url):
    st, j = _post(base_url, {"jsonrpc": "2.0", "id": 6, "method": "tools/call",
                             "params": {"name": "forum_whoami", "arguments": {}}},
                  token="wrong")
    assert j["result"]["isError"] is True


def test_unknown_path_and_bad_json_and_unknown_method(base_url):
    try:
        _post(base_url, {"jsonrpc": "2.0", "id": 7, "method": "ping"}, path="/nope")
        raise AssertionError("應該要 404")
    except urllib.error.HTTPError as e:
        assert e.code == 404 and "POST /mcp" in e.read().decode()

    try:
        req = urllib.request.Request(base_url + "/mcp", data=b"{not json", method="POST")
        req.add_header("Content-Type", "application/json")
        urllib.request.urlopen(req, timeout=10)
        raise AssertionError("應該要 400")
    except urllib.error.HTTPError as e:
        assert e.code == 400

    st, j = _post(base_url, {"jsonrpc": "2.0", "id": 8, "method": "no/such"})
    assert j["error"]["code"] == -32601


def test_batch_request(base_url):
    st, j = _post(base_url, [
        {"jsonrpc": "2.0", "id": 9, "method": "ping"},
        {"jsonrpc": "2.0", "id": 10, "method": "tools/list"},
    ])
    assert st == 200 and len(j) == 2
