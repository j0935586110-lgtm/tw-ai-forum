#!/usr/bin/env python3
"""Deterministic brand inventory for the forum x Adventurers Guild plan (M0.1).

This script reads **local source files only** (no network) and emits two
auditable artefacts:

    docs/inventory/brand-inventory.json
    docs/inventory/brand-inventory.md

Run it from the forum repo root::

    python3 scripts/check_brand_inventory.py

It exits 0 only when the generated inventory passes its own validation:
required top-level keys, exactly two products, each product carrying the
required fields, every array sorted, no duplicated entries/URLs, and no
obvious secret-like strings in the output.

Facts are tagged with one of three sources:

* ``local_static``    - parsed from a local file in one of the two checkouts
* ``known_live_url``  - one of the two live URLs handed to this milestone
* ``planning_decision`` - a decision recorded by this inventory (roles / CIS),
  explicitly *not* a claim about how the live sites currently behave.

Only the Python standard library is used.  Nothing here deploys, pushes or
calls an authenticated API.
"""

from __future__ import annotations

import argparse
import html as html_mod
import json
import os
import re
import sys
from pathlib import Path

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

SCHEMA = "brand-inventory/v1"
GENERATED_BY = "scripts/check_brand_inventory.py"

SOURCE_LOCAL = "local_static"
SOURCE_LIVE = "known_live_url"
SOURCE_PLANNING = "planning_decision"
SOURCE_KINDS = (SOURCE_LOCAL, SOURCE_LIVE, SOURCE_PLANNING)

DEFAULT_GUILD_ROOT = "/home/j/projects/adventurers-guild"
GUILD_ROOT_ENV = "GUILD_REPO"

EXCLUDE_DIR_PARTS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".venv",
        "venv",
        "dist",
        "build",
    }
)

EXCLUDE_REL_PREFIXES = ("docs/inventory/",)

CANONICAL_CIS = {
    "canonical_source_product": "forum",
    "live_conformance_claimed": False,
    "source": SOURCE_PLANNING,
    "statement": (
        "The forum (台灣 AI 實戰論壇 / Taiwan AI Forum, https://forum.928174.xyz/) "
        "is the canonical CIS source for both surfaces. This is a planning/inventory "
        "decision recorded for M0.1; it does not claim that the two live sites already "
        "share a CIS."
    ),
}

PRODUCT_SPECS = {
    "forum": {
        "display_name": "台灣 AI 實戰論壇 / Taiwan AI Forum",
        "role": (
            "Canonical CIS source and the human+agent community forum for the shared "
            "brand (planning decision; the live sites do not yet share a CIS)."
        ),
        "known_live_url": "https://forum.928174.xyz/",
        "primary_reference": "docs/index.html",
        "scan_include": [
            "docs/**/*.html",
            "docs/**/*.css",
            "docs/**/*.md",
            "README.md",
            "SKILL.md",
            "llms.txt",
            "bot-policy.json",
            "scripts/nodebb-branding.py",
        ],
        "route_roots": [],
    },
    "guild": {
        "display_name": "Adventurers Guild / 冒險者公會",
        "role": (
            "Product surface (work network) that will consume the forum's canonical CIS "
            "(planning decision)."
        ),
        "known_live_url": "https://adventurers-guild-tan.vercel.app/",
        "primary_reference": "index.html",
        "scan_include": [
            "index.html",
            "src/**/*.tsx",
            "src/**/*.ts",
            "src/**/*.css",
            "public/**/*.html",
            "public/**/*.css",
            "public/**/*.js",
            "preview/**/*.html",
            "preview/**/*.css",
        ],
        "route_roots": ["index.html", "public/**/*.html"],
    },
}

SCAN_POLICY = {
    "exclude_dir_parts": sorted(EXCLUDE_DIR_PARTS),
    "exclude_globs": [
        "docs/inventory/**",
        "**/dist/**",
        "**/node_modules/**",
        "**/__pycache__/**",
        "*.min.js",
        "package-lock.json",
    ],
    "forum_include_globs": sorted(PRODUCT_SPECS["forum"]["scan_include"]),
    "guild_include_globs": sorted(PRODUCT_SPECS["guild"]["scan_include"]),
    "note": (
        "Build output (dist/), dependency trees (node_modules/) and lockfiles are "
        "excluded from static token scanning; the deployed surface is represented "
        "by the known_live_url facts instead."
    ),
}

REQUIRED_TOP_LEVEL_KEYS = (
    "schema",
    "generated_by",
    "deterministic",
    "network_used",
    "source_kinds",
    "inputs",
    "scan_policy",
    "canonical_cis",
    "products",
)

REQUIRED_PRODUCT_FIELDS = (
    "id",
    "display_name",
    "name",
    "name_reference",
    "name_candidates",
    "role",
    "local_source_roots",
    "local_sources",
    "live_urls",
    "html_entry_files",
    "routes",
    "colors",
    "fonts",
    "brand_markers",
    "ctas",
    "external_links",
)

REQUIRED_BRAND_MARKER_FIELDS = ("wordmark", "titles", "og_image", "favicon", "assets")

# --------------------------------------------------------------------------- #
# Generic helpers
# --------------------------------------------------------------------------- #


