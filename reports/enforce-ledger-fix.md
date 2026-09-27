RN-20260927110747-9c8e

# 政策閘門 ledger 修正報告

修正 `reviews/forum-cli-adversarial-review.md` 的 MAJOR-1（真）與 MAJOR-2。

## 修正內容

### 1. `--dry-run` 不再寫入 ledger（MAJOR-1）

`policies/enforce.py` 原本的 ledger 附加在 `if not args.dry_run and token:` 之外，任何
路徑都會執行。現改為：

- 新增 `append_ledger(path, entry)`，將附加集中管理。
- `main()` 只在 `if not args.dry_run:` 時呼叫它，並在同一分支才設
  `result["ledger_appended"] = True`。
- 因此 dry-run 的結果 JSON 不再出現 `ledger_appended` 欄位，也不會建立不存在的
  ledger 檔。

### 2. 並行附加改為鎖內單次寫入（MAJOR-2）

`append_ledger()` 以 `fcntl.flock(..., LOCK_EX)` 包住整個「寫一行 + flush + fsync」，
並行事件不會交錯或遺失 JSON 行；`load_ledger()` 讀取時取 `LOCK_SH`，避免讀到寫到一半
的行。僅用標準函式庫（`fcntl` 為 POSIX 內建；非 POSIX 平台無 `fcntl` 時退化為單純
附加，不影響 CI 與本機 Linux）。

### 測試

新增 `tests/test_enforce_ledger.py`（5 條）：

- `test_dry_run_leaves_ledger_byte_identical`：dry-run 後 ledger 位元組完全相同，且
  輸出 JSON 不含 `ledger_appended`。
- `test_dry_run_does_not_create_missing_ledger`：dry-run 不建立 ledger。
- `test_non_dry_run_appends_exactly_one_line`：非 dry-run（無 token、不碰網路）恰好
  附加一行合法 JSON。
- `test_concurrent_appenders_produce_two_well_formed_lines`：兩個並行 process 各附加
  一筆，檔案恰為兩行且皆可 `json.loads`。
- `test_concurrent_appenders_many_lines_no_loss`：三個 process 各附加 20 筆，共 60 行
  無遺失。

## 驗證

`python3 -m pytest -q tests/` 實際輸出：

```text
.......................................................... [ 50%]
.........................................................                [100%]
115 passed, 14 subtests passed in 1.19s
```

`agents/ledger.jsonl` dry-run 前後 sha256（證明未變動）：

```text
BEFORE: c70378fac2e380b46fb260f7cc52804158563f6863d24a0ebad6ff7f2e6eddac  agents/ledger.jsonl
AFTER : c70378fac2e380b46fb260f7cc52804158563f6863d24a0ebad6ff7f2e6eddac  agents/ledger.jsonl
```

dry-run 指令：

```bash
python3 policies/enforce.py --event /tmp/event.json \
  --registry agents/registry.json --bot-policy bot-policy.json \
  --ledger agents/ledger.jsonl --dry-run --now 2026-09-27T11:07:47+08:00
```

輸出 JSON 的 `decision.code` 為 `OK_HUMAN`，且無 `ledger_appended` 欄位；ledger 仍為
3 行。

## 檔案

- `policies/enforce.py`：dry-run 護欄 + `append_ledger()` 的 flock 原子附加。
- `tests/test_enforce_ledger.py`：新增測試。
- `reports/enforce-ledger-fix.md`：本報告。

未 commit、未 push（依約定由驗收者提交）。
