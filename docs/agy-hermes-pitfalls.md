# 踩坑與排錯：Codex、agy、Hermes、MCP、J1

這不是理論清單，而是我們實際遇到、實際測過的問題。每一項都分成「看到什麼」「真正原因」「怎麼處理」「還不能宣稱什麼」。

## 1. 把 agy 當成普通 model 名稱直接填入

### 看到什麼
以為在 Hermes 設定裡把 `model` 改成 `agy` 就完成接入。

### 真正原因
agy 是另一個 CLI／模型入口，不是 Hermes 原生模型名稱。中間需要 provider/plugin adapter，負責把 Hermes 的請求轉成 agy 能理解的呼叫，再把回覆和錯誤傳回 Hermes。

### 怎麼處理
使用經實測的 `antigravity-agy` provider/plugin，並先在隔離 profile 做 smoke test。

### 不要宣稱
「裝了 plugin 就一定支援所有工具、圖片、streaming 和 fallback」。這些都要分開測。

## 2. 同時掛兩套 J1 MCP

### 看到什麼
agy 啟動很慢，甚至要等約 60 秒；結束時看到 MCP timeout 或 force-kill。

### 真正原因
Hermes 和 agy 都在嘗試管理 J1，兩個生命週期互相重疊。模型本身可能只推理幾秒，但 CLI 啟動、MCP 掃描、收尾和等待會拖長整體時間。

### 怎麼處理
只保留：

```text
Codex / agy → Hermes → J1
```

停用 agy 內的 `j1` 和 `j1exec`，讓 Hermes 保留唯一一套 J1。

### 不要宣稱
「J1 沒用」或「Go 程式關不掉」。我們直接測過 J1 Go controller 收到 stdin EOF、SIGTERM 和 process-group SIGTERM 都能在毫秒級退出；主要問題在 MCP transport／lifecycle 收尾，不是 Go 語言本身。

## 3. Hermes 也會出現 Force-killed MCP process

### 看到什麼
log 出現：

```text
Force-killed MCP process ... after SIGTERM timeout
```

### 真正原因
這不是 agy 專屬現象。Hermes 的 MCP lifecycle 也有「先要求正常終止，超時後強制 kill」的保底邏輯。我們曾在 `aste`、`j1`、`aimode`、`codegraph` 等不同 MCP 看到類似紀錄。

### 怎麼處理
先分辨三件事：

1. 子程序是否真的仍然活著？
2. 是主程序沒退出，還是 descendant／process group 沒被 reap？
3. 是工具卡住，還是 shutdown timeout 太短？

不要第一時間修改 J1 controller。先用最小重現測 transport、EOF、SIGTERM、process group 和 reap 時序。

### 不要宣稱
看到 force-kill 就等於「功能失敗」。有些情況是工作已完成，只是收尾不乾淨；要同時看 tool result、exit code 和 process state。

## 4. 把模型推理時間當成完整延遲

### 看到什麼
agy 回報模型約 1.7–2.3 秒，但使用者等了十幾秒甚至更久。

### 真正原因
模型時間和 wall time 不是同一件事。wall time 還包含 CLI、登入狀態、MCP 初始化、重試、shutdown 和網路等待。

### 怎麼處理
同時記錄模型回報時間和外部 `/usr/bin/time` wall time，至少跑 4 次，回報中位數和最大值。

## 5. 以為 directsdk 一定比較快

### 看到什麼
想把新的 subscription direct SDK 當成舊 CLI adapter 的替代品。

### 實測結果
我們的 A/B 測試中：

- `antigravity-agy`：約 18.1–26.1 秒，中位數約 21.8 秒
- `antigravity-subscription-directsdk`：約 25.0–166.4 秒，中位數約 76.85 秒

### 怎麼處理
保留較穩定的 `antigravity-agy`，不要因為名字看起來比較直接就跳過 A/B。

### 限制
這是當時環境和測試題的結果，不是永久保證；更新版本後要重測。

## 6. 看到 503 就判定額度用完

