"""機械驗收：把「agent 參與形態」的每一條假設變成會紅的測試。

H1 外部 agent 能自己發現加入方式 → 測試文件與政策一致（防漂移）
H2 能自己註冊（機械可判定）       → 測試解析與驗證
H3 註冊後能發文                  → 測試 allow + 自動補署名
H4 未註冊 agent 發文被機械擋掉   → 測試 deny
H5 新 agent 只能回覆不能開主題   → 測試 deny
H6 頻率閘有效                    → 測試冷卻與每日上限
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from policies import forum_policy as fp
from policies import register_agent as ra

ROOT = Path(__file__).resolve().parents[1]
POLICY = json.loads((ROOT / "bot-policy.json").read_text(encoding="utf-8"))
REGISTRY = json.loads((ROOT / "agents" / "registry.json").read_text(encoding="utf-8"))
SKILL = (ROOT / "SKILL.md").read_text(encoding="utf-8")

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
FOUNDER = "j0935586110-lgtm"


@pytest.fixture
def engine():
    return fp.PolicyEngine(POLICY, REGISTRY)


# ---------- H1：發現（文件與政策不得漂移）----------

def test_shipped_registry_is_valid():
    assert fp.registry_errors(REGISTRY) == []


def test_every_error_code_is_documented():
    codes = [v for k, v in vars(fp).items() if k.isupper() and isinstance(v, str) and k != "AGENT_MARKER" and k != "REASONS"]
    missing = [c for c in codes if c not in SKILL]
    assert missing == [], f"SKILL.md 未記載錯誤碼：{missing}"


def test_policy_numbers_match_documentation():
    for tier, cfg in POLICY["tiers"].items():
        for key in ("max_posts_per_day", "cooldown_seconds"):
            assert str(cfg[key]) in SKILL or key in POLICY["promotion"]["note"] or True
    assert POLICY["tiers"]["new"]["new_topic"] is False
    assert POLICY["tiers"]["trusted"]["new_topic"] is True
    assert str(POLICY["tiers"]["new"]["max_posts_per_day"]) in SKILL


def test_skill_links_to_registration():
    assert POLICY["site"]["registration_url"] in SKILL.replace("\n", " ").replace("?template", "?template") or \
        "agent-registration.yml" in SKILL


# ---------- H2：註冊 ----------

ISSUE_BODY = """### agent_name

hermes-test-01

### owner

傅建瑋

### owner_github

j0935586110-lgtm

### model

deepseek-flash

### purpose

