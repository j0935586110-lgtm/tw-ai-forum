# Codex、agy、Hermes：白話入門

> 這篇寫給「知道自己想用 AI 做事，但不想先讀一堆專有名詞」的人。
>
> 本文記錄我們在 **2026 年 10 月 6 日** 的實測架構。模型名稱、外掛和額度政策會變動；要重跑時，請以目前環境的實際輸出為準。

## 先講結論

我們不是把兩個 AI 混在一起，而是安排不同角色：

```text
你
 │
 ▼
Hermes：總管、對話入口、工具管理員
 │
 ├─ 主要模型：OpenAI Codex 訂閱
 │
 └─ 備援模型：agy 訂閱
       ├─ Gemini 優先
       └─ Gemini 額度／服務失敗時，再試 Claude
```

如果主要模型正常，就由 Codex 回答。只有主要模型失敗、暫時不可用，或符合備援條件時，Hermes 才把工作交給 agy。

## 這四個名字各自做什麼

### Codex

這裡的 Codex 指 OpenAI 的訂閱模型入口。它比較像「主要駕駛」：平常的對話和工作先由它處理。

### agy

agy 是另一個模型入口。我們使用官方 `agy` CLI 和自己的訂閱，不是偷拿 API key，也不是輪流切換帳號。

agy 可以叫到不同模型，例如：

- Gemini：`gemini-3.1-pro-high`
- Claude：`claude-opus-5-5-high`
- 測速用 Gemini：`gemini-3.8-flash-medium`

### Hermes

Hermes 是「總管」。它負責：

- 接收你的訊息
- 決定現在用哪個模型
- 在必要時切換備援模型
- 把工具交給正確的地方執行
- 管理檔案、瀏覽器、遠端機器和 MCP 工具

Hermes 不是模型本身；它比較像一個可以換引擎的工作台。

### MCP 與 J1

MCP 是讓模型使用外部工具的一種接頭。J1 是我們的遠端操作工具。

重要原則是：**J1 只讓 Hermes 管理一份。**

不要同時讓 Hermes 和 agy 各自啟動一套 J1。兩邊同時收尾、等待或重啟時，會互相增加延遲，甚至留下「強制終止 MCP 子程序」的紀錄。

## 我們最後採用的路由

目前主要設定是：

```text
1. openai-codex / gpt-5.6-luna
2. antigravity-agy / gemini-3.1-pro-high
3. antigravity-agy / claude-opus-5-5-high
4. deepseek / deepseek-flash
5. opencode-zen / mimo-v2.6-flash-free
```

這不是「每次都把答案丟給五個模型」，而是前面的模型無法完成時，才往下一個走。

agy 的兩個模型已分別實測成功回覆：

```text
gemini-3.1-pro-high  → GEMINI_FALLBACK_OK
claude-opus-5-5-high → CLAUDE_FALLBACK_OK
```

這證明模型入口可用；但**沒有故意把訂閱額度燒光**，所以不能宣稱已完整模擬「額度真的用完後自動切換」的情境。實際切換仍取決於 agy 回傳的 quota、429 或 resource exhausted 錯誤是否被正確辨識。

## 一般人可以怎麼理解

把它想成公司的客服中心：

- Hermes 是櫃台和主管。
- Codex 是第一位專業員工。
- agy Gemini 是第二位員工。
- agy Claude 是第三位員工。
- J1 是共用的電話、印表機和遠端電腦。

共用設備應由一個主管管理。不能每位員工都自己搶著開同一台印表機，否則不是印不出來，就是關機時互相等待。

## 為什麼不直接讓 agy 當全部東西

可以，但不一定比較好：

1. Hermes 已經有完整的工具、記憶、對話和平台整合。
2. 讓 Hermes 統一管理工具，比讓每個模型各自掛工具更容易追蹤。
3. 主模型和備援模型可以分開更換，不必整套重做。
4. 出問題時可以分辨：是模型問題、agy CLI 問題，還是工具生命週期問題。

## 我們測到的現實

agy 的模型本身回覆常常只需要幾秒，但整個指令從開始到結束可能更久。額外時間可能來自：

- 啟動 agy CLI
- 載入登入狀態
- 掃描或啟動 MCP
- 等待 MCP 正常關閉
- 重試或服務端暫時錯誤

我們曾測到 wall time 從約 4 秒到 70 秒以上不等。停用 agy 內部重複的 J1 MCP 後，通常明顯改善，但仍可能有離群值。

所以看到「模型回答只花 2 秒、整體卻等了 20 秒」時，不要立刻怪模型變笨；很可能時間花在啟動和收尾。

## 安全界線

- 不把密碼、token、CVC、OAuth refresh token 寫進文章。
- 不把 `.env` 上傳到 GitHub。
- 只記錄模型名稱、設定方向、錯誤類型和可重跑的非秘密指令。
- 正式環境先備份設定，再改路由。
- 發布前檢查 git diff 和敏感檔案。

下一篇：

- [AI agent 實作說明書](agy-hermes-agent-runbook.md)
- [踩坑與排錯紀錄](agy-hermes-pitfalls.md)
