"""Stdlib-only tests for the Editorial Network CIS v0.1 checker.

The tests are hermetic: they never touch the network and they never mutate the
committed CIS files. Mutation cases copy ``docs/cis/`` into a temporary
directory and point the checker at it with ``--root``.

They run under pytest (plain functions with no fixtures) and, if pytest is not
installed, under the fallback runner at the bottom of this file::

    python3 tests/test_cis.py
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts" / "check_cis.py"
CIS_DIR = REPO_ROOT / "docs" / "cis"

_spec = importlib.util.spec_from_file_location("check_cis_under_test", MODULE_PATH)
assert _spec and _spec.loader
cis = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cis)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _copy_cis(destination: Path) -> Path:
    target = destination / "docs" / "cis"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(CIS_DIR, target)
    return target


def _run(root: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(MODULE_PATH), "--root", str(root), *extra],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


def _run_default(*extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(MODULE_PATH), *extra],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


def _replace_in(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, f"fixture text not found: {old!r}"
    path.write_text(text.replace(old, new), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Unit tests
# --------------------------------------------------------------------------- #


def test_parse_token_declarations_reads_keys_and_values():
    text = ":root { --cis-color-paper: #f6f4ef; --cis-space-md: 16px; }"
    assert cis.parse_token_declarations(text) == [
        ("--cis-color-paper", "#f6f4ef"),
        ("--cis-space-md", "16px"),
    ]


def test_parse_token_declarations_reports_duplicates_by_count():
    text = ":root { --cis-x: 1; --cis-x: 2; }"
    keys = [key for key, _value in cis.parse_token_declarations(text)]
    assert keys.count("--cis-x") == 2


def test_syntax_balance_accepts_balanced_css_and_rejects_unclosed():
    assert cis.syntax_balance_problems(":root { --x: var(--y); }") == []
    assert cis.syntax_balance_problems(":root { --x: rgba(0, 0, 0; }")


def test_is_purple_family_keeps_indigo_and_teal():
    assert cis.is_purple_family(*cis._hex_to_rgb("#8b5cf6")) is True
    assert cis.is_purple_family(*cis._hex_to_rgb("#3d5afe")) is False
    assert cis.is_purple_family(*cis._hex_to_rgb("#2f7d78")) is False


def test_check_tokens_css_accepts_committed_tokens():
    assert cis.check_tokens_css((CIS_DIR / "tokens.css").read_text(encoding="utf-8")) == []


def test_self_audit_flags_network_imports():
    assert cis.self_audit_problems("import socket\n") != []
    assert cis.self_audit_problems("import json\n") == []
    assert cis.self_audit_problems(MODULE_PATH.read_text(encoding="utf-8")) == []


def test_build_manifest_matches_required_keys():
    manifest = cis.build_manifest()
    assert manifest["schema"] == cis.MANIFEST_SCHEMA
    assert manifest["source_of_truth"] == "forum"
    assert manifest["network_used"] is False
    assert manifest["token_keys"] == sorted(cis.REQUIRED_TOKEN_KEYS)


# --------------------------------------------------------------------------- #
# Subprocess smoke tests against the real repository
# --------------------------------------------------------------------------- #


def test_default_run_passes_and_validates_manifest():
    result = _run_default()
    assert result.returncode == 0, result.stderr
    assert "cis: OK" in result.stdout


def test_default_run_does_not_mutate_committed_manifest():
    manifest = CIS_DIR / "manifest.json"
    before = manifest.read_bytes() if manifest.is_file() else None
    result = _run_default()
    assert result.returncode == 0, result.stderr
    after = manifest.read_bytes() if manifest.is_file() else None
    assert after == before


# --------------------------------------------------------------------------- #
# Mutation tests on a temporary copy
# --------------------------------------------------------------------------- #


def test_temporary_copy_passes():
    with tempfile.TemporaryDirectory() as tmp:
        _copy_cis(Path(tmp))
        result = _run(Path(tmp))
        assert result.returncode == 0, result.stderr


def test_missing_token_key_fails():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        target = _copy_cis(root) / "tokens.css"
        lines = target.read_text(encoding="utf-8").splitlines(keepends=True)
        kept = [line for line in lines if "--cis-color-gold: #8a5a12;" not in line]
        target.write_text("".join(kept), encoding="utf-8")
        result = _run(root)
        assert result.returncode != 0
        assert "missing token key '--cis-color-gold'" in result.stderr


def test_duplicate_token_key_fails():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        target = _copy_cis(root) / "tokens.css"
        lines = target.read_text(encoding="utf-8").splitlines(keepends=True)
        output = []
        for line in lines:
            output.append(line)
            if "--cis-color-network: #3d5afe;" in line:
                output.append(line)
        target.write_text("".join(output), encoding="utf-8")
        result = _run(root)
        assert result.returncode != 0
        assert "duplicate token key '--cis-color-network'" in result.stderr


def test_purple_value_fails():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        target = _copy_cis(root) / "tokens.css"
        _replace_in(target, "#3d5afe", "#8b5cf6")
        result = _run(root)
        assert result.returncode != 0
        assert "purple-family" in result.stderr


def test_gradient_value_fails():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        target = _copy_cis(root) / "tokens.css"
        _replace_in(target, "#3d5afe", "linear-gradient(#ffffff, #000000)")
        result = _run(root)
        assert result.returncode != 0
        assert "gradient" in result.stderr


def test_em_dash_in_copy_fails():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        readme = _copy_cis(root) / "README.md"
        with readme.open("a", encoding="utf-8") as handle:
            handle.write("\nUnsupported statement \u2014 remove me.\n")
        result = _run(root)
        assert result.returncode != 0
        assert "em dash" in result.stderr


def test_write_manifest_round_trip_is_deterministic():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        target = _copy_cis(root)
        (target / "manifest.json").unlink(missing_ok=True)
        first = _run(root, "--write-manifest")
        assert first.returncode == 0, first.stderr
        manifest = target / "manifest.json"
        assert manifest.is_file()
        first_bytes = manifest.read_bytes()
        data = json.loads(first_bytes)
        assert data["schema"] == cis.MANIFEST_SCHEMA

        second = _run(root, "--write-manifest")
        assert second.returncode == 0, second.stderr
        assert manifest.read_bytes() == first_bytes

        validate = _run(root)
        assert validate.returncode == 0, validate.stderr


def test_non_canonical_token_key_fails():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        target = _copy_cis(root) / "tokens.css"
        _replace_in(target, "--cis-color-paper: #f6f4ef;", "--cis-color-paper: #f6f4ef;\n  --cis-color-extra: #123456;")
        result = _run(root)
        assert result.returncode != 0
        assert "non-canonical token key '--cis-color-extra'" in result.stderr


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
