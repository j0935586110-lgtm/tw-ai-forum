# AI agent 實作說明書：讓 Hermes 使用 Codex，agy 作備援

> 這份不是給一般讀者的介紹，而是給能執行指令、讀設定檔、回報證據的 AI agent 或維運者。
>
> 目標：**OpenAI Codex 訂閱作主模型，官方 agy CLI 作備援；工具由 Hermes 統一管理。**

## 0. 不可違反的原則

1. 先讀目前 Hermes 官方文件與本機設定，不要猜設定鍵名。
2. 不讀出、不複製、不回傳任何秘密值。
3. 不把 `.env`、token、cookie、OAuth 資料或完整 log 上傳到公開 repo。
4. 改設定前先備份；測試失敗要能回滾。
5. 不讓 agy 和 Hermes 同時掛載同一套 J1 MCP。
6. 不為了測試故意耗盡訂閱額度；quota fallback 先用可控的錯誤注入或已知失敗模型驗證。
7. 實測結果要附指令、退出碼、模型、provider 和限制，不能只寫「成功」。

## 1. 先做環境盤點

```bash
hermes --help
agy --help
hermes profile list
hermes config get model
hermes config get fallback_providers
```

如果指令名稱或輸出和這份文件不同，以目前安裝版本為準。不要直接覆蓋整份設定檔。

確認登入狀態時，只記錄「成功／失敗」和錯誤類型，不要把 token 或完整秘密環境變數貼到報告：

```bash
agy --print "只回答：AGY_LOGIN_PROBE"
```

## 2. 建立隔離的 agy 實驗 profile

不要先改正式 profile。使用 Hermes 目前版本支援的 profile clone 方法建立測試副本：

```bash
hermes profile create agylab --clone-from default --no-alias
```

若 profile 已存在，先查清楚它是否正在被 gateway 使用，再決定是重用、備份後修改，或另建名稱。不要直接刪除未知 profile。

## 3. 安裝 agy provider/plugin

在目標 profile 安裝經實測可用的 `antigravity-agy` plugin。安裝後確認：

```bash
hermes -p agylab plugins list
hermes -p agylab config get model
```

預期至少要看見 provider/plugin 已啟用。若使用 `antigravity-subscription-directsdk`，先做 A/B 測試，不要假設它比 CLI bridge 快；我們的測試中它明顯較慢，已移除。

## 4. 設定 agy profile

概念上的設定如下；實際寫入請使用目前 Hermes 支援的設定命令或小範圍 patch：

```yaml
model:
  provider: antigravity-agy
  default: gemini-3.8-flash-medium
```

這個 profile 主要用來做 agy smoke test。正式 fallback 則使用已驗證的 Gemini 和 Claude 模型：

```yaml
fallback_providers:
  - provider: antigravity-agy
    model: gemini-3.1-pro-high
  - provider: antigravity-agy
    model: claude-opus-5-5-high
```

`gemini-3.8-flash-medium` 是我們為速度測試選的模型，不要把「最快」誤寫成「最聰明」。

## 5. 關閉 agy 內重複的 J1 MCP

這是本次最重要的架構修正：

```text
正確：Codex / agy → Hermes → 唯一一套 J1 MCP
錯誤：Hermes → J1 MCP
      agy    → 另一套 J1 MCP
```

在 agy 的 MCP 設定中停用 `j1` 和 `j1exec`，但保留 Hermes 自己的 J1 工具。改完後用「列出工具」或 audit trace 確認 agy 不再啟動 J1；不要只看設定檔就宣稱完成。

## 6. 先測單一模型，再測 Hermes tool loop

### 6.1 agy 文字 smoke test

```bash
export ANTIGRAVITY_AGY_PREWARM=0
export ANTIGRAVITY_AGY_CLEANUP=on

hermes chat -q "只回答：GEMINI_SMOKE_OK" \
  --oneshot -Q \
  --provider antigravity-agy \
  --model gemini-3.1-pro-high

hermes chat -q "只回答：CLAUDE_SMOKE_OK" \
  --oneshot -Q \
  --provider antigravity-agy \
  --model claude-opus-5-5-high
```

驗收條件：

- process exit code 是 0
- 回覆包含指定字串
- 沒有把 fallback 成功誤判成原模型成功
- log 沒有秘密值

### 6.2 Hermes 工具迴圈

測試模型是否能真的使用 Hermes 工具，不要只測它會聊天：

```text
請在指定的 scratch 目錄建立 probe.txt，內容必須是 TOOL_OK；
再讀回檔案，最後只回答 TOOL_LOOP_DONE。
```

驗收要讀回檔案內容，並保留可重跑的結果。測試檔放在 `/home/j/.hermes/cache/scratch/`，完成後清理。

## 7. 設定正式 fallback

正式設定的方向：

```yaml
model:
  provider: openai-codex
  default: gpt-5.6-luna

fallback_providers:
  - provider: antigravity-agy
    model: gemini-3.1-pro-high
  - provider: antigravity-agy
    model: claude-opus-5-5-high
  - provider: deepseek
    model: deepseek-flash
  - provider: opencode-zen
    model: mimo-v2.6-flash-free
```

注意：fallback 的順序是「失敗後往下走」，不是多模型投票。每個 provider 都要在同一環境下單獨 smoke test。

## 8. 驗證 fallback，不要燒額度

可以先用不存在的主模型或隔離 profile，驗證 Hermes 能否轉到第一個 fallback：

```bash
hermes chat -q "只回答：FALLBACK_OK" \
  --oneshot -Q \
  --provider <故意失敗的測試 provider> \
  --model <不存在的測試模型>
```

驗收時要從 log 或輸出確認實際 provider/model。只看到 `FALLBACK_OK` 不夠，因為可能是錯誤地走到別的模型。

agy plugin 應將下列錯誤視為可能的 quota／限流訊號：

- `resource_exhausted`
- `quota`
- `rate limit`
- `429`
- `limit reached`
- `out of credits`

但不能只因為出現一個字就認定「額度用完」；要保留原始錯誤類型和 provider，避免把暫時性 503 當成永久 quota。

## 9. 延遲測量

不要只量模型回報時間，要量完整 wall time：

```bash
/usr/bin/time -f 'wall=%e rc=%x' \
  hermes chat -q "只回答：LATENCY_OK" \
  --oneshot -Q --provider antigravity-agy --model gemini-3.1-pro-high
```

至少做 4 次，記錄：

- provider
- model
- wall time
- exit code
- 是否啟動 MCP
- 是否出現 retry／503／429
- 是否為 cold start

不要用單次最快結果代表穩定性；至少回報中位數和離群值。

## 10. 交付報告格式

```markdown
## 環境
- 日期：YYYY-MM-DD
- Hermes 版本：
- agy 版本：
- OS：

## 設定
- 主 provider/model：
- fallback 順序：
- agy 內 J1 MCP：enabled/disabled
- prewarm：on/off

## 實測
| 測試 | provider/model | rc | 結果 |
|---|---|---:|---|
| 文字 smoke | ... | 0 | ... |
| tool loop | ... | 0 | ... |
| fallback | ... | 0 | ... |

## 限制
- 是否真的測到 quota exhaustion：是／否
- 是否有長延遲離群值：是／否
- 尚未證明的事情：
```

## 11. 回滾

若正式對話異常：

1. 停止繼續修改。
2. 把目前設定備份成帶日期的檔案。
3. 還原最後一份已驗證設定。
4. 重新跑主模型 smoke test。
5. 再單獨測 agy，不要同時改兩層。

報告中必須列出備份檔位置、實際回傳和退出碼；不要只說「已回復」。
