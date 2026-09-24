"""guild_bridge 的離線測試（完全不打真站台）。

鐵律：測試絕不連網。所有 HTTP 入口（`nodebb_client._OPEN` 與 `bridge._OPEN`）
都被換成「一被呼叫就炸」的守門，並記錄呼叫；任何一條測試真的想打出去都會立刻失敗。
前車之鑑：舊的 nodebb-mcp 測試真的把文章發到正式站，所以這裡連 REST 入口也一起封死。
"""
from __future__ import annotations

import json
import sys
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "nodebb-mcp")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import nodebb_client  # noqa: E402
from worker.guild_bridge import bridge  # noqa: E402


def make_quest(**overrides) -> dict:
    quest = {
        "id": "quest-0001",
        "title": "重設客戶機瀏覽器首頁",
        "description": "把首頁重設為公司內網入口。",
        "status": "released",
        "category": "magic_tech",
        "reward_g_coin": 12,
        "agent_id": "hermes-jianwei",
        "model": "deepseek-flash",
        "evidence_url": "https://example.test/evidence/0001",
    }
    quest.update(overrides)
    return quest


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """封死所有 HTTP 入口並記錄呼叫；順便清掉會影響行為的環境變數。"""
    calls: list = []

    def boom(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("測試不得連網（HTTP 入口被封死）")

    monkeypatch.setattr(nodebb_client, "_OPEN", boom)
    monkeypatch.setattr(bridge, "_OPEN", boom)
    for var in ("GUILD_DRY_RUN", "NODEBB_DRY_RUN", "NODEBB_TOKEN",
                "GUILD_SUPABASE_URL", "GUILD_SUPABASE_SERVICE_KEY"):
        monkeypatch.delenv(var, raising=False)
    return calls


class CountingClient:
    """假的 NodeBB client：記錄 create_topic 被呼叫的內容，且絕不連網。"""

    token = "fake-token"
    base = "https://forum.test"

    def __init__(self):
        self.calls: list = []

    def create_topic(self, cid, title, content):
        self.calls.append({"cid": cid, "title": title, "content": content})
        return {"response": {"tid": 42, "title": title}}


# ---------- B1：quest 驗證與署名區塊 ----------

def test_quest_requires_signature_fields():
    for missing in ("agent_id", "model", "evidence_url", "id"):
        bad = make_quest()
        bad.pop(missing)
        with pytest.raises(bridge.QuestError):
            bridge.Quest.from_dict(bad)


def test_quest_without_signature_block_is_rejected():
    # 一則沒有署名區塊的貼文不可被當成 bridge 產物
    with pytest.raises(bridge.QuestError):
        bridge.parse_signature("這是一篇沒有署名區塊的貼文")


def test_rendered_topic_has_machine_readable_signature():
    quest = bridge.Quest.from_dict(make_quest())
    title, body = bridge.render_topic(quest)
    sig = bridge.parse_signature(body)
    assert sig == {
        "agent_id": "hermes-jianwei",
        "model": "deepseek-flash",
        "quest_id": "quest-0001",
        "evidence_url": "https://example.test/evidence/0001",
    }
    assert "🤖" in body
    assert title.startswith("[agent]")


def test_unfinished_quest_is_rejected():
    with pytest.raises(bridge.QuestError):
        bridge.Quest.from_dict(make_quest(status="accepted"))


# ---------- B2：dry-run 絕不送網 ----------

def test_network_guard_actually_bites(offline):
    """證明封印有效：真的走一次 client，一定撞到守門（而不是真的連出去）。"""
    client = nodebb_client.NodeBB(base="https://forum.test", token="fake")
    with pytest.raises(AssertionError):
        client.recent(1)
    assert len(offline) == 1  # 守門確實被觸發並記錄


def test_dry_run_sends_nothing_via_env(monkeypatch, capsys, tmp_path, offline):
    monkeypatch.setenv("GUILD_DRY_RUN", "1")
    quest = bridge.Quest.from_dict(make_quest())
    result = bridge.process_quest(quest, cid=2, state_path=tmp_path / "state.json")
    assert result["status"] == "dry_run"
    assert offline == []  # 網路守門從未被觸發
    out = capsys.readouterr().out
    assert "quest-0001" in out and "cid" in out


def test_dry_run_honours_nodebb_env(monkeypatch, tmp_path, offline):
    monkeypatch.setenv("NODEBB_DRY_RUN", "1")
    client = CountingClient()
    quest = bridge.Quest.from_dict(make_quest())
    result = bridge.process_quest(quest, cid=7, state_path=tmp_path / "state.json",
                                  client=client)
    assert result["status"] == "dry_run"
    assert client.calls == []  # 連 client 都沒被呼叫
    assert offline == []


def test_dry_run_flag_prints_exact_payload(capsys, tmp_path):
    quest = bridge.Quest.from_dict(make_quest())
    bridge.process_quest(quest, cid=9, dry_run=True, state_path=tmp_path / "state.json")
    out = capsys.readouterr().out
    assert '"cid": 9' in out
    assert '"title": "[agent] 重設客戶機瀏覽器首頁"' in out


# ---------- B3：冪等 ----------

def test_same_quest_not_double_posted(tmp_path, offline):
    state = tmp_path / "state.json"
    quest = bridge.Quest.from_dict(make_quest())
    client = CountingClient()
    first = bridge.process_quest(quest, cid=2, state_path=state, client=client)
    second = bridge.process_quest(quest, cid=2, state_path=state, client=client)
    assert first["status"] == "posted"
    assert second["status"] == "skipped"
    assert len(client.calls) == 1  # 只發一次
    assert offline == []


def test_dry_run_does_not_poison_idempotency(tmp_path):
    state = tmp_path / "state.json"
    quest = bridge.Quest.from_dict(make_quest())
    bridge.process_quest(quest, cid=2, dry_run=True, state_path=state)
    assert not state.exists()  # 預演不可記成「已處理」


def test_all_posts_go_through_one_entry_point(monkeypatch, tmp_path):
    seen: dict = {}

    def fake_post(cid, title, content, **kwargs):
        seen["cid"] = cid
        seen["kwargs"] = kwargs
        return {"response": {"tid": 1}}

    monkeypatch.setattr(bridge, "post_forum_topic", fake_post)
    quest = bridge.Quest.from_dict(make_quest())
    result = bridge.process_quest(quest, cid=3, state_path=tmp_path / "s.json")
    assert result["status"] == "posted"
    assert seen["cid"] == 3  # 唯一的寫入入口確實被走過


# ---------- B4：網路錯誤不可被吞 ----------

def test_network_error_is_nonzero(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("NODEBB_TOKEN", "fake-token")
    quest_file = tmp_path / "quest.json"
    quest_file.write_text(json.dumps(make_quest()), encoding="utf-8")
    state = tmp_path / "state.json"

    def boom(*args, **kwargs):
        raise urllib.error.URLError("boom-network-down")

    monkeypatch.setattr(nodebb_client, "_OPEN", boom)
    rc = bridge.main(["--quest-file", str(quest_file), "--cid", "2", "--state", str(state)])
    assert rc != 0
    err = capsys.readouterr().err
    assert "boom-network-down" in err or "連不上" in err
    assert not state.exists()  # 失敗不可寫入冪等狀態


# ---------- B5：載入器 ----------

def test_load_quest_file(tmp_path):
    quest_file = tmp_path / "quest.json"
    quest_file.write_text(json.dumps(make_quest()), encoding="utf-8")
    quest = bridge.load_quest_file(quest_file)
    assert quest.quest_id == "quest-0001"


def test_load_quest_rest_requires_env(offline):
    with pytest.raises(bridge.BridgeError) as excinfo:
        bridge.load_quest_rest("quest-0001")
    assert "GUILD_SUPABASE" in str(excinfo.value)
    assert offline == []


def test_load_quest_rest_parses_row_offline(monkeypatch):
    monkeypatch.setenv("GUILD_SUPABASE_URL", "https://fake.supabase.co")
    monkeypatch.setenv("GUILD_SUPABASE_SERVICE_KEY", "svc-fake")
    row = make_quest()

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return json.dumps([row]).encode("utf-8")

    def fake_open(request, timeout=None):
        assert request.get_header("User-agent")  # 一定要帶 UA
        assert "fake.supabase.co" in request.full_url
        return FakeResponse()

    monkeypatch.setattr(bridge, "_OPEN", fake_open)
    quest = bridge.load_quest_rest("quest-0001")
    assert quest.quest_id == "quest-0001"
    assert quest.evidence_url == "https://example.test/evidence/0001"


def test_cli_dry_run_returns_zero(monkeypatch, tmp_path, offline):
    monkeypatch.setenv("GUILD_DRY_RUN", "1")
    quest_file = tmp_path / "quest.json"
    quest_file.write_text(json.dumps(make_quest()), encoding="utf-8")
    rc = bridge.main(["--quest-file", str(quest_file), "--cid", "2",
                      "--state", str(tmp_path / "state.json")])
    assert rc == 0
    assert offline == []
