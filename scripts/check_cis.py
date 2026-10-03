#!/usr/bin/env python3
"""Deterministic checker for the Editorial Network CIS v0.1.

This script reads **local files only** under ``docs/cis/``. It never opens a
socket, never calls an external API, and never uses a timestamp. Run it from the
repository root::

    python3 scripts/check_cis.py
    python3 scripts/check_cis.py --root /path/to/repo
    python3 scripts/check_cis.py --write-manifest

It exits ``0`` only when all of the following hold:

* every required CIS file exists;
* ``tokens.css`` carries the ``CIS v0.1`` version comment and the ``cis/v0.1``
  schema marker;
* the token declarations are exactly the canonical v0.1 key set, with no
  missing, extra, or duplicate keys;
* the only selector in ``tokens.css`` is the ``:root`` token block, and no
  ``url()`` asset reference leaks into the token layer;
* CSS brackets, quotes, and Markdown code fences are balanced;
* no forbidden design tell is present: purple / violet hues, named purple-family
  colours, gradient fills, or the em dash and en dash in canonical copy.

Only the Python standard library is used. The optional ``manifest.json`` is a
generated index; it is validated when present and written only when
``--write-manifest`` is passed, so a plain run never mutates the repository.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

CIS_VERSION = "0.1"
SCHEMA = "cis/v0.1"
MANIFEST_SCHEMA = "cis-manifest/v0.1"
GENERATED_BY = "scripts/check_cis.py"
SOURCE_OF_TRUTH = "forum"

CIS_DIR_REL = "docs/cis"
TOKENS_FILE = "tokens.css"
MANIFEST_FILE = "manifest.json"

REQUIRED_FILES = (
    "README.md",
    "tokens.css",
    "components.md",
    "content-style.md",
)

# The canonical token keys for v0.1. ``tokens.css`` must declare exactly this
# set: missing keys, duplicate keys, and non-canonical keys are all errors.
TOKEN_GROUPS = {
    "color-base": (
        "--cis-color-paper",
        "--cis-color-paper-raised",
        "--cis-color-paper-sunken",
        "--cis-color-graphite",
        "--cis-color-graphite-raised",
        "--cis-color-ink",
        "--cis-color-ink-muted",
        "--cis-color-ink-inverse",
        "--cis-color-line",
        "--cis-color-line-strong",
    ),
    "color-network": (
        "--cis-color-network",
        "--cis-color-network-strong",
        "--cis-color-network-soft",
        "--cis-color-network-contrast",
    ),
    "color-agent": (
        "--cis-color-agent",
        "--cis-color-agent-soft",
        "--cis-color-agent-contrast",
    ),
    "color-gold": (
        "--cis-color-gold",
        "--cis-color-gold-soft",
        "--cis-color-gold-contrast",
    ),
    "color-status": (
        "--cis-color-success",
        "--cis-color-warning",
        "--cis-color-danger",
    ),
    "font": (
        "--cis-font-sans",
        "--cis-font-mono",
        "--cis-font-size-xs",
        "--cis-font-size-sm",
        "--cis-font-size-md",
        "--cis-font-size-lg",
        "--cis-font-size-xl",
        "--cis-font-size-2xl",
        "--cis-line-height-tight",
        "--cis-line-height-base",
        "--cis-line-height-relaxed",
        "--cis-font-weight-regular",
        "--cis-font-weight-medium",
        "--cis-font-weight-semibold",
        "--cis-font-weight-bold",
        "--cis-letter-spacing-tight",
        "--cis-letter-spacing-wide",
    ),
    "space": (
        "--cis-space-2xs",
        "--cis-space-xs",
        "--cis-space-sm",
        "--cis-space-md",
        "--cis-space-lg",
        "--cis-space-xl",
        "--cis-space-2xl",
        "--cis-space-3xl",
    ),
    "radius": (
        "--cis-radius-xs",
        "--cis-radius-sm",
        "--cis-radius-md",
        "--cis-radius-lg",
        "--cis-radius-pill",
    ),
    "border": (
        "--cis-border-width",
        "--cis-border-width-strong",
    ),
    "shadow": (
        "--cis-shadow-sm",
        "--cis-shadow-md",
    ),
    "focus": (
        "--cis-focus-ring-color",
        "--cis-focus-ring-width",
        "--cis-focus-ring-offset",
        "--cis-focus-ring",
    ),
    "layout": (
        "--cis-layout-container",
        "--cis-layout-header-height",
        "--cis-layout-mobile-breakpoint",
    ),
    "motion": (
        "--cis-motion-duration-fast",
        "--cis-motion-duration-base",
        "--cis-motion-ease",
    ),
}

REQUIRED_TOKEN_KEYS = tuple(
    key for group in TOKEN_GROUPS.values() for key in group
)
REQUIRED_TOKEN_KEY_SET = frozenset(REQUIRED_TOKEN_KEYS)

# Content markers the documents must keep so policy cannot silently disappear.
README_MARKERS = (
    "canonical cis",
    "source of truth",
    "adventurers guild",
    "synchronized",
    "drift",
    "ownership",
    "change policy",
    "parchment",
    "fantasy",
    "game-card",
    "purple",
)

COMPONENT_MARKERS = (
    "header",
    "footer",
    "cta",
    "card",
    "focus",
    "agent marker",
    "mobile navigation",
    "empty",
    "error",
)

CONTENT_MARKERS = (
    "plain language",
    "evidence",
    "unsupported",
    "revenue",
    "guarantee",
    "invented",
    "traditional chinese",
    "em dash",
    "en dash",
)

# Design tells that must never appear in the token layer.
FORBIDDEN_VALUE_TERMS = (
    "purple",
    "violet",
    "fuchsia",
    "magenta",
    "orchid",
    "parchment",
)

GRADIENT_RE = re.compile(
    r"\b(?:repeating-)?(?:linear|radial|conic)-gradient\s*\(", re.IGNORECASE
)
HEX_COLOR_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b")
DECLARATION_RE = re.compile(r"(--[A-Za-z0-9-]+)\s*:\s*([^;{}]+);")
SELECTOR_RE = re.compile(r"([^{}]+)\{")
URL_RE = re.compile(r"\burl\s*\(", re.IGNORECASE)
FENCE_RE = re.compile(r"^\s*```", re.MULTILINE)

EM_DASH = "\u2014"
EN_DASH = "\u2013"
DASH_LABELS = (
    (EM_DASH, "em dash (U+2014)"),
    (EN_DASH, "en dash (U+2013)"),
)

# This checker must stay offline. Modules it must never import are audited by
# parsing its own source, so a future edit cannot quietly add a network call.
NETWORK_MODULES = frozenset(
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
    }
)


# --------------------------------------------------------------------------- #
# Generic helpers
# --------------------------------------------------------------------------- #


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def strip_css_comments(text: str) -> str:
    return re.sub(r"/\*.*?\*/", " ", text, flags=re.DOTALL)


def parse_token_declarations(text: str, strip_comments: bool = True) -> list:
    """Return ``(key, value)`` pairs in source order."""

    if strip_comments:
        text = strip_css_comments(text)
    return [(match.group(1), match.group(2).strip()) for match in DECLARATION_RE.finditer(text)]


def syntax_balance_problems(text: str, strip_comments: bool = True) -> list:
    """Return bracket and quote balance problems for a CSS-like file."""

    problems = []
    if strip_comments:
        text = strip_css_comments(text)

    closing = {")": "(", "]": "[", "}": "{"}
    stack = []
    for index, char in enumerate(text):
        if char in "([{":
            stack.append(char)
        elif char in ")]}":
            if not stack or stack.pop() != closing[char]:
                problems.append(f"unbalanced {char!r} at offset {index}")
                return problems
    if stack:
        problems.append(f"unclosed {stack[-1]!r}")

    for quote in ('"', "'"):
        count = 0
        escaped = False
        for char in text:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                count += 1
        if count % 2:
            problems.append(f"unbalanced {quote} quote")
    return problems


def markdown_fence_problems(text: str) -> list:
    fences = FENCE_RE.findall(text)
    if len(fences) % 2:
        return ["unbalanced Markdown code fence"]
    return []


def _hex_to_rgb(value: str):
    digits = value.lstrip("#")
    if len(digits) == 3:
        digits = "".join(char * 2 for char in digits)
    if len(digits) not in (6, 8):
        return None
    try:
        return tuple(int(digits[index:index + 2], 16) for index in (0, 2, 4))
    except ValueError:
        return None


def rgb_to_hsl(red: int, green: int, blue: int):
    """Return ``(hue_degrees, saturation, lightness)`` with s and l in 0..1."""

    r, g, b = red / 255.0, green / 255.0, blue / 255.0
    maximum = max(r, g, b)
    minimum = min(r, g, b)
    delta = maximum - minimum
    lightness = (maximum + minimum) / 2.0
    if delta == 0:
        return 0.0, 0.0, lightness
    saturation = delta / (2.0 - maximum - minimum) if lightness > 0.5 else delta / (maximum + minimum)
    if maximum == r:
        hue = ((g - b) / delta) % 6
    elif maximum == g:
        hue = (b - r) / delta + 2
    else:
        hue = (r - g) / delta + 4
    return hue * 60.0, saturation, lightness


def is_purple_family(red: int, green: int, blue: int) -> bool:
    """True for a visible purple / violet hue, while keeping indigo allowed."""

    hue, saturation, lightness = rgb_to_hsl(red, green, blue)
    return 255.0 <= hue < 330.0 and saturation >= 0.20 and 0.06 <= lightness <= 0.96


def forbidden_design_tells(text: str, declarations: list, stripped: str) -> list:
    """Return problems for purple-family colour, gradients, and named tells."""

    problems = []

    if GRADIENT_RE.search(stripped):
        problems.append("tokens.css: gradient fills are not part of CIS v0.1")

    for key, value in declarations:
        lowered = value.lower()
        for term in FORBIDDEN_VALUE_TERMS:
            if term in lowered:
                problems.append(f"tokens.css: {key} uses forbidden colour term {term!r}")
        for hex_value in HEX_COLOR_RE.findall(value):
            rgb = _hex_to_rgb(hex_value)
            if rgb is not None and is_purple_family(*rgb):
                problems.append(
                    f"tokens.css: {key} uses purple-family colour {hex_value.lower()}"
                )
    return problems


# --------------------------------------------------------------------------- #
# Per-file checks
# --------------------------------------------------------------------------- #


def check_tokens_css(text: str) -> list:
    problems = []

    if f"CIS v{CIS_VERSION}" not in text:
        problems.append(f"tokens.css: missing version comment 'CIS v{CIS_VERSION}'")
    if SCHEMA not in text:
        problems.append(f"tokens.css: missing schema marker {SCHEMA!r}")

    stripped = strip_css_comments(text)
    declarations = parse_token_declarations(text)
    if not declarations:
        problems.append("tokens.css: no custom-property declarations found")
        return problems

    counts = {}
    for key, _value in declarations:
        counts[key] = counts.get(key, 0) + 1
    for key, count in sorted(counts.items()):
        if count > 1:
            problems.append(f"tokens.css: duplicate token key {key!r} declared {count} times")

    present = set(counts)
    for key in REQUIRED_TOKEN_KEYS:
        if key not in present:
            problems.append(f"tokens.css: missing token key {key!r}")
    for key in sorted(present):
        if key not in REQUIRED_TOKEN_KEY_SET:
            problems.append(f"tokens.css: non-canonical token key {key!r}")

    selectors = [match.group(1).strip() for match in SELECTOR_RE.finditer(stripped)]
    for selector in selectors:
        if selector != ":root":
            problems.append(
                f"tokens.css: only the :root token block is allowed, found selector {selector!r}"
            )
    if not selectors:
        problems.append("tokens.css: missing :root token block")

    if URL_RE.search(stripped):
        problems.append("tokens.css: url() references are not allowed in the token layer")

    problems.extend(syntax_balance_problems(text))
    problems.extend(forbidden_design_tells(text, declarations, stripped))
    return problems


def check_markers(path: Path, markers, label: str) -> list:
    if not path.is_file():
        return []
    lowered = read_text(path).lower()
    return [f"{label}: missing required content marker {marker!r}" for marker in markers if marker not in lowered]


def check_dashes(root: Path) -> list:
    problems = []
    directory = root / CIS_DIR_REL
    if not directory.is_dir():
        return problems
    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.suffix.lower() not in {".md", ".css", ".json"}:
            continue
        text = read_text(path)
        for char, label in DASH_LABELS:
            if char in text:
                problems.append(f"{path.name}: forbidden {label} in canonical copy")
    return problems


def build_manifest() -> dict:
    return {
        "schema": MANIFEST_SCHEMA,
        "version": CIS_VERSION,
        "generated_by": GENERATED_BY,
        "deterministic": True,
        "network_used": False,
        "source_of_truth": SOURCE_OF_TRUTH,
        "files": sorted(REQUIRED_FILES),
        "token_groups": {name: list(keys) for name, keys in TOKEN_GROUPS.items()},
        "token_keys": sorted(REQUIRED_TOKEN_KEYS),
        "checks": sorted(
            [
                "required-files",
                "schema-version",
                "required-token-keys",
                "duplicate-token-keys",
                "syntax-balance",
                "forbidden-design-tells",
            ]
        ),
    }


def check_manifest(path: Path) -> list:
    problems = []
    try:
        data = json.loads(read_text(path))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"manifest.json: not valid JSON ({exc})"]
    if not isinstance(data, dict):
        return ["manifest.json: root must be a JSON object"]

    if data.get("schema") != MANIFEST_SCHEMA:
        problems.append(f"manifest.json: schema must be {MANIFEST_SCHEMA!r}")
    if data.get("version") != CIS_VERSION:
        problems.append(f"manifest.json: version must be {CIS_VERSION!r}")
    if data.get("source_of_truth") != SOURCE_OF_TRUTH:
        problems.append(f"manifest.json: source_of_truth must be {SOURCE_OF_TRUTH!r}")
    if data.get("network_used") is not False:
        problems.append("manifest.json: network_used must be false")
    if sorted(data.get("token_keys") or []) != sorted(REQUIRED_TOKEN_KEYS):
        problems.append("manifest.json: token_keys must equal the canonical v0.1 key set")
    if sorted(data.get("files") or []) != sorted(REQUIRED_FILES):
        problems.append("manifest.json: files must equal the required CIS file set")
    return problems


def self_audit_problems(source: str) -> list:
    """Confirm this checker never imports a network-capable module."""

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
        f"checker self-audit: forbidden network module {name!r} imported"
        for name in sorted(imported & NETWORK_MODULES)
    ]


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def check_cis(root: Path, manifest_path: Path | None = None, write_manifest: bool = False):
    """Return ``(problems, manifest)``. ``manifest`` is ``None`` when invalid."""

    problems = []

    if not root.is_dir():
        return [f"root directory does not exist: {root}"], None

    cis_dir = root / CIS_DIR_REL
    for name in REQUIRED_FILES:
        if not (cis_dir / name).is_file():
            problems.append(f"missing required file: {CIS_DIR_REL}/{name}")
    if problems:
        return problems, None

    tokens_path = cis_dir / TOKENS_FILE
    problems.extend(check_tokens_css(read_text(tokens_path)))

    readme = cis_dir / "README.md"
    problems.extend(check_markers(readme, README_MARKERS, "README.md"))
    components = cis_dir / "components.md"
    problems.extend(check_markers(components, COMPONENT_MARKERS, "components.md"))
    problems.extend(markdown_fence_problems(read_text(components)))
    content = cis_dir / "content-style.md"
    problems.extend(check_markers(content, CONTENT_MARKERS, "content-style.md"))
    problems.extend(markdown_fence_problems(read_text(content)))
    problems.extend(markdown_fence_problems(read_text(readme)))
    problems.extend(check_dashes(root))

    if manifest_path is not None and manifest_path.is_file() and not write_manifest:
        problems.extend(check_manifest(manifest_path))

    problems.extend(self_audit_problems(read_text(Path(__file__))))

    if problems:
        return problems, None
    return [], build_manifest()


def render_manifest(manifest: dict) -> str:
    return json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Check the Editorial Network CIS v0.1.")
    parser.add_argument(
        "--root",
        default=None,
        help="repository root to check (default: the parent of this script's directory)",
    )
    parser.add_argument(
        "--manifest",
        default=None,
        help="manifest path (default: <root>/docs/cis/manifest.json)",
    )
    parser.add_argument(
        "--write-manifest",
        action="store_true",
        help="write the generated manifest after a successful check",
    )
    args = parser.parse_args(argv)

    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parents[1]
    manifest_path = (
        Path(args.manifest).resolve()
        if args.manifest
        else root / CIS_DIR_REL / MANIFEST_FILE
    )

    problems, manifest = check_cis(root, manifest_path, args.write_manifest)

    if problems:
        print("cis: INVALID", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    if args.write_manifest:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(render_manifest(manifest), encoding="utf-8")

    print("cis: OK")
    print(f"  version: {CIS_VERSION}")
    print(f"  required files: {len(REQUIRED_FILES)}")
    print(f"  canonical token keys: {len(REQUIRED_TOKEN_KEYS)}")
    if args.write_manifest:
        print(f"  wrote {manifest_path}")
    elif manifest_path.is_file():
        print(f"  manifest validated: {manifest_path}")
    else:
        print("  manifest: absent (optional)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
