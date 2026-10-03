"""Stdlib-only tests for the M1.3 CIS drift checker and the M1.2 preview page.

Two subjects share this file because the milestone pairs them and the allowed
file list keeps them together:

* ``scripts/check_cis_drift.py`` - unit tests plus subprocess tests that mutate
  temporary copies of the forum CIS and of a fake guild checkout;
* ``docs/cis-preview.html`` - static, no-browser HTML parsing checks (landmarks,
  both product labels, canonical note, ``data-cis-token`` markers, no external
  resources, no placeholder artifacts).

The tests are hermetic: they never touch the network and never mutate the
committed ``docs/cis/`` files or the real Adventurers Guild checkout. They run
under pytest (plain functions) and, when pytest is absent, under the fallback
runner at the bottom of this file::

    python3 tests/test_cis_drift.py
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
from html.parser import HTMLParser
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts" / "check_cis_drift.py"
CIS_DIR = REPO_ROOT / "docs" / "cis"
PREVIEW_PATH = REPO_ROOT / "docs" / "cis-preview.html"

_spec = importlib.util.spec_from_file_location("check_cis_drift_under_test", MODULE_PATH)
assert _spec and _spec.loader
drift = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(drift)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _copy_forum_cis(destination: Path) -> Path:
    target = destination / "docs" / "cis"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(CIS_DIR, target)
    return target


def _write_guild_tokens(root: Path, rel: str, text: str) -> Path:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(MODULE_PATH), *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


def _run_default() -> subprocess.CompletedProcess:
    return _run()


def _replace_in(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, f"fixture text not found: {old!r}"
    path.write_text(text.replace(old, new), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Unit tests: token parsing and comparison
# --------------------------------------------------------------------------- #


def test_parse_tokens_reads_keys_and_values():
    text = ":root { --cis-color-paper: #f6f4ef; --cis-space-md: 16px; }"
    assert drift.parse_tokens(text) == [
        ("--cis-color-paper", "#f6f4ef"),
        ("--cis-space-md", "16px"),
    ]


def test_parse_tokens_ignores_comments():
    text = ":root { /* --cis-x: 1; */ --cis-y: 2; }"
    assert drift.parse_tokens(text) == [("--cis-y", "2")]


def test_token_map_reports_duplicates():
    mapping, duplicates = drift.token_map(
        [("--cis-x", "1"), ("--cis-x", "2"), ("--cis-y", "3")]
    )
    assert mapping["--cis-x"] == "1"
    assert duplicates == ["--cis-x"]


def test_compare_guild_tokens_flags_value_drift_and_missing_keys():
    canonical = {"--cis-color-network": "#3d5afe", "--cis-color-gold": "#8a5a12"}
    problems, _private = drift.compare_guild_tokens(
        canonical,
        [("--cis-color-network", "#ff0000")],
    )
    joined = "\n".join(problems)
    assert "value drift for '--cis-color-network'" in joined
    assert "missing canonical token key '--cis-color-gold'" in joined


def test_compare_guild_tokens_flags_extra_cis_key():
    canonical = {"--cis-color-network": "#3d5afe"}
    problems, _private = drift.compare_guild_tokens(
        canonical,
        [("--cis-color-network", "#3d5afe"), ("--cis-color-extra", "#123456")],
    )
    assert any("non-canonical CIS token key '--cis-color-extra'" in p for p in problems)


def test_compare_guild_tokens_allows_private_prefix():
    canonical = {"--cis-color-network": "#3d5afe"}
    problems, private = drift.compare_guild_tokens(
        canonical,
        [("--cis-color-network", "#3d5afe"), ("--guild-hero-height", "12rem")],
    )
    assert problems == []
    assert private == ["--guild-hero-height"]


def test_validate_manifest_accepts_committed_manifest():
    manifest = json.loads((CIS_DIR / "manifest.json").read_text(encoding="utf-8"))
    tokens = dict(drift.parse_tokens((CIS_DIR / "tokens.css").read_text(encoding="utf-8")))
    assert drift.validate_manifest(manifest, tokens, manifest.get("token_keys")) == []


def test_validate_manifest_rejects_wrong_source_of_truth():
    manifest = json.loads((CIS_DIR / "manifest.json").read_text(encoding="utf-8"))
    tokens = dict(drift.parse_tokens((CIS_DIR / "tokens.css").read_text(encoding="utf-8")))
    manifest["source_of_truth"] = "guild"
    problems = drift.validate_manifest(manifest, tokens, manifest.get("token_keys"))
    assert any("source_of_truth" in p for p in problems)


def test_self_audit_flags_forbidden_modules():
    assert drift.self_audit_problems("import socket\n") != []
    assert drift.self_audit_problems("import time\n") != []
    assert drift.self_audit_problems("import json\n") == []
    assert drift.self_audit_problems(MODULE_PATH.read_text(encoding="utf-8")) == []


# --------------------------------------------------------------------------- #
# Subprocess tests: the real repository and a fake guild checkout
# --------------------------------------------------------------------------- #


def test_default_run_passes_with_no_duplicate_source():
    result = _run_default()
    assert result.returncode == 0, result.stderr
    assert "cis drift: OK" in result.stdout
    assert "no duplicate source" in result.stdout


def test_default_run_is_deterministic():
    first = _run_default()
    second = _run_default()
    assert first.returncode == 0, first.stderr
    assert first.stdout == second.stdout


def test_no_duplicate_source_passes():
    with tempfile.TemporaryDirectory() as tmp:
        guild = Path(tmp) / "guild"
        guild.mkdir()
        result = _run("--guild-root", str(guild))
        assert result.returncode == 0, result.stderr
        assert "no duplicate source" in result.stdout


def test_missing_canonical_key_fails():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "forum"
        target = _copy_forum_cis(root) / "tokens.css"
        lines = target.read_text(encoding="utf-8").splitlines(keepends=True)
        kept = [line for line in lines if "--cis-color-gold: #8a5a12;" not in line]
        target.write_text("".join(kept), encoding="utf-8")
        guild = Path(tmp) / "guild"
        guild.mkdir()
        result = _run("--root", str(root), "--guild-root", str(guild))
        assert result.returncode != 0
        assert "missing" in result.stderr


def test_manifest_key_mismatch_fails():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "forum"
        target = _copy_forum_cis(root) / "manifest.json"
        data = json.loads(target.read_text(encoding="utf-8"))
        data["token_keys"] = [
            key for key in data["token_keys"] if key != "--cis-color-network"
        ]
        target.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        guild = Path(tmp) / "guild"
        guild.mkdir()
        result = _run("--root", str(root), "--guild-root", str(guild))
        assert result.returncode != 0
        assert "token_keys" in result.stderr


def test_divergent_duplicate_guild_tokens_fail():
    with tempfile.TemporaryDirectory() as tmp:
        guild = Path(tmp) / "guild"
        text = (CIS_DIR / "tokens.css").read_text(encoding="utf-8")
        text = text.replace("--cis-color-network: #3d5afe;", "--cis-color-network: #ff0000;")
        _write_guild_tokens(guild, "docs/cis/tokens.css", text)
        result = _run("--guild-root", str(guild))
        assert result.returncode != 0
        assert "value drift" in result.stderr


def test_divergent_duplicate_guild_missing_key_fails():
    with tempfile.TemporaryDirectory() as tmp:
        guild = Path(tmp) / "guild"
        text = (CIS_DIR / "tokens.css").read_text(encoding="utf-8")
        lines = [line for line in text.splitlines(keepends=True) if "--cis-color-gold: #8a5a12;" not in line]
        _write_guild_tokens(guild, "src/cis/tokens.css", "".join(lines))
        result = _run("--guild-root", str(guild))
        assert result.returncode != 0
        assert "missing canonical token key" in result.stderr


def test_synced_guild_copy_is_allowed():
    with tempfile.TemporaryDirectory() as tmp:
        guild = Path(tmp) / "guild"
        text = (CIS_DIR / "tokens.css").read_text(encoding="utf-8")
        _write_guild_tokens(guild, "docs/cis/tokens.css", text)
        result = _run("--guild-root", str(guild))
        assert result.returncode == 0, result.stderr
        assert "synced" in result.stdout


def test_guild_manifest_claiming_own_source_fails():
    with tempfile.TemporaryDirectory() as tmp:
        guild = Path(tmp) / "guild"
        text = (CIS_DIR / "tokens.css").read_text(encoding="utf-8")
        _write_guild_tokens(guild, "public/cis/tokens.css", text)
        manifest = {"schema": "cis-manifest/v0.1", "source_of_truth": "guild"}
        _write_guild_tokens(
            guild, "public/cis/manifest.json", json.dumps(manifest, indent=2)
        )
        result = _run("--guild-root", str(guild))
        assert result.returncode != 0
        assert "source_of_truth" in result.stderr


def test_checker_does_not_modify_guild_checkout():
    with tempfile.TemporaryDirectory() as tmp:
        guild = Path(tmp) / "guild"
        text = (CIS_DIR / "tokens.css").read_text(encoding="utf-8")
        path = _write_guild_tokens(guild, "docs/cis/tokens.css", text)
        before = path.read_bytes()
        result = _run("--guild-root", str(guild))
        assert result.returncode == 0, result.stderr
        assert path.read_bytes() == before


def test_scan_guild_reports_copy_count():
    with tempfile.TemporaryDirectory() as tmp:
        guild = Path(tmp) / "guild"
        text = (CIS_DIR / "tokens.css").read_text(encoding="utf-8")
        _write_guild_tokens(guild, "docs/cis/tokens.css", text)
        _write_guild_tokens(guild, "assets/tokens.css", text)
        canonical = dict(drift.parse_tokens(text))
        report = drift.scan_guild(guild, canonical)
        assert report["copies"] == 2
        assert report["synced_copies"] == 2
        assert report["duplicate_mother_source"] == 0


# --------------------------------------------------------------------------- #
# Static HTML checks for docs/cis-preview.html
# --------------------------------------------------------------------------- #

_SKIP_CONTENT = frozenset({"script", "style", "template"})
_RESOURCE_TAGS = frozenset(
    {"link", "script", "img", "source", "iframe", "video", "audio", "track", "embed", "object"}
)
_REQUIRED_COMPONENTS = frozenset(
    {
        "header",
        "footer",
        "cta",
        "card",
        "agent-marker",
        "mobile-navigation",
        "empty-state",
        "error-state",
        "focus-ring",
    }
)


class _HtmlProbe(HTMLParser):
    """Collect the facts the static preview checks need."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags = []
        self.resources = []
        self.tokens = []
        self.components = []
        self.products = []
        self.text_parts = []
        self.headings = []
        self.focusable = 0
        self._skip_depth = 0
        self._heading_tag = None
        self._heading_buffer = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        attributes = dict(attrs)
        if tag in _SKIP_CONTENT:
            self._skip_depth += 1
        if tag in _RESOURCE_TAGS:
            for name in ("src", "href", "srcset", "data"):
                value = attributes.get(name)
                if value:
                    self.resources.append((tag, name, value))
        for name, bucket in (
            ("data-cis-token", self.tokens),
            ("data-cis-component", self.components),
            ("data-cis-product", self.products),
        ):
            if attributes.get(name):
                bucket.append(attributes[name])
        if tag in ("a", "button", "input", "select", "textarea") or "tabindex" in attributes:
            self.focusable += 1
        if re.fullmatch(r"h[1-6]", tag):
            self._heading_tag = tag
            self._heading_buffer = []

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag in _SKIP_CONTENT and self._skip_depth:
            self._skip_depth -= 1
        if re.fullmatch(r"h[1-6]", tag) and self._heading_tag == tag:
            self._heading_tag = None

    def handle_endtag(self, tag):
        if tag in _SKIP_CONTENT and self._skip_depth:
            self._skip_depth -= 1
        if re.fullmatch(r"h[1-6]", tag) and self._heading_tag == tag:
            self.headings.append((tag, "".join(self._heading_buffer).strip()))
            self._heading_tag = None

    def handle_data(self, data):
        if self._skip_depth:
            return
        if data.strip():
            self.text_parts.append(data)
        if self._heading_tag:
            self._heading_buffer.append(data)

    @property
    def text(self) -> str:
        return " ".join(self.text_parts)


