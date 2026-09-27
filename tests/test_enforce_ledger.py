"""機械驗收：政策閘門的 ledger 寫入。

對應對抗性審查的兩個 MAJOR：
- MAJOR-1（真）：``--dry-run`` 不得寫入 ledger。
- MAJOR-2：並行附加不得遺失或交錯 JSON 行。
"""

from __future__ import annotations

import hashlib
import json
import multiprocessing as mp
from pathlib import Path

from policies import enforce

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "agents" / "registry.json"
BOT_POLICY = ROOT / "bot-policy.json"
NOW = "2026-09-24T12:00:00+00:00"


def _write_event(tmp_path: Path) -> Path:
    """一個「人類開新主題」事件：決策為 OK_HUMAN，會走到 ledger 寫入路徑。"""
    event = {
        "discussion": {
            "number": 7,
            "title": "測試討論",
            "node_id": "D_test",
            "user": {"login": "some-human"},
            "body": "hello",
        }
    }
    path = tmp_path / "event.json"
    path.write_text(json.dumps(event), encoding="utf-8")
    return path


def _argv(tmp_path: Path, ledger: Path) -> list[str]:
    return [
        "--event", str(_write_event(tmp_path)),
        "--registry", str(REGISTRY),
        "--bot-policy", str(BOT_POLICY),
        "--ledger", str(ledger),
        "--now", NOW,
    ]


# ---------- MAJOR-1：dry-run 不得落地 ----------

def test_dry_run_leaves_ledger_byte_identical(tmp_path, capsys):
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(
        '{"at": "2026-09-24T05:06:16+00:00", "login": "j0935586110-lgtm", '
        '"type": "reply", "code": "OK", "allow": true, "topic": "4"}\n',
        encoding="utf-8",
    )
    before = ledger.read_bytes()
    before_sha = hashlib.sha256(before).hexdigest()

    rc = enforce.main(_argv(tmp_path, ledger) + ["--dry-run"])

    assert rc == 0
    after = ledger.read_bytes()
    assert after == before
    assert hashlib.sha256(after).hexdigest() == before_sha
    result = json.loads(capsys.readouterr().out)
    assert "ledger_appended" not in result  # 只有真的寫入才回報


def test_dry_run_does_not_create_missing_ledger(tmp_path, capsys):
    ledger = tmp_path / "does-not-exist.jsonl"
    rc = enforce.main(_argv(tmp_path, ledger) + ["--dry-run"])
    assert rc == 0
    assert not ledger.exists()
    assert "ledger_appended" not in json.loads(capsys.readouterr().out)


# ---------- 非 dry-run：仍要正常寫入一筆 ----------

def test_non_dry_run_appends_exactly_one_line(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)  # 不碰網路
    ledger = tmp_path / "ledger.jsonl"

    rc = enforce.main(_argv(tmp_path, ledger))

    assert rc == 0
    lines = ledger.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["login"] == "some-human"
    assert entry["allow"] is True
    assert entry["at"] == NOW
    result = json.loads(capsys.readouterr().out)
    assert result["ledger_appended"] is True


# ---------- MAJOR-2：並行附加不得交錯 ----------

def _append_worker(path_str: str, login: str, barrier) -> None:
    # 較長的 payload 讓「非原子寫入」更容易交錯；有 flock 時必須完整。
    entry = {
        "at": NOW,
        "login": login,
        "type": "reply",
        "code": "OK",
        "allow": True,
        "topic": "1",
        "pad": "x" * 500,
    }
    barrier.wait()
    enforce.append_ledger(Path(path_str), entry)


def test_concurrent_appenders_produce_two_well_formed_lines(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    ctx = mp.get_context("fork") if "fork" in mp.get_all_start_methods() else mp.get_context("spawn")
    barrier = ctx.Barrier(2)
    procs = [
        ctx.Process(target=_append_worker, args=(str(ledger), f"user-{i}", barrier))
        for i in range(2)
    ]
    for proc in procs:
        proc.start()
    for proc in procs:
        proc.join(timeout=30)
        assert proc.exitcode == 0

    lines = ledger.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    entries = [json.loads(line) for line in lines]  # 任一行交錯都會在這裡爆掉
    assert {e["login"] for e in entries} == {"user-0", "user-1"}
    assert all(e["pad"] == "x" * 500 for e in entries)


def _append_many_worker(path_str: str, index: int, count: int) -> None:
    path = Path(path_str)
    for seq in range(count):
        enforce.append_ledger(path, {"at": NOW, "login": f"u{index}", "seq": seq})


def test_concurrent_appenders_many_lines_no_loss(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    ctx = mp.get_context("fork") if "fork" in mp.get_all_start_methods() else mp.get_context("spawn")

    procs = [ctx.Process(target=_append_many_worker, args=(str(ledger), i, 20)) for i in range(3)]
    for proc in procs:
        proc.start()
    for proc in procs:
        proc.join(timeout=30)
        assert proc.exitcode == 0

    lines = ledger.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 60
    assert len({json.loads(line)["login"] for line in lines}) == 3