def canonical_json(value: object) -> str:
    """Stable string form of a value, used both for sorting and diffing."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


_SORT_PREFERENCE = ("id", "url", "value", "path", "label", "name", "root", "kind")


def sort_key(item: object) -> tuple:
    """Deterministic ordering key that works for every array in the inventory."""

    if isinstance(item, dict):
        for index, field in enumerate(_SORT_PREFERENCE):
            candidate = item.get(field)
            if isinstance(candidate, str):
                return (0, index, candidate, canonical_json(item))
        return (1, len(_SORT_PREFERENCE), "", canonical_json(item))
    if isinstance(item, str):
        return (2, 0, item, item)
    return (3, 0, canonical_json(item), canonical_json(item))


def sort_deep(value: object) -> object:
    """Recursively sort every list in ``value`` so output is reproducible."""

    if isinstance(value, dict):
        return {key: sort_deep(item) for key, item in value.items()}
    if isinstance(value, list):
        return sorted((sort_deep(item) for item in value), key=sort_key)
    return value


def clean_text(value: str) -> str:
    """Strip tags/entities and collapse whitespace in a label or title."""

    value = re.sub(r"<[^>]+>", " ", value)
    value = html_mod.unescape(value)
    value = value.replace("\u00a0", " ")
    return re.sub(r"\s+", " ", value).strip()


def rel_posix(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _dedupe(items):
    """Return a sorted list with exact-duplicate entries removed."""

    seen = set()
    unique = []
    for item in items:
        marker = canonical_json(item)
        if marker not in seen:
            seen.add(marker)
            unique.append(item)
    return sorted(unique, key=sort_key)


# --------------------------------------------------------------------------- #
# File discovery
# --------------------------------------------------------------------------- #


def expand_scan_files(root: Path, patterns) -> list:
    """Expand include globs, dropping excluded directories and duplicates."""

    files = set()
    for pattern in patterns:
        for path in root.glob(pattern):
            if not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            rel_parts = path.relative_to(root).parts
            if any(part in EXCLUDE_DIR_PARTS for part in rel_parts):
                continue
            if any(rel == prefix or rel.startswith(prefix) for prefix in EXCLUDE_REL_PREFIXES):
                continue
            files.add(path)
    return sorted(files)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


BRAND_ASSET_NAME_RE = re.compile(
    r"(?:^|[-_.])(logo|wordmark|favicon|brand|og[-_]?image|icon)(?:[-_.]|$)",
    re.IGNORECASE,
)
ASSET_EXTENSIONS = frozenset(
    {".svg", ".png", ".jpg", ".jpeg", ".webp", ".ico", ".gif", ".avif", ".bmp"}
)


def find_brand_assets(root: Path) -> list:
    """Locate brand-related image assets (logo/favicon/og image...)."""

    if not root.is_dir():
        return []
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in EXCLUDE_DIR_PARTS]
        for filename in filenames:
            path = Path(filename)
            if path.suffix.lower() not in ASSET_EXTENSIONS:
                continue
            relative = rel_posix(Path(dirpath) / filename, root)
            if any(
                relative == prefix or relative.startswith(prefix)
                for prefix in EXCLUDE_REL_PREFIXES
            ):
                continue
            if BRAND_ASSET_NAME_RE.search(path.stem):
                found.append(relative)
    return sorted(set(found))


# --------------------------------------------------------------------------- #
# Token parsers
# --------------------------------------------------------------------------- #

HEX_COLOR_RE = re.compile(r"(?<![&%\w])#([0-9a-fA-F]{3,8})\b")
FUNC_COLOR_RE = re.compile(r"\b(rgba?|hsla?)\(([^()]*)\)", re.IGNORECASE)

NAMED_COLORS = frozenset(
    {
        "amber",
        "aqua",
        "azure",
        "beige",
        "black",
        "blue",
        "brown",
        "coral",
        "crimson",
        "cyan",
        "emerald",
        "fuchsia",
        "gold",
        "gray",
        "green",
        "grey",
        "indigo",
        "ivory",
        "khaki",
        "lavender",
        "lime",
        "magenta",
        "maroon",
        "navy",
        "neutral",
        "olive",
        "orange",
        "orchid",
        "pink",
        "plum",
        "purple",
        "red",
        "rose",
        "salmon",
        "silver",
        "sky",
        "slate",
        "stone",
        "tan",
        "teal",
        "tomato",
        "transparent",
        "turquoise",
        "violet",
        "white",
        "yellow",
        "zinc",
    }
)

_NAMED_ALT = "|".join(sorted(NAMED_COLORS))
# A named colour only counts when it is a CSS variable, a colour declaration
# value, or a Tailwind utility class - never when it merely appears inside a
# word such as the "guild-tan" hostname.
CSS_VAR_COLOR_RE = re.compile(r"--(" + _NAMED_ALT + r")\b", re.IGNORECASE)
COLOR_DECL_RE = re.compile(
    r"(?:color|[a-z-]*background(?:-color)?|border(?:-[a-z]+)?-color|border-color"
    r"|fill|stroke|outline-color|caret-color|accent-color|text-decoration-color"
    r"|column-rule-color)\s*:\s*([^;{}\n]+)",
    re.IGNORECASE,
)
NAMED_COLOR_RE = re.compile(r"\b(" + _NAMED_ALT + r")\b", re.IGNORECASE)
TAILWIND_COLOR_RE = re.compile(
    r"\b(?:bg|text|border|from|to|via|ring|fill|stroke|decoration|outline|shadow"
    r"|accent|caret|divide|placeholder|ring-offset)-("
    + _NAMED_ALT
    + r")(?:-(50|[1-9]00))?\b",
    re.IGNORECASE,
)

FONT_FAMILY_RE = re.compile(r"font-family\s*:\s*([^;}\n]+)", re.IGNORECASE)
FONT_SHORTHAND_RE = re.compile(r"(?<![-\w])font\s*:\s*([^;}\n]+)", re.IGNORECASE)
FONT_CUSTOM_PROP_RE = re.compile(r"(--font-[a-z0-9-]+)\s*:\s*([^;}\n]+)", re.IGNORECASE)
FONT_CLASS_RE = re.compile(r"\bfont-(sans|serif|mono)\b")

URL_RE = re.compile(r"https?://[^\s\"'<>`\)\]\}]+")
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)
MD_H1_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
SCRIPT_RE = re.compile(r"<script\b[^>]*>.*?</script\s*>", re.IGNORECASE | re.DOTALL)
COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
OPEN_TAG_NAME_RE = re.compile(r"<\s*([a-zA-Z][a-zA-Z0-9-]*)")
CLOSE_TAG_NAME_RE = re.compile(r"</\s*([a-zA-Z][a-zA-Z0-9-]*)")
FETCH_RE = re.compile(r"\bfetch\(\s*(?:`([^`]+)`|'([^']+)'|\"([^\"]+)\")")

_FONT_SIZE_RE = re.compile(
    r"^\d+(?:\.\d+)?(?:px|pt|pc|em|rem|ex|ch|vw|vh|vmin|vmax|%)?(?:/\S+)?$",
    re.IGNORECASE,
)
_FONT_STYLE_KEYWORDS = frozenset(
    {
        "normal",
        "italic",
        "oblique",
        "bold",
        "bolder",
        "lighter",
        "small-caps",
        "ultra-condensed",
        "condensed",
        "semi-condensed",
        "expanded",
        "semi-expanded",
        "ultra-expanded",
        "caption",
        "icon",
        "menu",
        "message-box",
        "small-caption",
        "status-bar",
        "inherit",
        "initial",
        "unset",
        "revert",
        "revert-layer",
    }
)


def normalize_hex(token: str) -> str:
    return "#" + token.lower()


def normalize_function_color(name: str, inside: str) -> str:
    return f"{name.lower()}({re.sub(r'\s+', '', inside).lower()})"


def extract_colors(text: str) -> list:
    """Return sorted unique color tokens (hex / rgb / hsl / named / tailwind)."""

    tokens = set()
    for match in HEX_COLOR_RE.finditer(text):
        tokens.add((normalize_hex(match.group(1)), "hex"))
    for match in FUNC_COLOR_RE.finditer(text):
        tokens.add((normalize_function_color(match.group(1), match.group(2)), "function"))
    for match in CSS_VAR_COLOR_RE.finditer(text):
        tokens.add((match.group(1).lower(), "named"))
    for match in COLOR_DECL_RE.finditer(text):
        for named in NAMED_COLOR_RE.finditer(match.group(1)):
            tokens.add((named.group(1).lower(), "named"))
    for match in TAILWIND_COLOR_RE.finditer(text):
        base = match.group(1).lower()
        tokens.add((base, "named"))
        if match.group(2):
            tokens.add((f"{base}-{match.group(2)}", "tailwind"))
    return sorted(tokens)


def split_font_families(value: str) -> list:
    """Split a CSS font-family / font shorthand value into family names."""

    families = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        kept = []
        for token in part.split():
            if _FONT_SIZE_RE.match(token):
                continue
            if token.lower() in _FONT_STYLE_KEYWORDS:
                continue
            kept.append(token)
        family = " ".join(kept).strip().strip("'\"")
        family = re.sub(r"\s+", " ", family)
        if family:
            families.append(family)
    return families


def extract_fonts(text: str) -> list:
    """Return sorted unique font tokens as ``(value, kind)`` pairs."""

    tokens = set()

    def add_families(value: str) -> None:
        for family in split_font_families(value):
            var_match = re.fullmatch(r"var\((--[a-z0-9-]+)\)", family, re.IGNORECASE)
            if var_match:
                tokens.add((var_match.group(1), "font_reference"))
            else:
                tokens.add((family, "font_family"))

    for match in FONT_FAMILY_RE.finditer(text):
        add_families(match.group(1))
    for match in FONT_SHORTHAND_RE.finditer(text):
        add_families(match.group(1))
    for match in FONT_CUSTOM_PROP_RE.finditer(text):
        tokens.add((match.group(1), "font_reference"))
        add_families(match.group(2))
    for match in FONT_CLASS_RE.finditer(text):
        tokens.add((f"font-{match.group(1).lower()}", "font_reference"))
    return sorted(tokens)


def extract_external_links(text: str) -> list:
    """Return sorted unique http(s) URLs found in ``text``."""

    urls = set()
    for match in URL_RE.finditer(text):
        url = match.group(0).rstrip(".,;:")
        if url:
            urls.add(url)
    return sorted(urls)


def _scan_open_tag(text: str, start: int):
    """Scan one opening tag starting at ``start``.

    ``>`` characters inside quotes or ``{...}`` expressions (JSX arrow
    functions such as ``onClick={() => ...}``) do not terminate the tag.
    Returns ``(name, attrs, body_start, self_closing)`` or ``None``.
    """

    name_match = OPEN_TAG_NAME_RE.match(text, start)
    if not name_match:
        return None
    index = name_match.end()
    quote = None
    brace = 0
    while index < len(text):
        char = text[index]
        if quote:
            if char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char == "{":
            brace += 1
        elif char == "}":
            if brace:
                brace -= 1
        elif char == ">" and brace == 0:
            attrs = text[name_match.end():index]
            self_closing = attrs.rstrip().endswith("/")
            return name_match.group(1).lower(), attrs, index + 1, self_closing
        index += 1
    return None


def iter_elements(text: str, tags):
    """Yield ``(tag, attrs, inner)`` for elements of the requested tags."""

    wanted = {tag.lower() for tag in tags}
    stack = []
    results = []
    index = 0
    length = len(text)
    while index < length:
        lt = text.find("<", index)
        if lt < 0:
            break
        if text.startswith("<!--", lt):
            end = text.find("-->", lt)
            index = length if end < 0 else end + 3
            continue
        close_match = CLOSE_TAG_NAME_RE.match(text, lt)
        if close_match:
            name = close_match.group(1).lower()
            gt = text.find(">", close_match.end())
            index = length if gt < 0 else gt + 1
            if stack and stack[-1][0] == name:
                open_name, attrs, body_start = stack.pop()
                results.append((open_name, attrs, text[body_start:lt]))
            continue
        open_info = _scan_open_tag(text, lt)
        if open_info is None:
            index = lt + 1
            continue
        name, attrs, body_start, self_closing = open_info
        if name in wanted and not self_closing:
            stack.append((name, attrs, body_start))
        index = body_start
    return results


def iter_open_tags(text: str, names=None):
    """Yield ``(tag, attrs)`` for every opening tag, quote/brace aware.

    Unlike a plain ``<tag[^>]*>`` regex this keeps a ``>`` that appears inside
    a quoted attribute value (for example an inline ``data:image/svg+xml``
    favicon) from ending the tag early.
    """

    wanted = None if names is None else {name.lower() for name in names}
    results = []
    index = 0
    length = len(text)
    while index < length:
        lt = text.find("<", index)
        if lt < 0:
            break
        if text.startswith("<!--", lt):
            end = text.find("-->", lt)
            index = length if end < 0 else end + 3
            continue
        if text.startswith("</", lt):
            gt = text.find(">", lt)
            index = length if gt < 0 else gt + 1
            continue
        info = _scan_open_tag(text, lt)
        if info is None:
            index = lt + 1
            continue
        name, attrs, body_start, _self_closing = info
        if wanted is None or name in wanted:
            results.append((name, attrs))
        index = body_start
    return results


def _attr(attrs: str, name: str):
    match = re.search(rf"\b{name}\s*=\s*\"([^\"]*)\"", attrs, re.IGNORECASE)
    if match:
        return match.group(1)
    match = re.search(rf"\b{name}\s*=\s*'([^']*)'", attrs, re.IGNORECASE)
    if match:
        return match.group(1)
    return None


def _handler(attrs: str):
    match = re.search(
        r"\bonclick\s*=\s*(?:\"([^\"]*)\"|'([^']*)')", attrs, re.IGNORECASE
    )
    if match:
        return (match.group(1) or match.group(2)).strip()
    match = re.search(r"\bonClick\s*=\s*\{([^{}]*)\}", attrs)
    if match:
        return match.group(1).strip()
    return None


def _label_from_inner(inner: str):
    match = re.search(r"\bt\(\s*['\"]([^'\"]+)['\"]\s*\)", inner)
    if match:
        return match.group(1), match.group(1)
    return clean_text(inner), None


def extract_ctas(text: str) -> list:
    """Return anchor/button action labels and targets.

    Returns a list of dicts with ``label``, ``label_key``, ``target``,
    ``target_kind`` (``href``/``handler``/``None``).
    """

    text = SCRIPT_RE.sub(" ", text)
    text = COMMENT_RE.sub(" ", text)
    ctas = []
    for tag, attrs, inner in iter_elements(text, {"a", "button"}):
        target = None
        target_kind = None
        if tag == "a":
            target = _attr(attrs, "href")
            if target is not None:
                target_kind = "href"
        if target is None:
            handler = _handler(attrs)
            if handler:
                target = handler
                target_kind = "handler"
        label, label_key = _label_from_inner(inner)
        if not label:
            label = clean_text(_attr(attrs, "aria-label") or "")
        if not label and not target:
            continue
        ctas.append(
            {
                "label": label,
                "label_key": label_key,
                "target": target,
                "target_kind": target_kind,
            }
        )
    return ctas


def extract_name_candidates(text: str, path: str) -> list:
    """Extract human-readable product-name candidates from one file."""

    candidates = []

    def add(value, kind):
        value = clean_text(value)
        if value:
            candidates.append({"value": value, "kind": kind, "path": path})

    for match in TITLE_RE.finditer(text):
        add(match.group(1), "html_title")
    for match in H1_RE.finditer(text):
        add(match.group(1), "html_h1")
    # A Markdown/plain-text document contributes only its first H1 (the title);
    # later "# " lines are section headings, not product titles.
    markdown_title = MD_H1_RE.search(text)
    if markdown_title:
        add(markdown_title.group(1), "markdown_h1")
    return candidates


def extract_json_name_candidates(data, path: str) -> list:
    """Recursively pull ``name``/``title`` string values out of parsed JSON."""

    keys = {"name", "name_en", "title"}
    candidates = []

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in keys and isinstance(value, str) and value.strip():
                    candidates.append(
                        {
                            "value": value.strip(),
                            "kind": f"json_{key}",
                            "path": path,
                        }
                    )
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(data)
    return candidates


WORDMARK_CLASS_RE = re.compile(r"\b(?:logo|wordmark)\b", re.IGNORECASE)


def extract_brand_markers(text: str, path: str) -> dict:
    """Extract per-file brand markers (wordmarks, og:image, favicon)."""

    markers = {"wordmarks": [], "og_image": None, "favicon": None}
    for _tag, attrs, inner in iter_elements(text, {"a", "div", "span", "h1", "p"}):
        class_value = _attr(attrs, "class") or _attr(attrs, "className") or ""
        if WORDMARK_CLASS_RE.search(class_value):
            value = clean_text(inner)
            if value:
                markers["wordmarks"].append(value)
    for _name, attrs in iter_open_tags(text, {"meta"}):
        property_value = _attr(attrs, "property") or _attr(attrs, "name") or ""
        if property_value.lower() != "og:image":
            continue
        content = _attr(attrs, "content")
        if content:
            markers["og_image"] = content.strip()
    for _name, attrs in iter_open_tags(text, {"link"}):
        rel_value = _attr(attrs, "rel") or ""
        if "icon" not in rel_value.lower():
            continue
        href = _attr(attrs, "href")
        if href:
            markers["favicon"] = href.strip()
    return markers


# --------------------------------------------------------------------------- #
# Inventory assembly
# --------------------------------------------------------------------------- #


def _collect_product(product_id: str, root: Path, root_label: str) -> dict:
    spec = PRODUCT_SPECS[product_id]
    files = expand_scan_files(root, spec["scan_include"])

    local_sources = []
    html_entry_files = []
    color_map = {}
    font_map = {}
    external_link_map = {}
    ctas = []
    name_candidates = []
    wordmark_candidates = []
    titles = []
    og_image = None
    favicon = None

    for path in files:
        rel = rel_posix(path, root)
        text = read_text(path)
        local_sources.append(rel)

        if rel.lower().endswith((".html", ".htm")):
            html_entry_files.append(rel)

        for token, kind in extract_colors(text):
            color_map.setdefault((token, kind), set()).add(rel)
        for token, kind in extract_fonts(text):
            font_map.setdefault((token, kind), set()).add(rel)
        for url in extract_external_links(text):
            external_link_map.setdefault(url, set()).add(rel)

        if rel.lower().endswith((".html", ".htm", ".tsx", ".jsx", ".js", ".ts")):
            for cta in extract_ctas(text):
                cta = dict(cta)
                cta["path"] = rel
                cta["source"] = SOURCE_LOCAL
                ctas.append(cta)

        if rel.lower().endswith((".html", ".htm")):
            # Ignore inline <script> templates so JS string templates are not
            # mistaken for static markup.
            html_text = COMMENT_RE.sub(" ", SCRIPT_RE.sub(" ", text))
            html_names = extract_name_candidates(html_text, rel)
            name_candidates.extend(html_names)
            markers = extract_brand_markers(html_text, rel)
            wordmark_candidates.extend(
                {"value": value, "path": rel, "source": SOURCE_LOCAL}
                for value in markers["wordmarks"]
            )
            titles.extend(
                {"value": candidate["value"], "path": rel, "kind": "html_title",
                 "source": SOURCE_LOCAL}
                for candidate in html_names
                if candidate["kind"] == "html_title"
            )
            if markers["og_image"]:
                og_image = og_image or {
                    "value": markers["og_image"],
                    "path": rel,
                    "source": SOURCE_LOCAL,
                }
            if markers["favicon"]:
                favicon = favicon or {
                    "value": markers["favicon"],
                    "path": rel,
                    "source": SOURCE_LOCAL,
                }
        elif rel.lower().endswith((".md", ".txt")):
            markdown_names = extract_name_candidates(text, rel)
            name_candidates.extend(markdown_names)
            titles.extend(
                {"value": candidate["value"], "path": rel, "kind": "markdown_h1",
                 "source": SOURCE_LOCAL}
                for candidate in markdown_names
                if candidate["kind"] == "markdown_h1"
            )
        elif rel.lower().endswith(".json"):
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                data = None
            if data is not None:
                name_candidates.extend(extract_json_name_candidates(data, rel))

    # ---- routes ---------------------------------------------------------- #
    routes = [
        {"path": "/", "kind": "site_root", "source": SOURCE_LIVE},
    ]
    for pattern in spec["route_roots"]:
        for path in sorted(root.glob(pattern)):
            if not path.is_file():
                continue
            rel = rel_posix(path, root)
            if rel == "index.html":
                continue
            if rel.startswith("public/"):
                routes.append(
                    {
                        "path": "/" + rel[len("public/"):],
                        "kind": "static_html",
                        "source": SOURCE_LOCAL,
                    }
                )
    for path in files:
        rel = rel_posix(path, root)
        if not rel.lower().endswith((".ts", ".tsx", ".js", ".jsx")):
            continue
        for match in FETCH_RE.finditer(read_text(path)):
            route = match.group(1) or match.group(2) or match.group(3)
            if route.startswith("/"):
                routes.append(
                    {"path": route, "kind": "api", "source": SOURCE_LOCAL}
                )

    # ---- name ------------------------------------------------------------ #
    name_candidates = _dedupe(name_candidates)
    name_value = None
    name_reference = None
    primary = spec["primary_reference"]
    for kind in ("html_h1", "html_title", "markdown_h1"):
        for candidate in name_candidates:
            if candidate["path"] == primary and candidate["kind"] == kind:
                name_value = candidate["value"]
                name_reference = f"{candidate['path']}#{kind}"
                break
        if name_value:
            break
    if name_value is None and name_candidates:
        first = name_candidates[0]
        name_value = first["value"]
        name_reference = f"{first['path']}#{first['kind']}"

    # ---- brand markers --------------------------------------------------- #
    wordmark_candidates = _dedupe(wordmark_candidates)
    if wordmark_candidates:
        wordmark = wordmark_candidates[0]
    elif name_value:
        wordmark = {"value": name_value, "path": name_reference, "source": SOURCE_LOCAL}
    else:
        wordmark = None

    assets = [
        {"path": path, "kind": "brand_named_file", "source": SOURCE_LOCAL}
        for path in find_brand_assets(root)
    ]

    brand_markers = {
        "wordmark": wordmark,
        "titles": _dedupe(titles),
        "og_image": og_image,
        "favicon": favicon,
        "assets": _dedupe(assets),
    }

    colors = [
        {"value": token, "kind": kind, "found_in": sorted(paths), "source": SOURCE_LOCAL}
        for (token, kind), paths in color_map.items()
    ]
    fonts = [
        {"value": token, "kind": kind, "found_in": sorted(paths), "source": SOURCE_LOCAL}
        for (token, kind), paths in font_map.items()
    ]
    external_links = [
        {"url": url, "paths": sorted(paths), "source": SOURCE_LOCAL}
        for url, paths in external_link_map.items()
    ]

    return {
        "id": product_id,
        "display_name": spec["display_name"],
        "name": name_value,
        "name_reference": name_reference,
        "name_candidates": name_candidates,
        "role": {"value": spec["role"], "source": SOURCE_PLANNING},
        "local_source_roots": [
            {"root": root_label, "source": SOURCE_LOCAL},
        ],
        "local_sources": sorted(set(local_sources)),
        "live_urls": [
            {
                "url": spec["known_live_url"],
                "source": SOURCE_LIVE,
                "kind": "site_root",
            }
        ],
        "html_entry_files": sorted(set(html_entry_files)),
        "routes": _dedupe(routes),
        "colors": _dedupe(colors),
        "fonts": _dedupe(fonts),
        "brand_markers": brand_markers,
        "ctas": _dedupe(ctas),
        "external_links": _dedupe(external_links),
    }


def build_inventory(
    forum_root: Path,
    guild_root: Path | None,
    guild_root_label: str,
    guild_root_source: str | None = None,
) -> dict:
    """Build the complete inventory dictionary (before deep sorting)."""

    products = [
        _collect_product("forum", Path(forum_root), "."),
        _collect_product("guild", Path(guild_root), guild_root_label)
        if guild_root is not None and Path(guild_root).is_dir()
        else _empty_product("guild", guild_root_label),
    ]

    inventory = {
        "schema": SCHEMA,
        "generated_by": GENERATED_BY,
        "deterministic": True,
        "network_used": False,
        "source_kinds": sorted(SOURCE_KINDS),
        "inputs": {
            "forum_root": ".",
            "guild_root": guild_root_label,
            "guild_root_available": bool(guild_root is not None and Path(guild_root).is_dir()),
            "guild_root_configured_via": guild_root_source
            or (
                f"env:{GUILD_ROOT_ENV}"
                if os.environ.get(GUILD_ROOT_ENV)
                else "default"
            ),
        },
        "scan_policy": SCAN_POLICY,
        "canonical_cis": CANONICAL_CIS,
        "products": products,
    }
    return sort_deep(inventory)


def _empty_product(product_id: str, root_label: str) -> dict:
    """A schema-complete product entry for a checkout that is not available."""

    spec = PRODUCT_SPECS[product_id]
    return {
        "id": product_id,
        "display_name": spec["display_name"],
        "name": None,
        "name_reference": None,
        "name_candidates": [],
        "role": {"value": spec["role"], "source": SOURCE_PLANNING},
        "local_source_roots": [{"root": root_label, "source": SOURCE_LOCAL}],
        "local_sources": [],
        "live_urls": [
            {"url": spec["known_live_url"], "source": SOURCE_LIVE, "kind": "site_root"}
        ],
        "html_entry_files": [],
        "routes": [{"path": "/", "kind": "site_root", "source": SOURCE_LIVE}],
        "colors": [],
        "fonts": [],
        "brand_markers": {
            "wordmark": None,
            "titles": [],
            "og_image": None,
            "favicon": None,
            "assets": [],
        },
        "ctas": [],
        "external_links": [],
    }


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #

SECRET_PATTERNS = (
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{6,}")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[0-9A-Za-z\-]{10,}")),
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----")),
    ("bearer_token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9_\-.]{20,}")),
    (
        "generic_secret_assignment",
        re.compile(
            r"(?i)\b(api[_-]?key|secret|password|passwd|access[_-]?token|"
            r"client[_-]?secret)\b\s*[:=]\s*[\"']?[A-Za-z0-9_\-./+]{12,}"
        ),
    ),
)


def _walk_arrays(value, path="$"):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _walk_arrays(item, f"{path}.{key}")
    elif isinstance(value, list):
        yield path, value
        for index, item in enumerate(value):
            yield from _walk_arrays(item, f"{path}[{index}]")


def _walk_dicts(value, path="$"):
    if isinstance(value, dict):
        yield path, value
        for key, item in value.items():
            yield from _walk_dicts(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk_dicts(item, f"{path}[{index}]")


def validate_inventory(inventory: dict) -> list:
    """Return a list of human-readable problems; empty means valid."""

    problems = []

    if not isinstance(inventory, dict):
        return ["inventory root is not a JSON object"]

    for key in REQUIRED_TOP_LEVEL_KEYS:
        if key not in inventory:
            problems.append(f"missing top-level key: {key}")

    if inventory.get("schema") != SCHEMA:
        problems.append(f"schema must be {SCHEMA!r}")
    if inventory.get("deterministic") is not True:
        problems.append("deterministic must be true")
    if inventory.get("network_used") is not False:
        problems.append("network_used must be false")

    source_kinds = inventory.get("source_kinds")
    if source_kinds != sorted(SOURCE_KINDS):
        problems.append(f"source_kinds must be {sorted(SOURCE_KINDS)!r}")

    canonical = inventory.get("canonical_cis")
    if not isinstance(canonical, dict):
        problems.append("canonical_cis must be an object")
    else:
        if canonical.get("canonical_source_product") != "forum":
            problems.append("canonical_cis.canonical_source_product must be 'forum'")
        if canonical.get("live_conformance_claimed") is not False:
            problems.append("canonical_cis.live_conformance_claimed must be false")
        statement = canonical.get("statement")
        if not isinstance(statement, str) or "canonical CIS source" not in statement:
            problems.append("canonical_cis.statement must name the forum as canonical CIS source")

    products = inventory.get("products")
    if not isinstance(products, list):
        problems.append("products must be a list")
    elif len(products) != 2:
        problems.append(f"products must contain exactly two entries, found {len(products)}")
    else:
        ids = [product.get("id") if isinstance(product, dict) else None for product in products]
        if ids != ["forum", "guild"]:
            problems.append(f"product ids must be ['forum', 'guild'], found {ids!r}")
        for product in products:
            if not isinstance(product, dict):
                problems.append("every product must be an object")
                continue
            product_id = product.get("id", "?")
            for field in REQUIRED_PRODUCT_FIELDS:
                if field not in product:
                    problems.append(f"{product_id}: missing required field {field!r}")
            role = product.get("role")
            if not isinstance(role, dict) or role.get("source") not in SOURCE_KINDS:
                problems.append(f"{product_id}: role must carry a valid source")
            live_urls = product.get("live_urls")
            if not isinstance(live_urls, list) or not live_urls:
                problems.append(f"{product_id}: live_urls must be a non-empty list")
            elif any(
                not isinstance(item, dict) or item.get("source") != SOURCE_LIVE
                for item in live_urls
            ):
                problems.append(f"{product_id}: every live_urls entry must be known_live_url")
            markers = product.get("brand_markers")
            if not isinstance(markers, dict):
                problems.append(f"{product_id}: brand_markers must be an object")
            else:
                for marker in REQUIRED_BRAND_MARKER_FIELDS:
                    if marker not in markers:
                        problems.append(
                            f"{product_id}: brand_markers missing {marker!r}"
                        )

    # Every array sorted and free of exact duplicates.
    for path, array in _walk_arrays(inventory):
        if array != sorted(array, key=sort_key):
            problems.append(f"{path}: array is not sorted")
        markers = [canonical_json(item) for item in array]
        if len(markers) != len(set(markers)):
            problems.append(f"{path}: array contains duplicate entries")

    # No duplicate URLs inside any URL-bearing array.
    for path, array in _walk_arrays(inventory):
        urls = [
            item["url"]
            for item in array
            if isinstance(item, dict) and isinstance(item.get("url"), str)
        ]
        if len(urls) != len(set(urls)):
            problems.append(f"{path}: array contains duplicate URLs")

    # Every declared source value is from the allowed set.
    for path, node in _walk_dicts(inventory):
        source = node.get("source")
        if source is not None and source not in SOURCE_KINDS:
            problems.append(f"{path}: invalid source {source!r}")

    # No obvious secret-like strings.
    blob = json.dumps(inventory, ensure_ascii=False, sort_keys=True)
    for label, pattern in SECRET_PATTERNS:
        if pattern.search(blob):
            problems.append(f"secret-like string detected ({label})")

    return problems


# --------------------------------------------------------------------------- #
# Markdown rendering
# --------------------------------------------------------------------------- #


def _md_list(values, empty="_(none detected)_"):
    values = list(values)
    if not values:
        return empty
    return "\n".join(f"- `{value}`" for value in values)


def _md_table(rows, header, empty="_(none detected)_"):
    rows = list(rows)
    if not rows:
        return empty
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    for row in rows:
        cells = [str(cell).replace("|", "\\|") for cell in row]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def render_markdown(inventory: dict) -> str:
    canonical = inventory["canonical_cis"]
    lines = [
        "# Brand inventory — forum × Adventurers Guild",
        "",
        f"Schema: `{inventory['schema']}`",
        f"Generated by: `{inventory['generated_by']}` (deterministic, no network).",
        "",
        "## Canonical CIS source (planning decision)",
        "",
        f"> {canonical['statement']}",
        "",
        f"- Canonical source product: **{canonical['canonical_source_product']}**",
        f"- Live conformance already claimed: "
        f"**{str(canonical['live_conformance_claimed']).lower()}**",
        f"- Source: `{canonical['source']}`",
        "",
        "## Inputs and scan policy",
        "",
        f"- Forum root: `{inventory['inputs']['forum_root']}`",
        f"- Guild root: `{inventory['inputs']['guild_root']}` "
        f"(available: {str(inventory['inputs']['guild_root_available']).lower()})",
        f"- Network used: {str(inventory['network_used']).lower()}",
        "",
        inventory["scan_policy"]["note"],
        "",
        "The forum and the guild are reported separately below. Fact sources are "
        "`local_static` (parsed from a local file), `known_live_url` (one of the two "
        "live URLs supplied for this milestone) or `planning_decision`.",
        "",
    ]

    for product in inventory["products"]:
        markers = product["brand_markers"]
        lines.extend(
            [
                f"## {product['id'].capitalize()} — {product['display_name']}",
                "",
                f"- **name**: {product['name'] or '_(not detected)_'}",
                f"- **name reference**: `{product['name_reference'] or 'null'}`",
                f"- **role**: {product['role']['value']} (`{product['role']['source']}`)",
                f"- **local source roots**: "
                + ", ".join(f"`{root['root']}`" for root in product["local_source_roots"]),
                f"- **live URLs**: "
                + ", ".join(f"`{item['url']}`" for item in product["live_urls"]),
                "",
                "### Local sources",
                "",
                _md_list(product["local_sources"]),
                "",
                "### HTML entry files",
                "",
                _md_list(product["html_entry_files"]),
                "",
                "### Routes",
                "",
                _md_table(
                    (
                        (route["path"], route["kind"], route["source"])
                        for route in product["routes"]
                    ),
                    ("route", "kind", "source"),
                ),
                "",
                "### Colors",
                "",
                _md_table(
                    (
                        (color["value"], color["kind"], ", ".join(color["found_in"]))
                        for color in product["colors"]
                    ),
                    ("token", "kind", "found in"),
                ),
                "",
                "### Fonts",
                "",
                _md_table(
                    (
                        (font["value"], font["kind"], ", ".join(font["found_in"]))
                        for font in product["fonts"]
                    ),
                    ("token", "kind", "found in"),
                ),
                "",
                "### Brand markers",
                "",
                (
                    "- wordmark: "
                    + (
                        f"`{markers['wordmark']['value']}`"
                        if markers["wordmark"]
                        else "_null_"
                    )
                ),
                f"- og:image: "
                + (f"`{markers['og_image']['value']}`" if markers["og_image"] else "_null_"),
                f"- favicon: "
                + (f"`{markers['favicon']['value']}`" if markers["favicon"] else "_null_"),
                f"- assets: "
                + (", ".join(f"`{asset['path']}`" for asset in markers["assets"]) or "_[]_"),
                "",
                "Titles:",
                "",
                _md_list(
                    f"{title['value']} ({title['path']}#{title['kind']})"
                    for title in markers["titles"]
                ),
                "",
                "### CTAs (labels and targets)",
                "",
                _md_table(
                    (
                        (cta["label"], cta["target"] or "", cta["target_kind"] or "", cta["path"])
                        for cta in product["ctas"]
                    ),
                    ("label", "target", "target kind", "path"),
                ),
                "",
                "### External links",
                "",
                _md_table(
                    (
                        (link["url"], ", ".join(link["paths"]))
                        for link in product["external_links"]
                    ),
                    ("url", "source paths"),
                ),
                "",
            ]
        )

    return "\n".join(lines).rstrip() + "\n"


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def _default_paths(forum_root: Path):
    return (
        forum_root / "docs" / "inventory" / "brand-inventory.json",
        forum_root / "docs" / "inventory" / "brand-inventory.md",
    )


def _resolve_guild_root(explicit: str | None):
    if explicit:
        return explicit, "explicit:--guild-root"
    env_value = os.environ.get(GUILD_ROOT_ENV)
    if env_value:
        return env_value, f"env:{GUILD_ROOT_ENV}"
    return DEFAULT_GUILD_ROOT, "default"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--guild-root", default=None, help="Adventurers Guild checkout")
    parser.add_argument("--json-out", default=None, help="JSON output path")
    parser.add_argument("--md-out", default=None, help="Markdown output path")
    args = parser.parse_args(argv)

    forum_root = Path(__file__).resolve().parent.parent
    guild_root_label, guild_root_source = _resolve_guild_root(args.guild_root)
    guild_root = (
        Path(guild_root_label) if Path(guild_root_label).is_dir() else None
    )

    inventory = build_inventory(
        forum_root, guild_root, guild_root_label, guild_root_source
    )
    problems = validate_inventory(inventory)

    if problems:
        print("brand inventory: INVALID", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    json_out, md_out = _default_paths(forum_root)
    if args.json_out:
        json_out = Path(args.json_out)
    if args.md_out:
        md_out = Path(args.md_out)

    json_text = json.dumps(inventory, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    md_text = render_markdown(inventory)

    json_out.parent.mkdir(parents=True, exist_ok=True)
    md_out.parent.mkdir(parents=True, exist_ok=True)
    json_out.write_text(json_text, encoding="utf-8")
    md_out.write_text(md_text, encoding="utf-8")

    print("brand inventory: OK")
    print(f"  schema: {inventory['schema']}")
    print(f"  products: {', '.join(product['id'] for product in inventory['products'])}")
    for product in inventory["products"]:
        print(
            f"  {product['id']}: {len(product['colors'])} colors, "
            f"{len(product['fonts'])} fonts, {len(product['ctas'])} ctas, "
            f"{len(product['external_links'])} external links"
        )
    print(f"  wrote {json_out}")
    print(f"  wrote {md_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
