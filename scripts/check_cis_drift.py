#!/usr/bin/env python3
"""Deterministic CIS drift checker for the forum x Adventurers Guild plan (M1.3).

The forum repository under ``docs/cis/`` is the single canonical CIS mother
source. This script keeps it that way:

* it validates the canonical token layer in ``docs/cis/tokens.css`` against the
  generated index in ``docs/cis/manifest.json``;
* it scans the Adventurers Guild checkout for a duplicate CIS mother source
  under ``docs/cis``, ``src/cis``, ``public/cis`` and for obvious ``tokens.css``
  copies elsewhere in that tree;
* a synced copy that matches the canonical keys and values exactly is allowed;
* a missing, extra, or redefined ``--cis-`` token, a value that differs from the
  canonical value, or a guild manifest that claims its own source of truth is
  reported as drift and makes the run exit non-zero.

It reads **local files only**. It never opens a socket, never reads a secret,
and never prints a timestamp. Run it from the forum repository root::

    python3 scripts/check_cis_drift.py
    python3 scripts/check_cis_drift.py --guild-root /path/to/guild
    python3 scripts/check_cis_drift.py --root /path/to/forum --guild-root /tmp/guild

The guild checkout is opened read-only: nothing in this script writes to it.
Only the Python standard library is used.
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

CIS_DIR_REL = "docs/cis"
TOKENS_FILE = "tokens.css"
MANIFEST_FILE = "manifest.json"

MANIFEST_SCHEMA = "cis-manifest/v0.1"
CANONICAL_SOURCE_PRODUCT = "forum"
CANONICAL_CHECKER_REL = "scripts/check_cis.py"

DEFAULT_GUILD_ROOT = "/home/j/projects/adventurers-guild"
GUILD_ROOT_ENV = "GUILD_REPO"

# Directories inside the guild checkout that are checked as a potential CIS
# mother source, relative to the guild root. These are the conventional CIS
# locations named by the M1.3 milestone.
GUILD_CIS_ROOTS = ("docs/cis", "src/cis", "public/cis")

# Obvious file names for a token-layer copy. The canonical name is tokens.css;
# the others catch hyphenated variants a consumer might generate.
TOKENS_COPY_NAMES = frozenset(
    {"tokens.css", "cis-tokens.css", "tokens-cis.css", "cis.tokens.css"}
)

# Generated, vendored, and tooling directories are not CIS mother sources.
IGNORED_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".cache",
        ".venv",
        "venv",
        "env",
        "dist",
        "build",
        "out",
        "coverage",
        "vendor",
        ".next",
        ".nuxt",
        ".svelte-kit",
    }
)

MAX_WALK_DEPTH = 8

DECLARATION_RE = re.compile(r"(--[A-Za-z0-9_-]+)\s*:\s*([^;{}]+);")
CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)

# This checker must stay offline and non-deterministic-free. Both sets are
# audited by parsing this script's own source, so a future edit cannot quietly
# add a network call, a clock read, or a random decision.
FORBIDDEN_MODULES = frozenset(
    {
        "socket",
        "ssl",
        "http",
        "urllib",
        "ftplib",
        "smtplib",
        "telnetlib",
        "xmlrpc",
        "requests",
        "httpx",
        "aiohttp",
        "webbrowser",
        "time",
        "datetime",
        "random",
        "uuid",
        "secrets",
    }
)


# --------------------------------------------------------------------------- #
# Generic helpers
# --------------------------------------------------------------------------- #


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def strip_css_comments(text: str) -> str:
    return CSS_COMMENT_RE.sub(" ", text)


def parse_tokens(text: str) -> list:
    """Return ``(key, value)`` pairs in source order, comments removed."""

    return [
        (match.group(1), match.group(2).strip())
        for match in DECLARATION_RE.finditer(strip_css_comments(text))
    ]


def token_map(pairs: list):
    """Return ``(mapping, duplicate_keys)``. First declaration wins."""

    mapping = {}
    counts = {}
    for key, value in pairs:
        counts[key] = counts.get(key, 0) + 1
        mapping.setdefault(key, value)
    duplicates = sorted(key for key, count in counts.items() if count > 1)
    return mapping, duplicates


def dedupe(items) -> list:
    seen = set()
    output = []
    for item in items:
        if item not in seen:
            seen.add(item)
            output.append(item)
    return output


# --------------------------------------------------------------------------- #
# Canonical source validation
# --------------------------------------------------------------------------- #


def load_canonical_checker():
    """Load ``scripts/check_cis.py`` so its key list stays the single source.

    Returns the module, or ``None`` when it cannot be loaded. The canonical
    token key set lives in that checker; reusing it stops the two checkers from
    disagreeing about the v0.1 key list.
    """

    checker = Path(__file__).resolve().with_name("check_cis.py")
    if not checker.is_file():
        return None
    try:
        spec = importlib.util.spec_from_file_location(
            "_cis_canonical_checker", checker
        )
        if spec and spec.loader:
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    except Exception:  # noqa: BLE001 - the caller falls back to the manifest
        return None
    return None


def validate_manifest(manifest, tokens, manifest_keys):
    """Validate a manifest object against a token map. Returns problems."""

    problems = []
    if not isinstance(manifest, dict):
        return ["manifest.json: root must be a JSON object"]

    if manifest.get("schema") != MANIFEST_SCHEMA:
        problems.append(f"manifest.json: schema must be {MANIFEST_SCHEMA!r}")
    if manifest.get("source_of_truth") != CANONICAL_SOURCE_PRODUCT:
        problems.append(
            "manifest.json: source_of_truth must be "
            f"{CANONICAL_SOURCE_PRODUCT!r}"
        )
    if manifest.get("network_used") is not False:
        problems.append("manifest.json: network_used must be false")

    listed = list(manifest_keys or [])
    token_keys = sorted(tokens)
    if sorted(listed) != token_keys:
        missing = sorted(set(token_keys) - set(listed))
        extra = sorted(set(listed) - set(token_keys))
        for key in missing:
            problems.append(
                f"manifest.json: token_keys is missing canonical key {key!r}"
            )
        for key in extra:
            problems.append(
                f"manifest.json: token_keys lists key {key!r} not present in "
                "tokens.css"
            )

    groups = manifest.get("token_groups")
    if not isinstance(groups, dict):
        problems.append("manifest.json: token_groups must be an object")
    else:
        seen = {}
        for name in sorted(groups):
            members = groups[name]
            if not isinstance(members, list):
                problems.append(
                    f"manifest.json: token_groups.{name} must be a list"
                )
                continue
            for key in members:
                if key in seen:
                    problems.append(
                        "manifest.json: token key "
                        f"{key!r} appears in both groups {seen[key]!r} and "
                        f"{name!r}"
                    )
                else:
                    seen[key] = name
        grouped = sorted(seen)
        if grouped != token_keys:
            for key in sorted(set(token_keys) - set(grouped)):
                problems.append(
                    f"manifest.json: token key {key!r} is in no token group"
                )
            for key in sorted(set(grouped) - set(token_keys)):
                problems.append(
                    f"manifest.json: token group lists unknown key {key!r}"
                )

    values = manifest.get("token_values")
    if values is not None:
        if not isinstance(values, dict):
            problems.append("manifest.json: token_values must be an object")
        else:
            for key in sorted(values):
                if key in tokens and values[key] != tokens[key]:
                    problems.append(
                        f"manifest.json: token_values[{key!r}] is "
                        f"{values[key]!r} but tokens.css declares "
                        f"{tokens[key]!r}"
                    )
    return problems


def validate_canonical(root: Path):
    """Validate the forum-side canonical source.

    Returns ``(problems, token_map, key_source_label)``. ``token_map`` is empty
    when the canonical tokens could not be parsed.
    """

    problems = []
    cis_dir = root / CIS_DIR_REL
    tokens_path = cis_dir / TOKENS_FILE
    manifest_path = cis_dir / MANIFEST_FILE

    if not tokens_path.is_file():
        problems.append(f"missing canonical file: {CIS_DIR_REL}/{TOKENS_FILE}")
        return problems, {}, "unavailable"

    declarations = parse_tokens(read_text(tokens_path))
    if not declarations:
        problems.append("tokens.css: no custom-property declarations found")
        return problems, {}, "unavailable"

    tokens, duplicates = token_map(declarations)
    for key in duplicates:
        problems.append(f"tokens.css: duplicate token key {key!r}")
    for key in sorted(tokens):
        if tokens[key] == "":
            problems.append(f"tokens.css: token key {key!r} has an empty value")

    manifest = None
    manifest_keys = None
    if not manifest_path.is_file():
        problems.append(f"missing canonical file: {CIS_DIR_REL}/{MANIFEST_FILE}")
    else:
        try:
            manifest = json.loads(read_text(manifest_path))
        except (OSError, json.JSONDecodeError) as exc:
            problems.append(f"manifest.json: not valid JSON ({exc})")
            manifest = None
        if manifest is not None:
            manifest_keys = manifest.get("token_keys") if isinstance(manifest, dict) else None
            problems.extend(validate_manifest(manifest, tokens, manifest_keys))

    # The M1.1 checker owns the authoritative key list and the deeper checks
    # (required files, forbidden design tells, syntax balance, manifest
    # equality). Load it once and fall back to the committed manifest when it
    # cannot be imported.
    checker_module = load_canonical_checker()
    canonical_keys = tuple(
        getattr(checker_module, "REQUIRED_TOKEN_KEYS", ()) or ()
    )
    key_source = CANONICAL_CHECKER_REL if canonical_keys else "unavailable"
    if not canonical_keys and manifest_keys:
        canonical_keys = tuple(sorted(manifest_keys))
        key_source = MANIFEST_FILE

    if canonical_keys:
        for key in canonical_keys:
            if key not in tokens:
                problems.append(f"tokens.css: missing canonical token key {key!r}")
        for key in sorted(tokens):
            if key not in canonical_keys:
                problems.append(f"tokens.css: non-canonical token key {key!r}")
    else:
        problems.append("canonical token key list is unavailable")

    if checker_module is not None:
        try:
            extra, _manifest = checker_module.check_cis(root, manifest_path)
            problems.extend(extra)
        except Exception as exc:  # noqa: BLE001 - report instead of crashing
            problems.append(f"canonical checker could not run ({type(exc).__name__})")

    return dedupe(problems), tokens, key_source


# --------------------------------------------------------------------------- #
# Guild-side duplicate source detection
# --------------------------------------------------------------------------- #


def _relative_label(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def discover_guild(guild_root: Path):
    """Return ``(roots_present, copies)`` for a guild checkout.

    ``roots_present`` is a sorted list of the conventional CIS root labels that
    exist. ``copies`` maps a relative label to the ``tokens.css``-like path.
    """

    roots_present = []
    copies = {}

    for rel in GUILD_CIS_ROOTS:
        directory = guild_root / rel
        if directory.is_dir():
            roots_present.append(rel)
            candidate = directory / TOKENS_FILE
            if candidate.is_file():
                copies[rel + "/" + TOKENS_FILE] = candidate

    for dirpath, dirnames, filenames in os.walk(guild_root):
        dirnames[:] = sorted(name for name in dirnames if name not in IGNORED_DIRS)
        current = Path(dirpath)
        try:
            depth = len(current.relative_to(guild_root).parts)
        except ValueError:
            depth = MAX_WALK_DEPTH + 1
        if depth >= MAX_WALK_DEPTH:
            dirnames[:] = []
        for name in sorted(filenames):
            if name in TOKENS_COPY_NAMES:
                path = current / name
                copies.setdefault(_relative_label(path, guild_root), path)

    return sorted(roots_present), dict(sorted(copies.items()))


def compare_guild_tokens(canonical, declarations):
    """Compare one guild token file with the canonical map.

    Returns ``(problems, private_keys)``. Extra keys outside the ``--cis-``
    namespace are treated as allowed private tokens, per the drift policy in
    ``docs/cis/README.md``.
    """

    problems = []
    guild, duplicates = token_map(declarations)
    for key in duplicates:
        problems.append(f"duplicate token key {key!r}")
    for key in sorted(guild):
        if guild[key] == "":
            problems.append(f"token key {key!r} has an empty value")

    for key in sorted(set(canonical) - set(guild)):
        problems.append(f"missing canonical token key {key!r}")
    for key in sorted(set(guild) - set(canonical)):
        if key.startswith("--cis-"):
            problems.append(f"non-canonical CIS token key {key!r}")
    for key in sorted(set(guild) & set(canonical)):
        if guild[key] != canonical[key]:
            problems.append(
                f"token value drift for {key!r}: canonical {canonical[key]!r}, "
                f"guild {guild[key]!r}"
            )

    private_keys = sorted(key for key in guild if not key.startswith("--cis-"))
    return problems, private_keys


def inspect_guild_manifest_claims(guild_root, roots_present):
    """Return problems for a guild manifest that claims a mother source.

    A guild-generated manifest is allowed only while it points at the forum as
    the source of truth. A manifest that claims its own source, or that sits
    without a token file, is a duplicate mother source.
    """

    problems = []
    for rel in roots_present:
        directory = guild_root / rel
        manifest_path = directory / MANIFEST_FILE
        tokens_path = directory / TOKENS_FILE
        label = rel

        if not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(read_text(manifest_path))
        except (OSError, json.JSONDecodeError) as exc:
            problems.append(f"{label}/manifest.json: not valid JSON ({exc})")
            continue
        if isinstance(manifest, dict):
            declared = manifest.get("source_of_truth")
            if declared != CANONICAL_SOURCE_PRODUCT:
                problems.append(
                    f"{label}/manifest.json: declares source_of_truth "
                    f"{declared!r}; the guild is a consumer, not a CIS mother "
                    "source"
                )
            if manifest.get("network_used") is not False:
                problems.append(
                    f"{label}/manifest.json: network_used must be false"
                )
        if not tokens_path.is_file():
            problems.append(f"{label}: manifest.json present without tokens.css")
    return problems


def scan_guild(guild_root: Path, canonical):
    """Return a deterministic report dict for the guild side."""

    report = {
        "guild_root": str(guild_root),
        "guild_root_available": guild_root.is_dir(),
        "roots_present": [],
        "copies": 0,
        "synced_copies": 0,
        "private_token_copies": 0,
        "duplicate_mother_source": 0,
        "problems": [],
    }
    if not guild_root.is_dir():
        return report

    roots_present, copies = discover_guild(guild_root)
    report["roots_present"] = roots_present
    report["copies"] = len(copies)
    report["problems"].extend(
        inspect_guild_manifest_claims(guild_root, roots_present)
    )

    # Every discovered token file is compared with the canonical map, whether
    # it sits at a conventional CIS root or anywhere else in the tree.
    for label, path in copies.items():
        declarations = parse_tokens(read_text(path))
        token_problems, private = compare_guild_tokens(canonical, declarations)
        if private:
            report["private_token_copies"] += 1
        if token_problems:
            for problem in token_problems:
                report["problems"].append(f"{label}: {problem}")
        else:
            report["synced_copies"] += 1

    report["problems"] = dedupe(report["problems"])
    report["duplicate_mother_source"] = len(report["problems"])
    return report


# --------------------------------------------------------------------------- #
# Self audit
# --------------------------------------------------------------------------- #


def self_audit_problems(source: str) -> list:
    """Confirm this checker imports no network, clock, or random module."""

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"checker self-audit: cannot parse own source ({exc})"]

    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    return [
        f"checker self-audit: forbidden module {name!r} imported"
        for name in sorted(imported & FORBIDDEN_MODULES)
    ]


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def build_report(root: Path, guild_root: Path):
    canonical_problems, canonical_tokens, key_source = validate_canonical(root)
    guild_report = scan_guild(guild_root, canonical_tokens)
    problems = dedupe(canonical_problems + guild_report["problems"])
    return {
        "root": str(root),
        "root_available": root.is_dir(),
        "canonical_problems": dedupe(canonical_problems),
        "canonical_keys": len(canonical_tokens),
        "key_source": key_source,
        "guild": guild_report,
        "problems": problems,
    }


def render_ok(report) -> str:
    guild = report["guild"]
    roots = ", ".join(guild["roots_present"]) or "none detected"
    copy_note = (
        f"{guild['copies']} token file(s), "
        f"{guild['synced_copies']} synced, "
        f"{guild['private_token_copies']} with private tokens"
    )
    lines = [
        "cis drift: OK",
        f"  forum root: {report['root']}",
        f"  canonical keys source: {report['key_source']}",
        f"  canonical token keys: {report['canonical_keys']}",
        f"  manifest: validated against tokens.css",
        f"  guild root: {guild['guild_root']}",
        f"  guild root available: {str(guild['guild_root_available']).lower()}",
        f"  guild cis roots present: {roots}",
        f"  guild token copies: {copy_note}",
        "  duplicate cis mother source: none",
        "  result: no duplicate source found",
    ]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Check CIS drift between the forum source and the guild."
    )
    parser.add_argument(
        "--root",
        default=None,
        help="forum repository root (default: the parent of this script)",
    )
    parser.add_argument(
        "--guild-root",
        default=None,
        help=(
            "Adventurers Guild checkout (default: $GUILD_REPO or "
            f"{DEFAULT_GUILD_ROOT})"
        ),
    )
    args = parser.parse_args(argv)

    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parents[1]
    if args.guild_root:
        guild_root = Path(args.guild_root).expanduser()
    else:
        guild_root = Path(os.environ.get(GUILD_ROOT_ENV, DEFAULT_GUILD_ROOT)).expanduser()

    report = build_report(root, guild_root)

    self_problems = self_audit_problems(read_text(Path(__file__)))
    problems = dedupe(report["problems"] + self_problems)

    if problems:
        print("cis drift: INVALID", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        print(f"  problems: {len(problems)}", file=sys.stderr)
        return 1

    sys.stdout.write(render_ok(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
