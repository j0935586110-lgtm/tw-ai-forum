"""skill_sink 的離線測試（完全不打 GitHub、不開 PR）。

`subprocess.run` 一律用測試替身；`--apply` 未給時必須證明它「一次都沒被呼叫」。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "nodebb-mcp")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import nodebb_client  # noqa: E402
from worker.guild_bridge import bridge, skill_sink  # noqa: E402

# 測試用的假憑證一律在執行期組出來，檔案裡不留任何「像真 token」的字面字串
# （避免洩密掃描誤判，也符合「不把 token 寫進 tracked 檔」的鐵律）。
FAKE_SK = "sk-" + "abcdef1234567890"
FAKE_TOKENS = [
    FAKE_SK,
    "ghp_" + "A" * 24,
    "AIza" + "b" * 28,
    "xoxb-" + "1234567890-abcd",
    "-----BEGIN " + "RSA PRIVATE KEY-----",
]


def make_quest(**overrides) -> dict:
    quest = {
        "id": "quest-0002",
        "title": "Fix printer spooler",
        "description": "Restart the print spooler and verify with a test page.",
        "status": "released",
        "category": "combat",
        "agent_id": "hermes-jianwei",
        "model": "deepseek-flash",
        "evidence_url": "https://example.test/evidence/0002",
    }
    quest.update(overrides)
    return quest


def quest_obj(**overrides):
    return bridge.Quest.from_dict(make_quest(**overrides))


def load_frontmatter(text: str) -> dict:
    """用真 YAML 解析 frontmatter；環境若沒裝 PyYAML 則退回最小解析（CI 只裝 pytest）。"""
    front = text.split("---", 2)[1]
    try:
        import yaml  # type: ignore
    except ImportError:
        yaml = None
    if yaml is not None:
        return yaml.safe_load(front)
    meta: dict = {}
    for line in front.splitlines():
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*):\s*(\S.*)$", line)
        if match:
            raw = match.group(2).strip()
            if raw[:1] == '"' and raw[-1:] == '"':
                raw = json.loads(raw)
            meta[match.group(1)] = raw
    return meta


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("測試不得連網")

    monkeypatch.setattr(nodebb_client, "_OPEN", boom)
    monkeypatch.setattr(bridge, "_OPEN", boom)


# ---------- S1：Hermes frontmatter ----------

def test_frontmatter_is_valid_yaml_and_use_when():
    text = skill_sink.render_skill(quest_obj())
    assert text.startswith("---\n")
    meta = load_frontmatter(text)
    assert meta["name"]
    assert meta["description"].startswith("Use when ")


def test_skill_name_is_a_valid_slug():
    text = skill_sink.render_skill(quest_obj())
    meta = load_frontmatter(text)
    assert skill_sink.SLUG_RE.match(meta["name"])


def test_rendered_skill_contains_quest_provenance():
    text = skill_sink.render_skill(quest_obj())
    assert "quest-0002" in text
    assert "https://example.test/evidence/0002" in text
    assert "## 來源" in text


# ---------- S2：憑證拒絕 ----------

def test_fake_token_is_refused():
    with pytest.raises(skill_sink.SkillSinkError):
        skill_sink.render_skill(quest_obj(description=f"token {FAKE_SK} leaked"))


@pytest.mark.parametrize("token", FAKE_TOKENS)
def test_all_credential_shapes_detected(token):
    assert skill_sink.find_credential(token) is not None


def test_clean_skill_has_no_credential():
    assert skill_sink.find_credential(skill_sink.render_skill(quest_obj())) is None


# ---------- S3：PR 指令，預設只印不跑 ----------

def test_pr_command_shape():
    cmd = skill_sink.pr_create_command(repo="o/r", branch="b", title="t", body="x")
    assert cmd[:3] == ["gh", "pr", "create"]
    assert "--repo" in cmd and "o/r" in cmd
    assert "--title" in cmd and "t" in cmd


def test_apply_absent_does_not_spawn_subprocess(monkeypatch, capsys):
    calls: list = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append((a, k)))
    result = skill_sink.open_pr(repo="o/r", branch="b", title="t", body="x", apply=False)
    assert calls == []
    assert result["applied"] is False
    assert "gh pr create" in capsys.readouterr().out


def test_apply_true_spawns_exactly_once(monkeypatch):
    calls: list = []

    class Proc:
        returncode = 0
        stdout = "https://github.com/o/r/pull/1"
        stderr = ""

    def runner(cmd, **kwargs):
        calls.append(cmd)
        return Proc()

    result = skill_sink.open_pr(repo="o/r", branch="b", title="t", body="x",
                                apply=True, runner=runner)
    assert len(calls) == 1
    assert calls[0][:3] == ["gh", "pr", "create"]
    assert result["applied"] is True
    assert result["returncode"] == 0


# ---------- S4：CLI ----------

def test_cli_without_apply_prints_only(monkeypatch, tmp_path, capsys):
    quest_file = tmp_path / "q.json"
    quest_file.write_text(json.dumps(make_quest()), encoding="utf-8")
    calls: list = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append((a, k)))
    rc = skill_sink.main(["--quest-file", str(quest_file), "--repo", "o/r"])
    assert rc == 0
    assert calls == []
    out = capsys.readouterr().out
    assert "---" in out and "gh pr create" in out


def test_cli_refuses_credential(monkeypatch, tmp_path, capsys):
    quest_file = tmp_path / "q.json"
    quest_file.write_text(
        json.dumps(make_quest(description=FAKE_SK)), encoding="utf-8")
    rc = skill_sink.main(["--quest-file", str(quest_file), "--repo", "o/r"])
    assert rc != 0
    assert "憑證" in capsys.readouterr().err
