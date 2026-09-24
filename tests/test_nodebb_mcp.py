"""NodeBB MCP server 的測試（完全離線，不打真站台）。

為什麼要離線：CI 上不該依賴外部服務，也不該在測試裡真的發文。
所以這裡用假的 NodeBB client 替身，只驗證「工具定義、錯誤處理、預演、協議」。
真站台的驗證靠 nodebb-mcp/README.md 裡的手動實測清單。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "nodebb-mcp"))

import nodebb_client  # noqa: E402
import nodebb_tools  # noqa: E402


def _load_unique(name: str, path: Path):
    """用獨立模組名載入 server.py。

    為什麼不能直接 `import server`：mcp/server.py 也叫 server，同一個 pytest
    行程裡第二個 import 會拿到快取的第一個檔案（模組名撞名）。
    """
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


server = _load_unique("nodebb_mcp_server", ROOT / "nodebb-mcp" / "server.py")


class FakeNodeBB:
    """假的 NodeBB client：只回傳固定的假資料，且絕不連網。"""

    token = "fake-token"
    base = "https://example.test"

    def __init__(self, *a, **k):
        pass

    def config(self):
        return {"siteTitle": "測試站", "description": "說明"}

    def categories(self):
        return [{"cid": 2, "name": "General Discussion", "topic_count": 3, "description": "聊天區"}]

    def recent(self, limit=20):
        return [{"tid": 2, "title": "標題", "postcount": 2, "user": {"username": "u"}}]

    def search(self, query, limit=10):
        return {
            "topics": [{"tid": 2, "title": "標題", "postcount": 2, "user": {"username": "u"}}],
            "posts": [{"tid": 2, "content": "<p>hi</p>", "user": {"username": "u"},
                       "topic": {"title": "標題"}}],
            "users": [{"username": "u"}],
        }

    def topic(self, tid):
        return {
            "tid": 2, "title": "標題", "postcount": 2, "category": {"name": "General Discussion"},
            "posts": [{"pid": 1, "user": {"username": "u"}, "content": "<p>hello<br>world</p>"}],
        }

    def user(self, slug):
        return {"uid": 1, "username": "u", "postcount": 3, "reputation": 1,
                "joindateISO": "2026-01-01T00:00:00.000Z", "aboutme": "<p>bio</p>"}

    def me(self):
        return {"uid": 1, "username": "u", "postcount": 3, "reputation": 1}

    def create_topic(self, cid, title, content):
        return {"response": {"tid": 9, "title": title}}

    def reply(self, tid, content):
        return {"response": {"tid": tid, "url": "https://example.test/post/9"}}


def test_tools_never_hit_network():
    """把每個工具都跑一次：只要有任何一個真的想連網，上面的守門就會炸掉。"""
    cases = [("forum_create_topic", {"cid": 2, "title": "t", "content": "c"}),
             ("forum_reply", {"tid": 2, "content": "c"})]
    for name, args in cases:
        out = nodebb_tools.by_name(name).run(args)
        assert isinstance(out, str) and out.strip()


class ExplodingNodeBB(FakeNodeBB):
    """只要被呼叫就爆炸 —— 用來證明預演模式真的沒碰網路。"""

    def create_topic(self, *a, **k):
        raise AssertionError("預演模式不該呼叫 create_topic")

    def reply(self, *a, **k):
        raise AssertionError("預演模式不該呼叫 reply")


@pytest.fixture(autouse=True)
def _fake(monkeypatch):
    """把整個 NodeBB 類別換成假的，並封死網路。

    教訓：一開始只換了 `_client`，但寫入工具當時是直接呼叫 `NodeBB()`，
    結果測試真的把文章發到正式站上（本機「通過」、CI 因為沒 token 而失敗）。
    現在連 urlopen 都封死 —— 任何測試只要想連網就會炸。
    """
    monkeypatch.setattr(nodebb_tools, "NodeBB", FakeNodeBB)
    monkeypatch.setattr(nodebb_tools, "_client", lambda: FakeNodeBB())
    monkeypatch.setattr(nodebb_tools, "DRY_RUN", False)

    def _no_network(*a, **k):
        raise AssertionError("測試不得連網（真的打出去會被這道守門擋下）")

    monkeypatch.setattr(nodebb_client, "_OPEN", _no_network)
    yield


# ---------- N1：工具定義 ----------

def test_tool_specs_are_valid():
    names = [t.name for t in nodebb_tools.TOOLS]
    assert len(names) == len(set(names)), "工具名稱不可重複"
    assert len(names) == 9
    for t in nodebb_tools.TOOLS:
        s = t.spec()
        assert set(s) == {"name", "description", "inputSchema"}
        assert s["name"].startswith("forum_")
        assert len(s["description"]) > 5
        assert s["inputSchema"]["type"] == "object"
        assert isinstance(s["inputSchema"]["properties"], dict)


def test_by_name():
    assert nodebb_tools.by_name("forum_health") is not None
    assert nodebb_tools.by_name("nope") is None


# ---------- N2：每個工具都能跑出東西 ----------

@pytest.mark.parametrize("tool,args", [
    ("forum_health", {}),
    ("forum_list_categories", {}),
    ("forum_recent", {"limit": 5}),
    ("forum_search", {"query": "x"}),
    ("forum_read_topic", {"tid": 2}),
    ("forum_read_user", {"slug": "u"}),
    ("forum_whoami", {}),
    ("forum_create_topic", {"cid": 2, "title": "t", "content": "c"}),
    ("forum_reply", {"tid": 2, "content": "c"}),
])
def test_every_tool_returns_text(tool, args):
    out = nodebb_tools.by_name(tool).run(args)
    assert isinstance(out, str) and out.strip()


def test_read_topic_shows_author_and_clean_text():
    out = nodebb_tools.by_name("forum_read_topic").run({"tid": 2})
    assert "作者：u" in out          # 作者要從第一篇推出來（頂層沒有 user 欄位）
    assert "<p>" not in out          # HTML 要被轉掉
    assert "hello" in out and "world" in out


def test_missing_required_args_are_friendly():
    assert "請給 query" in nodebb_tools.by_name("forum_search").run({})
    assert "需要 cid" in nodebb_tools.by_name("forum_create_topic").run({})
    assert "請給 tid" in nodebb_tools.by_name("forum_read_topic").run({})


# ---------- N3：預演模式真的不碰網路 ----------

def test_dry_run_does_not_touch_network(monkeypatch):
    monkeypatch.setattr(nodebb_tools, "_client", lambda: ExplodingNodeBB())
    monkeypatch.setattr(nodebb_tools, "DRY_RUN", True)
    out1 = nodebb_tools.by_name("forum_create_topic").run({"cid": 2, "title": "t", "content": "c"})
    out2 = nodebb_tools.by_name("forum_reply").run({"tid": 2, "content": "c"})
    assert out1.startswith("🧪") and out2.startswith("🧪")


# ---------- N4：HTML 轉純文字 ----------

def test_html_to_text():
    assert nodebb_client.html_to_text("<p>a<br>b</p>") == "a\nb"
    assert nodebb_client.html_to_text("<p>x &amp; y &lt;z&gt;</p>") == "x & y <z>"
    assert nodebb_client.html_to_text("") == ""


# ---------- N5：錯誤訊息要對 agent 有用 ----------

def test_401_message_mentions_token():
    msg = nodebb_client.NodeBB._explain(401, '{"status":{"message":"not-authorized"}}', "/api/search")
    assert "token" in msg
    assert "401" in msg


def test_404_message_is_actionable():
    msg = nodebb_client.NodeBB._explain(404, "{}", "/api/topic/999")
    assert "找不到" in msg and "/api/topic/999" in msg


def test_client_requires_token_for_writes():
    c = nodebb_client.NodeBB(base="https://example.test", token=None)
    c.token = None
    with pytest.raises(nodebb_client.NodeBBError):
        c.create_topic(1, "t", "c")


# ---------- N6：MCP 協議 ----------

def test_protocol_initialize_and_tools_list():
    r = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert r["result"]["serverInfo"]["name"] == "nodebb-forum"
    assert r["result"]["protocolVersion"] == server.PROTOCOL_VERSION

    r = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert len(r["result"]["tools"]) == 9

    r = server.handle({"jsonrpc": "2.0", "id": 3, "method": "ping"})
    assert r["result"] == {}


def test_protocol_tools_call_and_error_paths():
    r = server.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                       "params": {"name": "forum_health", "arguments": {}}})
    assert r["result"]["isError"] is False
    assert "站台" in r["result"]["content"][0]["text"]

    r = server.handle({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                       "params": {"name": "nope", "arguments": {}}})
    assert r["error"]["code"] == -32602

    r = server.handle({"jsonrpc": "2.0", "id": 6, "method": "no/such/method"})
    assert r["error"]["code"] == -32601

    # 通知（沒有 id）不該有回應
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_tool_exception_is_reported_not_crashed(monkeypatch):
    def boom(_):
        raise nodebb_client.NodeBBError("壞掉了")
    monkeypatch.setattr(nodebb_tools, "by_name", lambda n: nodebb_tools.Tool(
        "forum_x", "d", {"type": "object", "properties": {}}, boom))
    r = server.handle({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                       "params": {"name": "forum_x", "arguments": {}}})
    assert r["result"]["isError"] is True
    assert "壞掉了" in r["result"]["content"][0]["text"]


def test_server_specs_serializable():
    # 工具的 spec 一定要能被 JSON 序列化（MCP 傳輸用）
    json.dumps([t.spec() for t in nodebb_tools.TOOLS], ensure_ascii=False)