def _probe_preview() -> _HtmlProbe:
    probe = _HtmlProbe()
    probe.feed(PREVIEW_PATH.read_text(encoding="utf-8"))
    probe.close()
    return probe


def test_preview_has_landmarks_and_one_main_heading():
    probe = _probe_preview()
    assert "html" in probe.tags
    assert "header" in probe.tags
    assert "main" in probe.tags
    assert "footer" in probe.tags
    h1s = [text for tag, text in probe.headings if tag == "h1"]
    assert len(h1s) == 1
    assert h1s[0]


def test_preview_renders_both_products_separately():
    probe = _probe_preview()
    assert set(probe.products) == {"forum", "guild"}
    assert "Forum" in probe.text
    assert "Adventurers Guild" in probe.text


def test_preview_states_canonical_source():
    text = _probe_preview().text.lower()
    assert "canonical" in text
    assert "source of truth" in text
    assert "synchronized" in text
    assert "adventurers guild" in text


def test_preview_loads_tokens_relatively_and_no_external_resources():
    probe = _probe_preview()
    assert any(
        tag == "link" and name == "href" and value == "cis/tokens.css"
        for tag, name, value in probe.resources
    )
    for tag, name, value in probe.resources:
        assert not re.match(r"^(?:https?:)?//", value), f"external resource {tag} {name}={value}"


