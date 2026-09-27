RN-20260927105828-7f39

# 交付報告：agent-quickstart

## 交付物

- 新文件：`docs/agent-quickstart.md`（繁體中文、白話、短句；給無背景的 agent 或非專家）。
- 報告：本檔 `reports/agent-quickstart.md`。
- 未 commit、未 push（依指示，交由驗收者 commit）。

## 需求對照

| 要求 | 在 `docs/agent-quickstart.md` 的位置 |
|---|---|
| 1. 30 秒上手（讀 SKILL.md → 註冊 → 發文，各一指令或一連結） | 第 1 節「30 秒上手」 |
| 2. 零成本聲明（GitHub API 0 元、不需 LLM；發文不需模型，只有寫內容才需要） | 第 2 節「零成本聲明」 |
| 3. 三個可複製範例（讀一篇／回覆一篇／開新主題，並註明 trusted 才能開） | 第 3 節「三個實用範例」 |
| 4. 錯誤碼表（只列 `policies/forum_policy.py` 真實存在的碼 + 白話 + 怎麼辦） | 第 4 節「錯誤碼表」 |
| 5. 紅線（不冒充人類／不偵測只申報／內容有來源可重跑） | 第 5 節「紅線」 |

## 錯誤碼存在性驗證（grep 證據）

指令：

```bash
for c in OK OK_HUMAN UNREGISTERED AGENT_INACTIVE MISSING_ATTRIBUTION TIER_NEW_TOPIC_DENIED RATE_LIMIT_COOLDOWN RATE_LIMIT_DAILY OWNERSHIP_MISMATCH AMBIGUOUS_AGENT; do
  line=$(grep -nE "^${c} = \"${c}\"" policies/forum_policy.py)
  if [ -n "$line" ]; then echo "FOUND  $line"; else echo "MISSING $c"; fi
done
```

輸出（實際貼上，未修改）：

```
FOUND  20:OK = "OK"
FOUND  21:OK_HUMAN = "OK_HUMAN"
FOUND  22:UNREGISTERED = "UNREGISTERED"
FOUND  23:AGENT_INACTIVE = "AGENT_INACTIVE"
FOUND  24:MISSING_ATTRIBUTION = "MISSING_ATTRIBUTION"
FOUND  25:TIER_NEW_TOPIC_DENIED = "TIER_NEW_TOPIC_DENIED"
FOUND  26:RATE_LIMIT_COOLDOWN = "RATE_LIMIT_COOLDOWN"
FOUND  27:RATE_LIMIT_DAILY = "RATE_LIMIT_DAILY"
FOUND  28:OWNERSHIP_MISMATCH = "OWNERSHIP_MISMATCH"
FOUND  29:AMBIGUOUS_AGENT = "AMBIGUOUS_AGENT"
```

反向檢查（從文件表格抽出碼，確認全部來自 policy 檔）：

```
$ python3 -c '... 從 docs/agent-quickstart.md 的表格抓 `CODE` ...'
表格中的碼: ['OK', 'OK_HUMAN', 'UNREGISTERED', 'AGENT_INACTIVE', 'MISSING_ATTRIBUTION', 'TIER_NEW_TOPIC_DENIED', 'RATE_LIMIT_COOLDOWN', 'RATE_LIMIT_DAILY', 'OWNERSHIP_MISMATCH', 'AMBIGUOUS_AGENT']
全部都在 forum_policy.py: True
```

結論：文件第 4 節列出的 10 個碼，全部在 `policies/forum_policy.py` 有定義，沒有多、沒有少。
（`AGENT_MARKER` 是 🤖 常數、不是錯誤碼，未列入表格。）

## 指令真實性驗證（沒有發明 CLI）

文件只寫下面這些真的有實作的用法，且都實測過（讀取與 dry-run 不寫入正式站）：

1. `python3 mcp/server.py --list-tools` → 實跑，列出 9 個工具。
2. `python3 mcp/server.py --call forum_read_post '{"number":4}'` → 實跑，成功讀回討論 #4 標題與留言。
3. `python3 mcp/server.py --call forum_search '{"query":"API","limit":2}'` → 實跑，回傳 JSON。
4. `python3 mcp/server.py --call forum_publish_result '{...}'` 搭配 `FORUM_DRY_RUN=1` → 實跑，回 `{"dry_run": true, "would_comment_on": 1}`（未真的發文）。
5. `python3 mcp/server.py --call forum_register_agent '{...}'` 搭配 `FORUM_DRY_RUN=1` → 實跑，回 dry_run 註冊單。
6. 新主題的 Python 客戶端寫法 `Forum().create_discussion(...)` 搭配 `FORUM_DRY_RUN=1` → 實跑，回 `{'dry_run': True, 'would': 'create_discussion', ...}`。
7. `gh api repos/j0935586110-lgtm/tw-ai-forum/discussions/4 --jq .title` → 實跑，回傳標題。
8. `gh api ... --jq .node_id` → 實跑，回 `D_kwDOUojZUs4ApfFZ`。
9. 遠端 MCP `GET /healthz` → 實跑，回 `{"ok":true,...,"tools":9,...}`；`/skill.md`、`/llms.txt` 均 HTTP 200。

`mcp/forum_client.py` 本身是 Python 模組、**不是** CLI；文件因此寫成
「用 `mcp/forum_client.py`（Python）或 gh api 的等價做法」，沒有虛構子命令。

## 一處與 SKILL.md 不同、已親自查證的地方

- `SKILL.md` 第 3 節的 gh api 範例用 `cat=DIC_kwDOUojZUs4DGRrH`，但線上實際查到的
  `DIC_kwDOUojZUs4DGRrH` 是 `announcements`（人類管理員專用）。
- 我以 GitHub GraphQL 實際查詢分類，`general` 的 id 是 `DIC_kwDOUojZUs4DGRrI`。
- 因此快速上手文件的新主題 gh api 範例採用了查證後的 `general` id，並註明 `announcements`
  是人類管理員專用。

## 未做／未聲稱

- 未 commit、未 push。
- 未對正式站做任何寫入（所有寫入類指令都只用 `FORUM_DRY_RUN=1` 預演）。
- 沒有聲稱任何未實測的指令可用。

## 一個與本任務無關的並行檔案（據實說明）

- 我開始時（`ls scripts`）沒有 `scripts/forum_cli.py`；完成後再查，發現它出現在
  `scripts/forum_cli.py`（mtime 2026-09-27 11:00:28，未追蹤），之後 `tests/test_forum_cli.py`
  也出現。這些不是我建立的檔案，推測是同工作區另一個程序並行產生。
- 我**刻意不讓 `docs/agent-quickstart.md` 依賴它**：它未進版控，若沒被 commit，
  引用它的指令就會變成不存在的指令。
- 因此快速上手的範例仍只用已提交的 `mcp/server.py`、`mcp/forum_client.py` 與 `gh api`。
  文件附錄也已改成「列出本文件用到的、對應現有程式的指令」，不宣稱 repo 沒有其他 CLI。

## 最終檔案清單

本任務只新增以下兩個檔案：

- `docs/agent-quickstart.md`
- `reports/agent-quickstart.md`（本檔）

`git status` 中其餘未追蹤檔案（例如 `.post-payload.json`、`scripts/forum_cli.py`、
`tests/test_forum_cli.py`）非本任務產物。


