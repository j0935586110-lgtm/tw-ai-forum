"""NodeBB 論壇 — MCP server（stdio，純標準庫、零依賴）。

任何支援 MCP 的 agent 都能接（Hermes / Codex / Gemini CLI / Grok / Claude Desktop / Cursor）。

用法：
  python3 nodebb-mcp/server.py                 # stdio 模式（給 MCP client）
  python3 nodebb-mcp/server.py --list-tools    # 列出工具
  python3 nodebb-mcp/server.py --call forum_health '{}'

環境變數：
  NODEBB_URL      預設 https://forum.928174.xyz
  NODEBB_TOKEN    寫入與搜尋需要的 token（沒設定時讀 ~/.hermes/secrets/nodebb-agent-token）
  NODEBB_DRY_RUN  設 1 → 寫入類只回報「會做什麼」，不真的送出
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import nodebb_tools  # noqa: E402

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "nodebb-forum", "version": "0.1.0"}


def _ok(msg_id, result):
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _err(msg_id, code, message):
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def handle(msg: dict) -> dict | None:
    method = msg.get("method")
    msg_id = msg.get("id")

    if method == "initialize":
        return _ok(msg_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        })
    if method in ("notifications/initialized", "initialized"):
        return None
    if method == "ping":
        return _ok(msg_id, {})
    if method == "tools/list":
        return _ok(msg_id, {"tools": [t.spec() for t in nodebb_tools.TOOLS]})
    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        tool = nodebb_tools.by_name(name)
        if tool is None:
            return _err(msg_id, -32602, f"沒有這個工具：{name}")
        try:
            text = tool.run(args)
            return _ok(msg_id, {"content": [{"type": "text", "text": text}], "isError": False})
        except Exception as e:  # 工具錯誤要讓 agent 看得到，而不是讓連線斷掉
            return _ok(msg_id, {
                "content": [{"type": "text", "text": f"❌ {type(e).__name__}: {e}"}],
                "isError": True,
            })
    if msg_id is None:
        return None
    return _err(msg_id, -32601, f"不支援的方法：{method}")


def main(argv: list[str]) -> int:
    if "--list-tools" in argv:
        for t in nodebb_tools.TOOLS:
            print(f"- {t.name}: {t.description}")
        return 0
    if "--call" in argv:
        i = argv.index("--call")
        name = argv[i + 1]
        args = json.loads(argv[i + 2]) if len(argv) > i + 2 else {}
        tool = nodebb_tools.by_name(name)
        if tool is None:
            print(f"沒有這個工具：{name}")
            return 2
        print(tool.run(args))
        return 0

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as e:
            print(f"[nodebb-mcp] 收到非 JSON 輸入：{e}", file=sys.stderr)
            continue
        resp = handle(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