def test_preview_has_data_cis_token_markers():
    probe = _probe_preview()
    assert len(probe.tokens) >= 30
    assert all(token.startswith("--cis-") for token in probe.tokens)
    required = {
        "--cis-color-network",
        "--cis-color-agent",
        "--cis-color-paper-sunken",
        "--cis-color-danger",
        "--cis-focus-ring",
        "--cis-font-size-lg",
        "--cis-layout-header-height",
    }
    assert required <= set(probe.tokens)


def test_preview_token_markers_are_canonical():
    manifest = json.loads((CIS_DIR / "manifest.json").read_text(encoding="utf-8"))
    canonical = set(manifest["token_keys"])
    referenced = set(_probe_preview().tokens)
    assert referenced <= canonical, f"unknown tokens: {sorted(referenced - canonical)}"


def test_preview_covers_required_components():
    components = set(_probe_preview().components)
    assert _REQUIRED_COMPONENTS <= components


def test_preview_has_focusable_controls():
    assert _probe_preview().focusable >= 5


def test_preview_has_no_placeholder_artifacts():
    text = _probe_preview().text.lower()
    assert "undefined" not in text
    assert "null" not in text
    assert "[object object]" not in text


def test_preview_labels_content_as_specimen():
    text = _probe_preview().text.lower()
    assert "specimen" in text
    assert "樣本" in text


# --------------------------------------------------------------------------- #
# Stdlib fallback runner
# --------------------------------------------------------------------------- #


def main() -> int:
    tests = sorted(
        (name, value)
        for name, value in list(globals().items())
        if name.startswith("test_") and callable(value)
    )
    failures = []
    for name, function in tests:
        try:
            function()
        except Exception as exc:  # noqa: BLE001 - the runner reports everything
            failures.append((name, exc))
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
        else:
            print(f"PASS {name}")
    print()
    print(f"{len(tests) - len(failures)} passed, {len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
