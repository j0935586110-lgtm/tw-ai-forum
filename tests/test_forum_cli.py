import contextlib
import io
import json
import os
import unittest
from unittest.mock import patch

from scripts import forum_cli
from mcp.forum_client import ForumError


class FakeForum:
    def __init__(self, *, dry_run=False, error=None):
        self.dry_run = dry_run
        self.token = "ghp_TEST_SHOULD_NOT_PRINT"
        self.error = error
        self.writes = []

    def _maybe_error(self):
        if self.error:
            raise ForumError(self.error)

    def create_discussion(self, title, body, category):
        self._maybe_error()
        self.writes.append(("post", title, body, category))
        return {"number": 7, "url": "https://example.test/7"}

    def add_comment(self, number, body):
        self._maybe_error()
        self.writes.append(("reply", number, body))
        return {"url": "https://example.test/comment"}

    def discussion(self, number):
        self._maybe_error()
        return {"number": number, "title": "Hello", "body": "Body",
                "user": {"login": "alice"}, "html_url": "https://example.test/7"}

    def comments(self, number):
        self._maybe_error()
        return [{"body": "Comment", "user": {"login": "bob"}}]

    def discussions(self, limit):
        self._maybe_error()
        return [{"number": 7, "title": "Hello", "body": "Body", "comments": 1,
                 "user": {"login": "alice"}, "html_url": "https://example.test/7"}]

    def _rest(self, method, path):
        self._maybe_error()
        self.assertions = (method, path)
        return {"login": "alice"}


class ForumCliTests(unittest.TestCase):
    def invoke(self, argv, client, env=None):
        out = io.StringIO()
        err = io.StringIO()
        with patch.dict(os.environ, env or {}, clear=False):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = forum_cli.main(argv, forum=client)
        return code, out.getvalue(), err.getvalue()

    def test_dry_run_does_not_write(self):
        client = FakeForum(dry_run=True)
        code, output, _ = self.invoke(["post", "--category", "general", "--title", "T",
                                       "--body", "B", "--json"], client,
                                      env={"FORUM_TOKEN": "test-token"})
        self.assertEqual(code, 0)
        self.assertEqual(client.writes, [])
        self.assertTrue(json.loads(output)["dry_run"])

    def test_every_subcommand_parses(self):
        cases = [
            ["post", "--category", "general", "--title", "T", "--body", "B"],
            ["reply", "7", "--body", "B"],
            ["read", "7"],
            ["list", "--limit", "2"],
            ["search", "hello", "--limit", "2"],
            ["whoami"],
        ]
        for argv in cases:
            with self.subTest(argv=argv):
                env = {"FORUM_TOKEN": "test-token"} if argv[0] in ("post", "reply") else None
                code, _, _ = self.invoke(argv, FakeForum(), env=env)
                self.assertEqual(code, 0)

    def test_write_without_explicit_token_is_denied(self):
        with patch.dict(os.environ, {"FORUM_TOKEN": "", "GITHUB_TOKEN": "", "GH_TOKEN": ""},
                        clear=False):
            code, output, _ = self.invoke(
                ["post", "--category", "general", "--title", "T", "--body", "B", "--json"],
                FakeForum())
        self.assertEqual(code, forum_cli.DENIED)
        data = json.loads(output)
        self.assertFalse(data["ok"])
        self.assertIn("FORUM_TOKEN", data["error"])

    def test_failure_exit_code_mapping(self):
        for message, expected in (("HTTP 404 Not Found", 3),
                                  ("HTTP 403 Forbidden", 4),
                                  ("HTTP 401 Unauthorized", 5),
                                  ("Connection reset", 5)):
            with self.subTest(message=message):
                code, _, _ = self.invoke(["list"], FakeForum(error=message))
                self.assertEqual(code, expected)

    def test_usage_error_is_two(self):
        code, _, _ = self.invoke(["reply", "7", "--body", ""], FakeForum())
        self.assertEqual(code, 2)

    def test_json_shapes(self):
        for argv, key in ((["read", "7", "--json"], "discussion"),
                          (["list", "--json"], "discussions"),
                          (["search", "hello", "--json"], "discussions"),
                          (["whoami", "--json"], "login")):
            with self.subTest(argv=argv):
                code, output, _ = self.invoke(argv, FakeForum())
                data = json.loads(output)
                self.assertEqual(code, 0)
                self.assertTrue(data["ok"])
                self.assertIn(key, data)

    def test_dry_run_never_calls_mutating_client_method(self):
        class NoMutationForum(FakeForum):
            def create_discussion(self, *args):
                raise AssertionError("mutating create_discussion called during dry-run")

            def add_comment(self, *args):
                raise AssertionError("mutating add_comment called during dry-run")

        client = NoMutationForum()
        code, output, _ = self.invoke(
            ["post", "--category", "general", "--title", "T", "--body", "B",
             "--dry-run", "--json"], client, env={"FORUM_TOKEN": "test-token"})
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output)["dry_run"], True)
        self.assertEqual(client.writes, [])

    def test_write_json_includes_identity_and_token_source(self):
        code, output, _ = self.invoke(
            ["reply", "7", "--body", "B", "--json"], FakeForum(),
            env={"FORUM_TOKEN": "test-token"})
        self.assertEqual(code, 0)
        data = json.loads(output)
        self.assertEqual(data["identity"], "alice")
        self.assertEqual(data["token_source"], "env:FORUM_TOKEN")


if __name__ == "__main__":
    unittest.main()
