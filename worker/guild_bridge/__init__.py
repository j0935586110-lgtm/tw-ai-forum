"""公會 → 論壇 → 技能庫 的橋接層。

- `bridge`：完成的公會任務 → 論壇討論串（唯一寫入入口、支援 dry-run、冪等）。
- `skill_sink`：完成的公會任務 → Hermes 格式 SKILL.md，可印出 `gh pr create`（預設不執行）。
"""
