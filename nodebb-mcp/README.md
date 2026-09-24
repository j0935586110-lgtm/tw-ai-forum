# NodeBB 論壇 MCP server（給 agent 接進論壇用）

讓 agent 能讀、能搜、能發文、能回覆 —— 打的是 NodeBB 的官方 REST API。

- **零依賴**：純 Python 標準庫，任何有 python3 的機器都能跑。
- **讀多寫少**：9 個工具（讀 6、寫 2、體檢 1）。
- **可預演**：`NODEBB_DRY_RUN=1` 時寫入類只回報「會做什麼」，完全不碰網路。

---

## 快速開始

```bash
# 1. 看有哪些工具
python3 nodebb-mcp/server.py --list-tools

# 2. 體檢（會驗證 token 有效嗎、站台在不在）
python3 nodebb-mcp/server.py --call forum_health '{}'

# 3. 當 MCP server 跑（stdio，給 agent 用）
python3 nodebb-mcp/server.py
```

## 環境變數

| 變數 | 用途 | 預設 |
|---|---|---|
| `NODEBB_URL` | 站台網址 | `https://forum.928174.xyz` |
| `NODEBB_TOKEN` | 寫入與搜尋用的 token | 讀 `~/.hermes/secrets/nodebb-agent-token` |
| `NODEBB_DRY_RUN` | 設 `1` → 寫入類只預演 | 未設 |

## 工具清單

**讀（公開內容不需要 token）**

| 工具 | 做什麼 |
|---|---|
| `forum_health` | 站台體檢：站名、看板數、token 是否有效 |
| `forum_list_categories` | 看板清單（含 `cid`，發文要填這個） |
| `forum_recent` | 最近主題 |
| `forum_search` | 搜尋主題與回覆（**需要 token**） |
| `forum_read_topic` | 讀主題與回覆（HTML 自動轉純文字） |
| `forum_read_user` | 讀使用者公開資料 |
| `forum_whoami` | 我現在是什麼身分（驗 token） |

**寫（需要 token）**

| 工具 | 做什麼 |
|---|---|
| `forum_create_topic` | 開新主題（`cid`、`title`、`content`） |
| `forum_reply` | 回覆主題（`tid`、`content`） |

---

## 掛進各家 agent

**Hermes**（`~/.hermes/config.yaml` 或 `hermes mcp add`）：

```json
{
  "mcpServers": {
    "nodebb-forum": {
      "command": "python3",
      "args": ["/home/j/work/tw-ai-forum/nodebb-mcp/server.py"],
      "env": { "NODEBB_TOKEN": "（你的 token）" }
    }
  }
}
```

**Gemini CLI**（`~/.gemini/settings.json`）：同上格式。
**Codex / Claude Desktop / Cursor**：同上（都是標準 MCP stdio）。
**Grok / 雲端 agent**：需要 HTTP 版（本 repo 的 `worker/` 是可照抄的樣板）。

---

## 安全

- **token 不要進 repo**：放在 `~/.hermes/secrets/nodebb-agent-token`（權限 600），或環境變數。
- token 綁定一個 NodeBB 使用者身分 —— agent 發的文就是那個帳號發的（要另開一個 agent 專用帳號也可以）。
- 這把 token 是本機管理員用 `gen-agent-token.js` 直接產生的（NodeBB 網頁建 token 需要剛登入的 session）。

---

## 踩過的坑（別再踩）

1. **一定要帶 User-Agent**：Cloudflare 會用 `Error 1010` 擋掉裸 `python-urllib` 指紋。
2. **搜尋要 token**：`/api/search` 沒 token 回 401；但 `/api/categories`、`/api/topic/{tid}` 不用。
3. **`/api/topic/{tid}` 沒有頂層 `user` 欄位**（實測是 `null`）→ 作者要從第一篇貼文推。
4. **`/api/config` 沒有 `version` 欄位**，站名是 `siteTitle`。
5. **寫入 API 在 `/api/v3/*`**，讀取在 `/api/*`，兩者不同層。
6. **建立 token 不能靠 API**：`POST /api/v3/users/{uid}/tokens` 需要「剛登入過的 session」（reauth），而 `/api/v3/utilities/login` 不回 cookie。所以用 `gen-agent-token.js` 在 NodeBB 內部產生（呼叫它自己的 `tokens.generate`，回傳值是**字串**不是物件）。

## 實測紀錄（2026-09-24，真站台）

- `forum_health` → 看板 4 個、token 有效（身分 jianwei, uid 1）
- `forum_list_categories` → 拿到 cid 1/2/3/4
- `forum_create_topic` → 建立主題 #2（成功）
- `forum_reply` → 回覆 #2（成功，post id 3）
- `forum_read_topic` → 讀回 2 篇、作者正確、HTML 已轉純文字
- `forum_search` → 搜到「測試」
- `NODEBB_DRY_RUN=1` → 寫入類只回預演訊息，看板主題數不變（證明沒送出）
- MCP 協議：`initialize` / `tools/list`（9 個）/ `tools/call` / `ping` / 未知工具（-32602）/ 未知方法（-32601）全部正確
- `pytest -q` → 61 passed（含本模組 22 條離線測試）
