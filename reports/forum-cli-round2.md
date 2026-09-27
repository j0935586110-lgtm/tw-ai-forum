RN-20260927110519-3db9

已完成修正：

- `scripts/forum_cli.py:42-62,268-287`：寫入命令要求 `FORUM_TOKEN` 或 `GITHUB_TOKEN`；未設定時以 exit code 4 拒絕，只有 `--allow-gh-token` 才允許 `gh auth token`。新增 `--dry-run`，並確保寫入分支不呼叫 mutating client method。
- `scripts/forum_cli.py:175-193,216-264`：`post`、`reply`、`whoami` 回報解析出的 GitHub identity 與 token source；寫入輸出也涵蓋 JSON 模式。
- `scripts/forum_cli.py:291-310`：`--json` 錯誤輸出單一 JSON object，並保留錯誤 exit code。
- `tests/test_forum_cli.py:62-69,86-95,122-145`：補上 token 拒絕、dry-run 禁止 mutating call，以及 identity/source JSON 測試。

測試：

```text
........                                                   [100%]
8 passed, 14 subtests passed in 0.32s
```

未能修正：無。
