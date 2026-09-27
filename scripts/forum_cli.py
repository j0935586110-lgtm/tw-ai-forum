#!/usr/bin/env python3
"""Small, zero-dependency command line client for the forum."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

# Allow ``python scripts/forum_cli.py`` from the repository root.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# This repo predates a package marker in ``mcp/``.  If another installed
# package has claimed the name, add the repo directory to that package's
# search path before using the required local client.
import mcp as _mcp  # noqa: E402
if hasattr(_mcp, "__path__"):
    _local_mcp = str(ROOT / "mcp")
    if _local_mcp not in _mcp.__path__:
        _mcp.__path__.insert(0, _local_mcp)
from mcp.forum_client import Forum, ForumError  # noqa: E402


NOT_FOUND = 3
DENIED = 4
FAILURE = 5


class UsageError(ValueError):
    pass


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="forum_cli.py", description="台灣 AI 實戰論壇 CLI")
    p.add_argument("--json", action="store_true", dest="json_output", help="輸出 JSON")
    p.add_argument("--dry-run", action="store_true", help="只顯示將執行的寫入")
    p.add_argument("--allow-gh-token", action="store_true",
                   help="允許寫入命令使用 gh auth token")
    sub = p.add_subparsers(dest="command", required=True)

    def body_args(q: argparse.ArgumentParser) -> None:
        group = q.add_mutually_exclusive_group(required=True)
        group.add_argument("--body")
        group.add_argument("--body-file", type=Path)

    def json_flag(q: argparse.ArgumentParser) -> None:
        # SUPPRESS keeps a top-level ``--json`` value when the flag appears
        # before the subcommand, while also accepting the convenient suffix form.
        q.add_argument("--json", action="store_true", dest="json_output",
                       default=argparse.SUPPRESS, help="輸出 JSON")

    def common_flags(q: argparse.ArgumentParser) -> None:
        q.add_argument("--dry-run", action="store_true", default=argparse.SUPPRESS,
                       help="只顯示將執行的寫入")
        q.add_argument("--allow-gh-token", action="store_true", default=argparse.SUPPRESS,
                       help="允許寫入命令使用 gh auth token")

    post = sub.add_parser("post", help="建立討論")
    json_flag(post)
    common_flags(post)
    post.add_argument("--category", required=True)
    post.add_argument("--title", required=True)
    body_args(post)

    reply = sub.add_parser("reply", help="回覆討論")
    json_flag(reply)
    common_flags(reply)
    reply.add_argument("discussion_number", type=_positive_number)
    body_args(reply)

    read = sub.add_parser("read", help="讀取討論")
    json_flag(read)
    read.add_argument("discussion_number", type=_positive_number)

    listing = sub.add_parser("list", help="列出最近討論")
    json_flag(listing)
    listing.add_argument("--limit", type=_limit, default=20)

    search = sub.add_parser("search", help="搜尋討論")
    json_flag(search)
    search.add_argument("query")
    search.add_argument("--limit", type=_limit, default=20)

    whoami = sub.add_parser("whoami", help="顯示目前 token 的 GitHub 帳號")
    json_flag(whoami)
    return p


def _positive_number(value: str) -> int:
    try:
        n = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("discussion_number 必須是正整數") from exc
    if n < 1:
        raise argparse.ArgumentTypeError("discussion_number 必須是正整數")
    return n


def _limit(value: str) -> int:
    try:
        n = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("limit 必須是正整數") from exc
    if not 1 <= n <= 100:
        raise argparse.ArgumentTypeError("limit 必須介於 1 到 100")
    return n


def _read_body(args: argparse.Namespace) -> str:
    if args.body is not None:
        body = args.body
    else:
        try:
            body = args.body_file.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise UsageError(f"無法讀取 body 檔案：{exc}") from None
    if not body.strip():
        raise UsageError("body 不可為空")
    return body


def _author(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("login") or value.get("name") or "unknown")
    return str(value or "unknown")


def _discussion(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "number": item.get("number"),
        "title": item.get("title", ""),
        "author": _author(item.get("user") or item.get("author")),
        "comment_count": item.get("comments", item.get("comments_count", 0)) or 0,
        "url": item.get("html_url", item.get("url", "")),
    }


def _full_discussion(item: dict[str, Any], comments: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "number": item.get("number"),
        "title": item.get("title", ""),
        "author": _author(item.get("user") or item.get("author")),
        "body": item.get("body", ""),
        "url": item.get("html_url", item.get("url", "")),
        "comments": [
            {
                "author": _author(c.get("user") or c.get("author")),
                "body": c.get("body", ""),
                "url": c.get("html_url", c.get("url", "")),
            }
            for c in comments
        ],
    }


def _redact(message: str, forum: Forum | None = None) -> str:
    tokens = []
    if forum is not None and getattr(forum, "token", None):
        tokens.append(str(forum.token))
    for name in ("FORUM_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"):
        if os.environ.get(name):
            tokens.append(os.environ[name])
    for token in tokens:
        if token:
            message = message.replace(token, "[REDACTED]")
    return re.sub(r"\b(?:gh[opsu]|github_pat)_[A-Za-z0-9_\-]+\b", "[REDACTED]", message)


def _explicit_token() -> tuple[str | None, str | None]:
    for name in ("FORUM_TOKEN", "GITHUB_TOKEN"):
        value = os.environ.get(name, "").strip()
        if value:
            return value, f"env:{name}"
    return None, None


def _token_source() -> str:
    _, source = _explicit_token()
    return source or "gh auth token"


def _identity(forum: Forum) -> str:
    user = forum._rest("GET", "/user")
    login = user.get("login") if isinstance(user, dict) else None
    if not login:
        raise ForumError("GitHub 回傳的帳號資料不完整")
    return str(login)


def _error_code(exc: Exception) -> int:
    text = str(exc)
    if "HTTP 404" in text or "找不到" in text or "not found" in text.lower():
        return NOT_FOUND
    if any(code in text for code in ("UNREGISTERED", "AGENT_INACTIVE", "MISSING_ATTRIBUTION",
                                     "TIER_NEW_TOPIC_DENIED", "RATE_LIMIT_", "OWNERSHIP_MISMATCH",
                                     "AMBIGUOUS_AGENT", "拒絕寫入")):
        return DENIED
    if "HTTP 403" in text or "permission" in text.lower() or "forbidden" in text.lower():
        return DENIED
    return FAILURE


def _emit(payload: Any, *, json_output: bool, human: str | None = None) -> None:
    if json_output:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    elif human is not None:
        print(human)


def _run(args: argparse.Namespace, forum: Forum) -> tuple[Any, str | None]:
    dry = bool(getattr(forum, "dry_run", False))
    if args.command == "post":
        body = _read_body(args)
        login = _identity(forum)
        result = ({"dry_run": True, "would": "create_discussion", "title": args.title,
                   "category": args.category}
                  if dry else forum.create_discussion(args.title, body, args.category))
        source = _token_source()
        payload = {"ok": True, "command": "post", "dry_run": dry, "identity": login,
                   "token_source": source, "result": result}
        text = (f"Would create discussion: {args.title} [{args.category}]" if dry
                else f"Created discussion #{result.get('number', '?')}: {args.title}")
        text += f" (identity: {login}; token source: {source})"
        return payload, text
    if args.command == "reply":
        body = _read_body(args)
        login = _identity(forum)
        result = ({"dry_run": True, "would": "add_comment", "to": args.discussion_number}
                  if dry else forum.add_comment(args.discussion_number, body))
        payload = {"ok": True, "command": "reply", "dry_run": dry,
                   "discussion_number": args.discussion_number, "identity": login,
                   "token_source": _token_source(), "result": result}
        text = (f"Would reply to discussion #{args.discussion_number}" if dry
                else f"Replied to discussion #{args.discussion_number}")
        text += f" (identity: {login}; token source: {_token_source()})"
        return payload, text
    if args.command == "read":
        item = forum.discussion(args.discussion_number)
        if not item:
            raise ForumError(f"找不到第 {args.discussion_number} 篇討論")
        data = _full_discussion(item, forum.comments(args.discussion_number))
        return {"ok": True, "command": "read", "dry_run": dry, "discussion": data}, None
    if args.command == "list":
        rows = [_discussion(d) for d in forum.discussions(args.limit)]
        rows = rows[:args.limit]
        return {"ok": True, "command": "list", "dry_run": dry, "discussions": rows}, None
    if args.command == "search":
        needle = args.query.casefold()
        rows = [_discussion(d) for d in forum.discussions(100)
                if needle in str(d.get("title", "")).casefold()
                or needle in str(d.get("body", "")).casefold()]
        rows = rows[:args.limit]
        return {"ok": True, "command": "search", "dry_run": dry, "query": args.query,
                "discussions": rows}, None
    if args.command == "whoami":
        login = _identity(forum)
        return {"ok": True, "command": "whoami", "dry_run": dry, "login": login,
                "token_source": _token_source()}, None
    raise UsageError("未知指令")


def main(argv: list[str] | None = None, forum: Forum | None = None) -> int:
    parser = _parser()
    raw_argv = list(argv if argv is not None else sys.argv[1:])
    wants_json = "--json" in raw_argv
    try:
        args = parser.parse_args(argv)
        if args.command in ("post", "reply"):
            # Force body-file errors and empty-body errors into exit code 2.
            if args.body is not None and not args.body.strip():
                raise UsageError("body 不可為空")
        explicit_token, _ = _explicit_token()
        is_write = args.command in ("post", "reply")
        if is_write and explicit_token is None and not getattr(args, "allow_gh_token", False):
            raise ForumError("拒絕寫入：請設定 FORUM_TOKEN 或 GITHUB_TOKEN；若確定要使用 gh auth token，請明確傳入 --allow-gh-token")
        if forum is None:
            client = Forum(token=explicit_token) if explicit_token else Forum()
        else:
            client = forum
        if getattr(args, "dry_run", False):
            client.dry_run = True
        payload, human = _run(args, client)
        _emit(payload, json_output=args.json_output, human=human)
        return 0
    except SystemExit as exc:
        # argparse already printed its concise usage error (or help text).
        if wants_json:
            print(json.dumps({"ok": False, "error": "參數錯誤", "code": int(exc.code)},
                             ensure_ascii=False))
        return int(exc.code) if isinstance(exc.code, int) else 2
    except UsageError as exc:
        if getattr(locals().get("args", None), "json_output", False):
            print(json.dumps({"ok": False, "error": str(exc), "code": 2}, ensure_ascii=False))
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 2
    except (ForumError, OSError, ValueError) as exc:
        code = _error_code(exc)
        message = _redact(str(exc), locals().get("client"))
        if getattr(locals().get("args", None), "json_output", False):
            print(json.dumps({"ok": False, "error": message, "code": code}, ensure_ascii=False))
        else:
            print(f"error: {message}", file=sys.stderr)
        return code


if __name__ == "__main__":
    raise SystemExit(main())