### 看到什麼
Gemini 回 `503 UNAVAILABLE`，就以為 agy quota 沒了。

### 真正原因
503 可能是暫時服務不可用、重試、路由或模型端問題；quota 常見訊號則可能是 `429`、`resource_exhausted`、`quota` 等。兩者不能混為一談。

### 怎麼處理
讓 plugin 保留結構化錯誤分類，fallback 只在符合規則時觸發；log 同時記錄 provider、model、錯誤類型和 retry 次數。

## 7. 只測「模型會回答」，沒測工具

### 看到什麼
模型能回一句 `OK`，就宣稱 Hermes + agy 完成。

### 真正原因
模型回答文字，不代表它能正確使用 Hermes tool loop，也不代表 MCP 能正常收尾。

### 怎麼處理
至少分開測：

1. 純文字
2. 建立檔案
3. 讀回檔案
4. 錯誤時不亂報成功
5. MCP 正常退出

## 8. 只測到 fallback 成功，卻不知道是哪個模型回答

### 看到什麼
畫面出現 `FALLBACK_OK`，但沒有 provider/model 證據。

### 真正原因
也許真的走了預期 fallback，也許走了另一條 fallback，甚至是原模型重試後成功。

### 怎麼處理
從 Hermes log、session metadata 或明確的 provider/model 記錄確認路由。報告要寫實際路徑，不要只貼回答文字。

## 9. 沒有真的耗盡額度，卻宣稱 quota 自動切換已驗證

### 目前狀態
我們已驗證：

- Gemini `gemini-3.1-pro-high` 可用
- Claude `claude-opus-5-5-high` 可用
- plugin 能辨識多種 quota／429 類錯誤
- fallback chain 已設定 Gemini → Claude

但我們**沒有故意耗盡訂閱額度**。

### 正確說法
「已驗證 provider、模型和錯誤分類；真實 quota exhaustion 的完整自動切換尚未用消耗額度方式驗證。」

這比為了做漂亮報告而燒掉額度安全，也比較誠實。

## 10. 把 `.env` 直接 source 後出現 `Vault: command not found`

### 看到什麼
shell 顯示：

```text
Command 'Vault' not found
```

但後面的 agy 測試仍然成功。

### 真正原因
`.env` 裡有給人看的註記行，不是合法 shell assignment；用 `source` 時被 shell 當成命令執行。

### 怎麼處理
不要把含註記的 env 檔直接當 shell script source。使用 Hermes／agy 正常載入方式，或只在隔離測試中明確 export 非秘密設定。

### 不要做
不要把 `.env` 內容貼到論壇或 GitHub 來請人除錯，因為裡面可能同時有秘密值。

## 11. prewarm 的取捨

### 關閉 prewarm 的好處
- 減少常駐記憶體
- 減少閒置對話和 MCP 長時間佔用
- 降低多套 agent 同時活著的複雜度

### 代價
第一次呼叫可能多幾秒冷啟動時間。

我們為 freetest 選擇 `ANTIGRAVITY_AGY_PREWARM=0`，因為當時更重視資源和隔離，不是因為 prewarm 永遠不好。

## 12. 失敗時的最小排錯順序

```text
1. 確認目前實際 provider/model
2. 先測純文字
3. 再測 Hermes tool loop
4. 確認 agy 是否重複啟動 J1
5. 量 wall time，不只看模型時間
6. 分辨 503、429、quota、timeout
7. 看子程序和 process group 是否真的殘留
8. 必要時回滾，不要連續改三個層級
```

## 可重跑的證據位置

公開文件只放不含秘密的結論。完整測試產物放在本機 scratch：

- `/home/j/.hermes/cache/scratch/agy_lat.py`
- `/home/j/.hermes/cache/scratch/ab_lat.py`
- `/home/j/.hermes/cache/scratch/zen-free-hard-benchmark.md`
- `/home/j/.hermes/cache/scratch/zen-free-hard-scored.json`

這些檔案不是公開 API；重新測試時要先確認檔案仍存在，並重新產生日期、版本和結果。
