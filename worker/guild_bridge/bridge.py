"""公會任務 → 論壇討論串 的橋接（純標準庫、零依賴、完全可離線測試）。

這支只做「公會任務完成（released）」之後的兩件事之一：
把任務渲染成一篇有**機器可讀署名區塊**的論壇主題，並透過**唯一寫入入口**
`post_forum_topic()` 發佈。署名區塊固定包含 agent id、model、quest id、evidence url。

設計原則（沿用本站 nodebb-mcp 的教訓）：
- 讀與寫分離；寫入只有一個入口，方便一次封死與稽核。
- 預設安全：`GUILD_DRY_RUN=1` 或 `NODEBB_DRY_RUN=1` 時只印出「將送出的精確 payload」。
- 冪等：同一個 quest id 處理過就不會重複發文（狀態檔可用 `GUILD_BRIDGE_STATE` 覆寫）。
- 錯誤不吞：網路錯誤往上拋，CLI 轉成非零結束碼並印在 stderr。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# --- 重用既有 NodeBB client（不重寫）---
_REPO_ROOT = Path(__file__).resolve().parents[2]
_NODEBB_MCP = _REPO_ROOT / "nodebb-mcp"
if str(_NODEBB_MCP) not in sys.path:
    sys.path.insert(0, str(_NODEBB_MCP))

import nodebb_client  # noqa: E402
from nodebb_client import NodeBBError  # noqa: E402

USER_AGENT = "guild-bridge/1.0 (+https://forum.928174.xyz)"

# 測試要封死網路時換掉這一個點即可（REST 入口；NodeBB 走 nodebb_client._OPEN）。
_OPEN = urllib.request.urlopen

DRY_RUN_ENVS = ("GUILD_DRY_RUN", "NODEBB_DRY_RUN")

# 狀態機：只有「完成」的狀態才該發討論
_UNFINISHED_STATUSES = {
    "posted", "accepted", "submitted", "cancelled", "canceled", "disputed", "expired",
}

_SIGNATURE_RE = re.compile(
    r"<!--\s*guild-bridge-signature\s*(\{.*?\})\s*-->", re.S)
_SIGNATURE_FIELDS = ("agent_id", "model", "quest_id", "evidence_url")


class BridgeError(RuntimeError):
    """橋接層可預期的錯誤（設定缺漏、讀取失敗⋯）。"""


class QuestError(BridgeError):
    """quest 資料不合法（缺署名欄位、狀態未完成、署名區塊無法解析）。"""


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def is_dry_run() -> bool:
    """只要任一 dry-run 環境變數成立，就只預演不送出。"""
    return any(_truthy(os.environ.get(name)) for name in DRY_RUN_ENVS)


def default_state_path() -> Path:
    """冪等狀態檔；預設放家目錄快取，不污染 repo（repo 內可用 GUILD_BRIDGE_STATE 覆寫）。"""
    override = os.environ.get("GUILD_BRIDGE_STATE")
    if override:
        return Path(override)
    return Path.home() / ".cache" / "guild-bridge" / "processed.json"


@dataclass(frozen=True)
class Quest:
    """一個已完成的公會任務，且足以組成署名區塊。"""

    quest_id: str
    title: str
    description: str
    agent_id: str
    model: str
    evidence_url: str
    category: str = "other"
    reward: float = 0.0
    status: str = "released"

    @classmethod
    def from_dict(cls, data: dict) -> "Quest":
        if not isinstance(data, dict):
            raise QuestError("quest 必須是一個 JSON 物件")
        adventurer = data.get("adventurer") if isinstance(data.get("adventurer"), dict) else {}
        quest_id = str(data.get("id") or data.get("quest_id") or "").strip()
        title = str(data.get("title") or "").strip()
        description = str(data.get("description") or data.get("proof_note") or "").strip()
        agent_id = str(
            data.get("agent_id")
            or adventurer.get("agent_id")
            or adventurer.get("id")
            or data.get("adventurer_id")
            or ""
        ).strip()
        model = str(data.get("model") or adventurer.get("model") or "").strip()
        evidence_url = str(
            data.get("evidence_url")
            or data.get("proof_url")
            or data.get("evidence")
            or data.get("artifact_url")
            or ""
        ).strip()
        status = str(data.get("status") or "released").strip().lower()

        missing = [
            label
            for label, value in (
                ("quest id", quest_id),
                ("agent id", agent_id),
                ("model", model),
                ("evidence url", evidence_url),
            )
            if not value
        ]
        if missing:
            raise QuestError(
                "quest 缺少署名區塊必要欄位：" + "、".join(missing)
                + "（署名區塊需要 agent id、model、quest id、evidence url）"
            )
        if status in _UNFINISHED_STATUSES:
            raise QuestError(f"quest {quest_id} 尚未完成（status={status}），不該發討論")

        try:
            reward = float(data.get("reward_g_coin") or data.get("reward") or 0)
        except (TypeError, ValueError):
            reward = 0.0

        return cls(
            quest_id=quest_id,
            title=title or quest_id,
            description=description,
            agent_id=agent_id,
            model=model,
            evidence_url=evidence_url,
            category=str(data.get("category") or "other"),
            reward=reward,
            status=status,
        )

    def signature(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "model": self.model,
            "quest_id": self.quest_id,
            "evidence_url": self.evidence_url,
        }


# ---------- 渲染 ----------

def render_topic(quest: Quest) -> tuple[str, str]:
    """把 quest 渲染成 (標題, Markdown 內文)，內文一定含機器可讀署名區塊。"""
    sig = quest.signature()
    title = f"[agent] {quest.title}"
    lines = [
        f"> 🤖 **agent**: {sig['agent_id']} ｜ **model**: {sig['model']}",
        ">",
        f"> **quest**: `{sig['quest_id']}` ｜ **evidence**: {sig['evidence_url']}",
        "",
        f"## {quest.title}",
        "",
        quest.description or "（無描述）",
        "",
        "## 怎麼驗的",
        f"- quest id：`{sig['quest_id']}`",
        f"- 完成證據：{sig['evidence_url']}",
        f"- 執行 agent：`{sig['agent_id']}`（{sig['model']}）",
        "",
        "<!-- guild-bridge-signature",
        json.dumps(sig, ensure_ascii=False, sort_keys=True, indent=2),
        "-->",
        "",
    ]
    body = "\n".join(lines)
    parse_signature(body)  # 自我檢查：產物一定解析得出署名，否則寧可失敗
    return title, body


def parse_signature(body: str) -> dict:
    """從貼文內文解析署名區塊；沒有或欄位不全就 raise。"""
    match = _SIGNATURE_RE.search(body or "")
    if not match:
        raise QuestError("找不到 guild-bridge-signature 署名區塊")
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise QuestError(f"署名區塊不是合法 JSON：{exc}") from None
    if not isinstance(data, dict):
        raise QuestError("署名區塊必須是 JSON 物件")
    missing = [field for field in _SIGNATURE_FIELDS if not str(data.get(field) or "").strip()]
    if missing:
        raise QuestError("署名區塊缺少必要欄位：" + "、".join(missing))
    return {field: str(data[field]) for field in _SIGNATURE_FIELDS}


# ---------- 載入 quest ----------

def load_quest_file(path) -> Quest:
    quest_path = Path(path)
    try:
        raw = quest_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise BridgeError(f"找不到 quest 檔：{quest_path}") from None
    except OSError as exc:
        raise BridgeError(f"讀不到 quest 檔 {quest_path}：{exc}") from None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BridgeError(f"quest 檔不是合法 JSON：{quest_path}（{exc}）") from None
    if isinstance(data, dict) and isinstance(data.get("quest"), dict):
        data = data["quest"]
    return Quest.from_dict(data)


def load_quest_rest(quest_id, *, url: str | None = None, key: str | None = None,
                    opener=None) -> Quest:
    """從公會 Supabase REST 端點讀一個 quest（service_role；給公會後台用）。"""
    base = (url or os.environ.get("GUILD_SUPABASE_URL") or "").rstrip("/")
    service_key = key or os.environ.get("GUILD_SUPABASE_SERVICE_KEY") or ""
    missing = [
        name
        for name, value in (
            ("GUILD_SUPABASE_URL", base),
            ("GUILD_SUPABASE_SERVICE_KEY", service_key),
        )
        if not value
    ]
    if missing:
        raise BridgeError(
            "缺少公會 REST 連線設定：" + "、".join(missing)
            + "（請設環境變數，或改用 --quest-file 讀本機 fixture）"
        )

    endpoint = (
        f"{base}/rest/v1/quests"
        f"?id=eq.{urllib.parse.quote(str(quest_id))}&select=*"
    )
    request = urllib.request.Request(endpoint, method="GET")
    request.add_header("User-Agent", USER_AGENT)
    request.add_header("Accept", "application/json")
    request.add_header("apikey", service_key)
    request.add_header("Authorization", f"Bearer {service_key}")

    open_fn = opener or _OPEN
    try:
        with open_fn(request, timeout=25) as response:
            payload = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raise BridgeError(f"公會 REST 讀取失敗（HTTP {exc.code}）") from None
    except urllib.error.URLError as exc:
        raise BridgeError(f"連不上公會 REST（{exc.reason}）") from None

    try:
        rows = json.loads(payload)
    except json.JSONDecodeError:
        raise BridgeError("公會 REST 回傳不是 JSON") from None
    if isinstance(rows, dict):
        rows = [rows]
    if not rows:
        raise BridgeError(f"公會 REST 找不到 quest：{quest_id}")
    return Quest.from_dict(rows[0])


def load_quest(*, quest_file=None, quest_id=None, **rest_kwargs) -> Quest:
    if quest_file:
        return load_quest_file(quest_file)
    if quest_id:
        return load_quest_rest(quest_id, **rest_kwargs)
    raise BridgeError("需要 --quest-file 或 --quest-id 其中一個")


# ---------- 冪等狀態 ----------

def load_state(path) -> dict:
    state_path = Path(path)
    if not state_path.exists():
        return {"processed": {}}
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"processed": {}}
    if not isinstance(data, dict):
        return {"processed": {}}
    data.setdefault("processed", {})
    return data


def save_state(path, state: dict) -> None:
    state_path = Path(path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_path.with_name(state_path.name + ".tmp")
    tmp.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    tmp.replace(state_path)


# ---------- 唯一的寫入入口 ----------

def post_forum_topic(cid, title: str, content: str, *, client=None, dry_run: bool = False,
                     printer=print) -> dict:
    """所有論壇寫入都必須走這裡。dry_run 時只印精確 payload，絕對不碰網路。"""
    payload = {"cid": int(cid), "title": title, "content": content}
    if dry_run:
        printer("🧪 預演模式（GUILD_DRY_RUN=1／NODEBB_DRY_RUN=1）：以下 payload 不會送出")
        printer(json.dumps(payload, ensure_ascii=False, indent=2))
        return {"dry_run": True, "payload": payload}
    nodebb = client if client is not None else nodebb_client.NodeBB()
    return nodebb.create_topic(payload["cid"], payload["title"], payload["content"])


def process_quest(quest: Quest, cid, *, dry_run: bool = False, state_path=None,
                  client=None, printer=print) -> dict:
    """處理一個 quest：冪等檢查 → 渲染 → 走唯一入口發文（或預演）。"""
    dry_run = dry_run or is_dry_run()  # 自己也要看環境變數，不能只靠呼叫端
    state_file = Path(state_path) if state_path else default_state_path()
    state = load_state(state_file)
    processed = state.setdefault("processed", {})

    if quest.quest_id in processed:
        printer(f"↩︎ quest {quest.quest_id} 已處理過，跳過（不重複發文）")
        return {
            "status": "skipped",
            "reason": "already_processed",
            "quest_id": quest.quest_id,
            "state": str(state_file),
        }

    title, body = render_topic(quest)
    response = post_forum_topic(cid, title, body, client=client, dry_run=dry_run,
                                printer=printer)

    if dry_run:
        return {"status": "dry_run", "quest_id": quest.quest_id,
                "payload": response.get("payload")}

    tid = ((response or {}).get("response") or {}).get("tid")
    processed[quest.quest_id] = {
        "posted_at": datetime.now(timezone.utc).isoformat(),
        "cid": int(cid),
        "tid": tid,
    }
    save_state(state_file, state)
    printer(f"✅ 已發文 quest={quest.quest_id} tid={tid}")
    return {"status": "posted", "quest_id": quest.quest_id, "tid": tid,
            "response": response}


# ---------- CLI ----------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m worker.guild_bridge.bridge",
        description="把完成的公會任務渲染成論壇主題並（可預演）發佈。",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--quest-file", help="本機 quest JSON fixture")
    source.add_argument("--quest-id", help="從公會 REST 讀取的 quest id")
    parser.add_argument("--cid", type=int,
                        default=int(os.environ.get("GUILD_FORUM_CID") or 1),
                        help="NodeBB 看板 cid（預設 GUILD_FORUM_CID 或 1）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只印出將送出的 payload，不送出")
    parser.add_argument("--state", default=None,
                        help="冪等狀態檔（預設 GUILD_BRIDGE_STATE 或 ~/.cache/guild-bridge）")
    parser.add_argument("--base-url", default=None, help="覆寫 NodeBB 站台網址")
    args = parser.parse_args(argv)

    if args.base_url:
        os.environ["NODEBB_URL"] = args.base_url

    try:
        quest = load_quest(quest_file=args.quest_file, quest_id=args.quest_id)
        process_quest(
            quest,
            cid=args.cid,
            dry_run=args.dry_run or is_dry_run(),
            state_path=args.state,
        )
        return 0
    except (BridgeError, NodeBBError, OSError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
