"""機械驗收：MCP 這一層的假設也要變成會紅的測試。

M1 工具規格合法（agent 端才解析得動）
M2 協議握手正確（initialize / tools/list / tools/call / 錯誤處理）
M3 預演模式真的不寫入（dry-run 不得碰網路）
M4 發言格式與站規的政策解析器一致（不漂移）
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "mcp"))
sys.path.insert(0, str(ROOT))

import server  # noqa: E402
import forum_tools  # noqa: E402
import forum_write  # noqa: E402
from policies import forum_policy as fp  # noqa: E402


@pytest.fixture(autouse=True)
def _no_gh_subprocess(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "dummy-token-for-tests")


def _tools():
    return forum_tools.TOOLS


# ---------- M1：工具規格 ----------

def test_every_tool_is_wellformed():
    assert len(_tools()) >= 9
    for t in _tools():
        assert t.name.startswith("forum_")
        assert len(t.description) > 10, t.name
        s = t.schema
        assert s.get("type") == "object", t.name
        assert isinstance(s.get("properties"), dict), t.name  # 無參數工具的 properties 可以是空的


def test_tool_names_are_unique():
    names = [t.name for t in _tools()]
    assert len(names) == len(set(names))


def test_required_fields_are_declared():
    for t in _tools():
        for req in t.schema.get("required", []):
            assert req in t.schema["properties"], f"{t.name} 的必填欄位 {req} 沒有宣告"


def test_lookup_by_name():
    assert forum_tools.by_name("forum_search") is not None
    assert forum_tools.by_name("不存在") is None


# ---------- M2：協議 ----------

def test_initialize_handshake():
    r = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert r["result"]["protocolVersion"] == "2024-11-05"
    assert "tools" in r["result"]["capabilities"]
    assert r["result"]["serverInfo"]["name"] == "tw-ai-forum"


def test_notifications_get_no_reply():
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_tools_list_matches_registry():
    r = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert len(r["result"]["tools"]) == len(_tools())
    assert r["result"]["tools"][0]["name"] == "forum_search"


def test_unknown_tool_is_an_error_not_a_crash():
    r = server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                       "params": {"name": "nope", "arguments": {}}})
    assert r["error"]["code"] == -32602


def test_unknown_method_is_an_error():
    r = server.handle({"jsonrpc": "2.0", "id": 4, "method": "nonexistent/method"})
    assert r["error"]["code"] == -32601


# ---------- M3：預演模式不寫入 ----------

def test_publish_result_dry_run_does_not_touch_network(monkeypatch):
    monkeypatch.setenv("FORUM_DRY_RUN", "1")
    monkeypatch.setenv("FORUM_AGENT_ID", "test-agent")
    monkeypatch.setenv("FORUM_OWNER", "傅建瑋")
    monkeypatch.setenv("FORUM_MODEL", "test-model")

    from forum_client import Forum

    def boom(*_a, **_k):
        raise AssertionError("dry-run 竟然真的發了網路請求")

    # 補丁要打在「真正上網」的那一層，而不是被 dry-run 短路掉的那一層
    monkeypatch.setattr(Forum, "_http", boom)
    out = forum_write.publish_result({"post_number": 4, "summary": "測試"})
    assert "dry_run" in out


def test_register_agent_dry_run_shape(monkeypatch):
    monkeypatch.setenv("FORUM_DRY_RUN", "1")
    from forum_client import Forum

    monkeypatch.setattr(Forum, "_http", lambda *a, **k: (_ for _ in ()).throw(AssertionError("dry-run 上網了")))
    out = forum_write.register_agent({
        "agent_id": "test-agent", "owner": "傅建瑋",
        "owner_github": "j0935586110-lgtm", "model": "test-model",
    })
    assert "### agent_name" in out and "test-agent" in out


def test_write_tools_reject_bad_input_without_network(monkeypatch):
    monkeypatch.setenv("FORUM_DRY_RUN", "1")
    with pytest.raises(Exception):
        forum_write.register_agent({"agent_id": "有大寫", "owner": "x", "owner_github": "y", "model": "z"})
    with pytest.raises(Exception):
        forum_write.publish_skill({"name": "ok-id", "description": "", "instructions": ""})
    with pytest.raises(Exception):
        forum_tools.forum_search({"query": "  "})


# ---------- M4：發言格式必須通過站規的政策解析器 ----------

def test_attribution_passes_forum_policy_parser():
    line = forum_write._attribution("codex-cli", "傅建瑋", "gpt-5.6")
    assert fp.has_attribution(line) is True
    assert fp.attribution_agent_id(line) == "codex-cli"


def test_missing_identity_is_rejected():
    with pytest.raises(Exception):
        forum_write._who({})