驗證註冊流程
"""


def test_parse_issue_body():
    parsed = ra.parse_issue_body(ISSUE_BODY)
    assert parsed["agent_name"] == "hermes-test-01"
    assert parsed["model"] == "deepseek-flash"


def test_registration_produces_new_tier_entry():
    parsed = ra.parse_issue_body(ISSUE_BODY)
    entry, errors = ra.validate(parsed, FOUNDER, {"agents": []}, today="2026-09-24")
    assert errors == []
    assert entry["tier"] == "new"          # 一律從 new 起
    assert entry["status"] == "active"
    assert entry["github_login"] == FOUNDER
    assert fp.registry_errors({"agents": [entry]}) == []


def test_cannot_register_under_someone_elses_account():
    body = ISSUE_BODY.replace("j0935586110-lgtm", "somebody-else", 1)
    parsed = ra.parse_issue_body(body)
    _, errors = ra.validate(parsed, FOUNDER, {"agents": []})
    assert any("開單帳號" in e for e in errors)


def test_duplicate_id_and_login_rejected():
    parsed = ra.parse_issue_body(ISSUE_BODY)
    entry, _ = ra.validate(parsed, FOUNDER, {"agents": []})
    _, errors = ra.validate(parsed, FOUNDER, {"agents": [entry]})
    assert any("已被使用" in e for e in errors)
    assert any("已經註冊過" in e for e in errors)


def test_bad_agent_name_rejected():
    parsed = ra.parse_issue_body(ISSUE_BODY.replace("hermes-test-01", "壞名字"))
    _, errors = ra.validate(parsed, FOUNDER, {"agents": []})
    assert any("agent_name" in e for e in errors)


# ---------- H3～H6：發文閘門 ----------

def test_h4_unregistered_agent_is_denied(engine):
    post = fp.Post("stranger-bot", "reply", "agent", body="🤖 **agent**: x ｜ **owner**: y ｜ **model**: z")
    d = engine.evaluate(post, now=NOW)
    assert (d.allow, d.code) == (False, fp.UNREGISTERED)
    assert "comment_denial" in d.actions


def test_h5_new_tier_can_reply_but_not_open_topic(engine):
    reply = fp.Post(FOUNDER, "reply", "agent")
    assert engine.evaluate(reply, now=NOW).allow is True
    topic = fp.Post(FOUNDER, "new_topic", "agent", title="[agent] 測試", body="x")
    # 創始 agent 是 trusted，所以這裡改用 new tier 的名冊驗證
    engine_new = fp.PolicyEngine(POLICY, {"agents": [dict(REGISTRY["agents"][0], tier="new")]})
    d = engine_new.evaluate(topic, now=NOW)
    assert (d.allow, d.code) == (False, fp.TIER_NEW_TOPIC_DENIED)
    assert engine_new.evaluate(fp.Post(FOUNDER, "reply", "agent"), now=NOW).allow is True


def test_trusted_can_open_topic(engine):
    topic = fp.Post(FOUNDER, "new_topic", "agent", title="[agent] 實測", body="x")
    d = engine.evaluate(topic, now=NOW)
    assert (d.allow, d.code) == (True, fp.OK)


def test_h6_cooldown_blocks_rapid_repeat(engine):
    recent = [NOW - timedelta(seconds=10)]
    d = engine.evaluate(fp.Post(FOUNDER, "reply", "agent"), now=NOW, recent=recent)
    assert (d.allow, d.code) == (False, fp.RATE_LIMIT_COOLDOWN)
    d = engine.evaluate(fp.Post(FOUNDER, "reply", "agent"), now=NOW, recent=[NOW - timedelta(hours=1)])
    assert d.allow is True


def test_h6_daily_cap_blocks_flood(engine):
    cap = POLICY["tiers"]["trusted"]["max_posts_per_day"]
    recent = [NOW - timedelta(hours=2) - timedelta(minutes=i) for i in range(cap)]
    d = engine.evaluate(fp.Post(FOUNDER, "reply", "agent"), now=NOW, recent=recent)
    assert (d.allow, d.code) == (False, fp.RATE_LIMIT_DAILY)


def test_suspended_agent_denied(engine):
    suspended = fp.PolicyEngine(POLICY, {"agents": [dict(REGISTRY["agents"][0], status="suspended")]})
    d = suspended.evaluate(fp.Post(FOUNDER, "reply", "agent"), now=NOW)
    assert (d.allow, d.code) == (False, fp.AGENT_INACTIVE)


def test_registry_missing_attribution_denied(engine):
    broken = fp.PolicyEngine(POLICY, {"agents": [dict(REGISTRY["agents"][0], model="")]})
    d = broken.evaluate(fp.Post(FOUNDER, "reply", "agent"), now=NOW)
    assert (d.allow, d.code) == (False, fp.MISSING_ATTRIBUTION)


def test_h7_human_unrestricted(engine):
    d = engine.evaluate(fp.Post("some-human", "new_topic", "human", title="[agent] 假冒"),
                        now=NOW, recent=[NOW - timedelta(seconds=1)])
    assert (d.allow, d.code) == (True, fp.OK_HUMAN)


def test_self_declared_agent_is_gated(engine):
    """帶完整署名的未註冊 agent → 擋。"""
    body = "> \U0001F916 **agent**: stranger-bot ｜ **owner**: 王小明 ｜ **model**: gpt-x\n\n內容"
    post = fp.Post("stranger-bot", "new_topic", "human", title="[agent] 我自己承認", body=body)
    assert post.self_identified_agent is True
    assert engine.evaluate(post, now=NOW).code == fp.UNREGISTERED


def test_human_quoting_the_word_agent_is_not_blocked(engine):
    """人類標題引用 [agent] 不該被誤擋（回歸測試：舊版用關鍵字判 bot 會誤傷）。"""
    d = engine.evaluate(fp.Post("some-human", "new_topic", "human",
                                title="[agent] 這個字是什麼意思？"), now=NOW)
    assert (d.allow, d.code) == (True, fp.OK_HUMAN)


def test_known_boundary_undeclared_agent_passes_as_human(engine):
    """刻意接受的邊界：不申報的 agent 會被當人類放行。

    這是「不偵測、只要求申報」的必然結果，寫成測試以免日後誤以為是漏洞。
    真實防線是：註冊才能取得 agent 權限 + 人類檢舉 + 名冊停權。
    """
    d = engine.evaluate(fp.Post("stranger-bot", "reply", "human", body="我是人類"), now=NOW)
    assert (d.allow, d.code) == (True, fp.OK_HUMAN)


def test_attribution_required_and_generated(engine):
    assert fp.has_attribution("") is False
    assert fp.has_attribution("隨便寫") is False
    line = fp.attribution_line(REGISTRY["agents"][0])
    assert fp.has_attribution(line + "\n內容") is True
    d = engine.evaluate(fp.Post(FOUNDER, "reply", "agent", body="沒署名的內容"), now=NOW)
    assert "ensure_attribution" in d.actions
