RN-20260927105827-2c55

## Files created

- `scripts/forum_cli.py`
- `tests/test_forum_cli.py`
- `reports/forum-cli.md`

## Exact test command

```text
python3 -m unittest -v tests/test_forum_cli.py
```

## Raw test result

```text
test_dry_run_does_not_write (tests.test_forum_cli.ForumCliTests.test_dry_run_does_not_write) ... ok
test_every_subcommand_parses (tests.test_forum_cli.ForumCliTests.test_every_subcommand_parses) ... ok
test_failure_exit_code_mapping (tests.test_forum_cli.ForumCliTests.test_failure_exit_code_mapping) ... ok
test_json_shapes (tests.test_forum_cli.ForumCliTests.test_json_shapes) ... ok
test_usage_error_is_two (tests.test_forum_cli.ForumCliTests.test_usage_error_is_two) ... ok

----------------------------------------------------------------------
Ran 5 tests in 0.022s

OK
```

## Limitation

The existing `Forum` client does not expose a dedicated GitHub discussion-search method. The CLI search command therefore fetches up to 100 recent discussions through `Forum.discussions()` and filters title/body locally; it is not a repository-wide search.
