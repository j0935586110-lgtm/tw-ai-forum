#!/usr/bin/env python3
"""Deterministic checker for the Forum x Guild shared entry homepage (M2.1).

The homepage lives under ``docs/home/`` and consumes the canonical Editorial
Network CIS in ``docs/cis/``. This script is the offline gate for that page:

* every required homepage file exists, and no unexpected file sits in
  ``docs/home/``;
* the canonical CIS token layer, ``docs/cis/tokens.css``, and the local
  ``docs/cis-preview.html`` design reference exist;
* required metadata is present: ``<title>``, description, Open Graph title and
  description, an absolute canonical link, a relative favicon, a relative
  manifest, a viewport, ``lang="zh-Hant"`` and one ``<h1>``;
* links are real: the forum and guild URLs, the local CIS preview, and every
  relative href resolves on disk; no bare ``#`` link exists except the skip link;
* canonical CIS token usage: ``styles.css`` is layout only, references only
  ``--cis-`` keys declared in ``docs/cis/tokens.css``, defines no token of its
  own, and contains no raw colour literal; the manifest and favicon only reuse
  canonical token values;
* no placeholder text, no unsupported metric, testimonial, revenue, legal or
  hype claim, and no ``undefined`` / ``null`` / ``[object Object]`` output;
* no remote asset: no external stylesheet, font, script, image, ``@import`` or
  ``url(http...)``;
* basic HTML structure: doctype, ``html`` / ``head`` / ``body`` landmarks,
  balanced tags, and fragment targets that exist.

It reads **local files only**. It never opens a socket, never uses a timestamp,
and never mutates the repository. Run it from the repository root::

    python3 scripts/check_homepage.py
    python3 scripts/check_homepage.py --root /path/to/repo

It exits ``0`` only when every check passes. Failures print one human-readable
problem per line to standard error. Only the Python standard library is used.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

HOME_DIR_REL = "docs/home"
CIS_DIR_REL = "docs/cis"
TOKENS_REL = "docs/cis/tokens.css"
PREVIEW_REL = "docs/cis-preview.html"

INDEX_FILE = "index.html"
STYLES_FILE = "styles.css"
MANIFEST_FILE = "manifest.webmanifest"
FAVICON_FILE = "favicon.svg"

ALLOWED_HOME_FILES = (INDEX_FILE, STYLES_FILE, MANIFEST_FILE, FAVICON_FILE)

LANGUAGE = "zh-Hant"
CANONICAL_URL = "https://j0935586110-lgtm.github.io/tw-ai-forum/home/"

FORUM_URL = "https://forum.928174.xyz/"
GUILD_URL = "https://adventurers-guild-tan.vercel.app/"

TOKENS_LINK = "../cis/tokens.css"
STYLES_LINK = "styles.css"
PREVIEW_LINK = "../cis-preview.html"
FAVICON_LINK = "favicon.svg"
MANIFEST_LINK = "manifest.webmanifest"

REQUIRED_OG = ("og:title", "og:description")
REQUIRED_PRODUCTS = ("forum", "guild")

# Tokens the layout must actually consume. Not the full canonical set, only the
# ones this page needs, so a future edit cannot quietly drop the CIS layer.
REQUIRED_TOKEN_REFS = (
    "--cis-color-paper",
    "--cis-color-paper-raised",
    "--cis-color-paper-sunken",
    "--cis-color-ink",
    "--cis-color-ink-muted",
    "--cis-color-network",
    "--cis-color-network-strong",
    "--cis-color-network-contrast",
    "--cis-color-line",
    "--cis-color-gold-soft",
    "--cis-color-gold",
    "--cis-font-sans",
    "--cis-font-size-md",
    "--cis-space-md",
    "--cis-radius-md",
    "--cis-focus-ring",
    "--cis-layout-container",
)

MIN_TOKEN_REFS = 15
MIN_DESCRIPTION = 40
MAX_DESCRIPTION = 240

# Structural literal values allowed in the layout stylesheet. Custom properties
# cannot appear in media query conditions, so 48rem is documented as the CIS
# mobile breakpoint. 44px is the documented accessible touch target.
ALLOWED_LITERALS = frozenset({"44px", "48rem", "100%", "1fr"})

# Asset-bearing link relations. Anything else (canonical, author, ...) may point
# at an absolute URL and is not a remote asset load.
ASSET_LINK_RELS = frozenset(
    {
        "stylesheet",
        "icon",
        "shortcut icon",
        "apple-touch-icon",
        "mask-icon",
        "manifest",
        "preload",
        "prefetch",
        "preconnect",
        "dns-prefetch",
        "modulepreload",
    }
)

VOID_ELEMENTS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)

# Raw colour syntaxes that must never appear in the layout stylesheet.
HEX_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b")
COLOR_FN_RE = re.compile(
    r"\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch|color)\s*\(", re.IGNORECASE
)
VAR_REF_RE = re.compile(r"var\(\s*(--[A-Za-z0-9-]+)")
VAR_CALL_RE = re.compile(r"var\([^()]*\)")
DECLARATION_RE = re.compile(r"(-{0,2}[A-Za-z][A-Za-z0-9-]*)\s*:\s*([^;{}]+);")
NUMERIC_LITERAL_RE = re.compile(
    r"(?<![\w-])-?\d+(?:\.\d+)?(px|rem|em|%|vh|vw|fr|s|ms)\b"
)
URL_RE = re.compile(r"\burl\s*\(", re.IGNORECASE)
IMPORT_RE = re.compile(r"@import\b", re.IGNORECASE)
TOKEN_DECL_RE = re.compile(r"--cis-[A-Za-z0-9-]+\s*:")

EM_DASH = "\u2014"
EN_DASH = "\u2013"
DASH_LABELS = (
    (EM_DASH, "em dash (U+2014)"),
    (EN_DASH, "en dash (U+2013)"),
)

NAMED_COLORS = frozenset(
    {
        "white",
        "black",
        "red",
        "green",
        "blue",
        "yellow",
        "orange",
        "purple",
        "violet",
        "gray",
        "grey",
        "silver",
        "maroon",
        "olive",
        "lime",
        "aqua",
        "teal",
        "navy",
        "fuchsia",
        "magenta",
        "pink",
        "brown",
        "gold",
        "beige",
        "ivory",
        "khaki",
        "salmon",
        "tomato",
        "crimson",
        "indigo",
        "turquoise",
        "tan",
        "coral",
    }
)

# Placeholder markers. Applied to the whole file, case-insensitively.
PLACEHOLDER_PATTERNS = (
    r"\blorem\s+ipsum\b",
    r"\btodo\b",
    r"\bfixme\b",
    r"\btbd\b",
    r"\bxxx\b",
    r"\bexample\.(?:com|org|net)\b",
    r"\bplaceholder\b",
    r"\byour[-_]",
    r"\bundefined\b",
    r"\bnull\b",
    r"\[object\s+object\]",
    r"coming\s+soon",
    r"敬請期待",
    r"待補",
    r"範例文字",
)

# Unsupported claims. Applied to the rendered text of index.html only.
CLAIM_PATTERNS = (
    r"收益",
    r"營收",
    r"月入",
    r"年收",
    r"被動收入",
    r"穩賺",
    r"保證",
    r"掛保證",
    r"guarantee",
    r"warranty",
    r"revenue",
    r"income",
    r"見證",
    r"好評",
    r"客戶推薦",
    r"testimonial",
    r"五星",
    r"最強",
    r"最好",
    r"第一名",
    r"世界級",
    r"革命性",
    r"顛覆",
    r"領先",
    r"best[- ]in[- ]class",
    r"world[- ]class",
    r"revolutionary",
    r"game[- ]chang",
    r"\d+(?:\.\d+)?\s*(?:%|％)",
    r"\d[\d,]*\s*(?:位|名)\s*(?:用戶|使用者|會員|客戶)",
    r"\d[\d,]*\s*(?:篇|則)\s*(?:貼文|文章|討論)",
    r"\d[\d,]*\s*(?:個|項)\s*任務",
    r"活躍用戶",
)

# Modules this script must never import, so it can never touch the network.
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


def normalize_hex(value: str) -> str:
    digits = value.lstrip("#").lower()
    if len(digits) == 3:
        digits = "".join(char * 2 for char in digits)
    return "#" + digits


def parse_token_values(text: str) -> dict:
    """Return ``{token_key: value}`` for the canonical token layer."""

    stripped = strip_css_comments(text)
    values = {}
    for match in DECLARATION_RE.finditer(stripped):
        key, value = match.group(1), match.group(2).strip()
        if key.startswith("--cis-"):
            values[key] = value
    return values


def syntax_balance_problems(text: str) -> list:
    """Bracket and quote balance for a CSS-like file."""

    problems = []
    stripped = strip_css_comments(text)
    closing = {")": "(", "]": "[", "}": "{"}
    stack = []
    for index, char in enumerate(stripped):
        if char in "([{":
            stack.append(char)
        elif char in ")]}":
            if not stack or stack.pop() != closing[char]:
                problems.append(f"unbalanced {char!r} at offset {index}")
                return problems
    if stack:
        problems.append(f"unclosed {stack[-1]!r}")
    return problems


# --------------------------------------------------------------------------- #
# HTML parsing
# --------------------------------------------------------------------------- #


class HomepageParser(HTMLParser):
    """Collect the metadata, links, landmarks and text of the homepage."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.structure_problems = []
        self.ids = set()
        self.metas = []
        self.links = []
        self.anchors = []
        self.h1_texts = []
        self.data_cis_products = []
        self.tag_names = set()
        self.has_doctype = False
        self.html_lang = None
        self.scripts = []
        self.images = []
        self._anchor = None
        self._title_depth = None
        self._title_parts = []
        self._h1_depth = None
        self._h1_parts = []
        self._text_parts = []

    # -- events ----------------------------------------------------------- #

    def handle_decl(self, decl: str) -> None:
        if decl.strip().lower().startswith("doctype"):
            self.has_doctype = True

    def handle_starttag(self, tag, attrs) -> None:
        tag = tag.lower()
        data = {name.lower(): (value if value is not None else "") for name, value in attrs}
        self.tag_names.add(tag)

        if tag == "html":
            self.html_lang = data.get("lang")
        identifier = data.get("id")
        if identifier:
            self.ids.add(identifier)
        if "data-cis-product" in data:
            self.data_cis_products.append(data["data-cis-product"])
        if tag == "meta":
            self.metas.append(data)
        elif tag == "link":
            self.links.append(data)
        elif tag == "a":
            self._anchor = {
                "href": data.get("href", ""),
                "target": data.get("target", ""),
                "rel": data.get("rel", ""),
                "aria_label": data.get("aria-label", ""),
                "text": "",
            }
            self.anchors.append(self._anchor)
        elif tag == "script":
            self.scripts.append(data)
        elif tag == "img":
            self.images.append(data)

        if tag == "title":
            self._title_depth = len(self.stack)
            self._title_parts = []
        elif tag == "h1":
            self._h1_depth = len(self.stack)
            self._h1_parts = []

        if tag not in VOID_ELEMENTS:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs) -> None:
        super().handle_startendtag(tag, attrs)

    def handle_endtag(self, tag) -> None:
        tag = tag.lower()
        if tag == "title" and self._title_depth is not None:
            self.title_text = "".join(self._title_parts).strip()
            self._title_depth = None
        elif tag == "h1" and self._h1_depth is not None:
            self.h1_texts.append("".join(self._h1_parts).strip())
            self._h1_depth = None
        elif tag == "a" and self._anchor is not None:
            self._anchor = None

        if tag in VOID_ELEMENTS:
            return
        if not self.stack:
            self.structure_problems.append(f"unexpected closing </{tag}>")
            return
        if self.stack[-1] != tag:
            self.structure_problems.append(
                f"closing </{tag}> does not match <{self.stack[-1]}>"
            )
            if tag in self.stack:
                while self.stack and self.stack.pop() != tag:
                    pass
            return
        self.stack.pop()

    def handle_data(self, data: str) -> None:
        self._text_parts.append(data)
        if self._anchor is not None:
            self._anchor["text"] += data
        if getattr(self, "_title_depth", None) is not None:
            self._title_parts.append(data)
        if getattr(self, "_h1_depth", None) is not None:
            self._h1_parts.append(data)

    # -- derived ---------------------------------------------------------- #

    @property
    def title(self) -> str:
        return getattr(self, "title_text", "")

    @property
    def text(self) -> str:
        return re.sub(r"\s+", " ", "".join(self._text_parts)).strip()

    def meta_map(self) -> dict:
        out = {}
        for meta in self.metas:
            key = meta.get("name") or meta.get("property")
            if key:
                out[key.lower()] = meta.get("content", "")
        return out

    def charset(self):
        for meta in self.metas:
            if "charset" in meta:
                return meta["charset"]
        return None

    def links_by_rel(self) -> dict:
        out = {}
        for link in self.links:
            rel = " ".join(sorted(link.get("rel", "").lower().split()))
            if rel:
                out.setdefault(rel, []).append(link.get("href", ""))
        return out


def parse_homepage(text: str) -> HomepageParser:
    parser = HomepageParser()
    parser.feed(text)
    parser.close()
    return parser


# --------------------------------------------------------------------------- #
# Per-area checks
# --------------------------------------------------------------------------- #


def check_files(root: Path) -> list:
    problems = []
    home = root / HOME_DIR_REL
    if not home.is_dir():
        return [f"missing directory: {HOME_DIR_REL}"]

    for name in ALLOWED_HOME_FILES:
        if not (home / name).is_file():
            problems.append(f"missing required file: {HOME_DIR_REL}/{name}")

    for path in sorted(home.iterdir()):
        if path.is_file() and path.name not in ALLOWED_HOME_FILES:
            problems.append(
                f"unexpected file in {HOME_DIR_REL}/: {path.name} "
                "(allowed files are the four home assets)"
            )

    if not (root / TOKENS_REL).is_file():
        problems.append(f"missing canonical CIS token layer: {TOKENS_REL}")
    if not (root / PREVIEW_REL).is_file():
        problems.append(f"missing local design reference: {PREVIEW_REL}")
    return problems


def check_basic_structure(parsed: HomepageParser, problems: list) -> None:
    if not parsed.has_doctype:
        problems.append("index.html: missing <!DOCTYPE html>")
    if parsed.html_lang != LANGUAGE:
        problems.append(f"index.html: <html lang> must be {LANGUAGE!r}")
    if not parsed.charset():
        problems.append("index.html: missing <meta charset>")
    for tag in ("html", "head", "body", "header", "main", "footer", "nav"):
        if tag not in parsed.tag_names:
            problems.append(f"index.html: missing <{tag}> landmark element")
    if "main" not in parsed.ids:
        problems.append("index.html: <main> needs id='main' for the skip link")
    if parsed.stack:
        problems.append(
            "index.html: unclosed tag(s): " + ", ".join(sorted(parsed.stack))
        )
    problems.extend(f"index.html: {p}" for p in parsed.structure_problems)

    if len(parsed.h1_texts) != 1:
        problems.append(
            f"index.html: expected exactly one <h1>, found {len(parsed.h1_texts)}"
        )
    elif not parsed.h1_texts[0]:
        problems.append("index.html: <h1> must not be empty")

    if len(parsed.anchors) < 3:
        problems.append("index.html: expected at least three links")
    elif parsed.anchors[0]["href"] != "#main":
        problems.append("index.html: the first link must be the skip link to '#main'")


def check_metadata(parsed: HomepageParser, problems: list) -> None:
    meta = parsed.meta_map()

    if not parsed.title:
        problems.append("index.html: missing non-empty <title>")
    description = meta.get("description", "")
    if not description:
        problems.append("index.html: missing meta description")
    elif not (MIN_DESCRIPTION <= len(description) <= MAX_DESCRIPTION):
        problems.append(
            "index.html: meta description length must be "
            f"{MIN_DESCRIPTION}..{MAX_DESCRIPTION} characters"
        )

    for key in REQUIRED_OG:
        if not meta.get(key):
            problems.append(f"index.html: missing Open Graph {key!r}")
    if parsed.title and meta.get("og:title") and meta["og:title"] != parsed.title:
        problems.append("index.html: og:title must match <title>")
    if description and meta.get("og:description") and meta["og:description"] != description:
        problems.append("index.html: og:description must match meta description")

    viewport = meta.get("viewport", "")
    if "width=device-width" not in viewport:
        problems.append("index.html: viewport must set width=device-width")

    links = parsed.links_by_rel()
    canonical = links.get("canonical", [])
    if canonical != [CANONICAL_URL]:
        problems.append(
            f"index.html: rel=canonical must be exactly {CANONICAL_URL!r}"
        )
    if FAVICON_LINK not in links.get("icon", []):
        problems.append(f"index.html: favicon must be the relative {FAVICON_LINK!r}")
    if MANIFEST_LINK not in links.get("manifest", []):
        problems.append(
            f"index.html: manifest must be the relative {MANIFEST_LINK!r}"
        )

    stylesheets = links.get("stylesheet", [])
    if TOKENS_LINK not in stylesheets:
        problems.append(
            f"index.html: must import the canonical CIS token layer {TOKENS_LINK!r}"
        )
    if STYLES_LINK not in stylesheets:
        problems.append(f"index.html: must link the local {STYLES_LINK!r}")
    if TOKENS_LINK in stylesheets and STYLES_LINK in stylesheets:
        if stylesheets.index(TOKENS_LINK) > stylesheets.index(STYLES_LINK):
            problems.append(
                "index.html: the canonical token layer must load before styles.css"
            )

    products = set(parsed.data_cis_products)
    if products != set(REQUIRED_PRODUCTS):
        problems.append(
            "index.html: expected data-cis-product values "
            f"{sorted(REQUIRED_PRODUCTS)}, found {sorted(products)}"
        )


def _resolve(root: Path, href: str) -> Path:
    return (root / HOME_DIR_REL / href).resolve()


def check_links(root: Path, parsed: HomepageParser, problems: list) -> None:
    anchor_hrefs = [anchor["href"] for anchor in parsed.anchors]

    for required in (FORUM_URL, GUILD_URL):
        if required not in anchor_hrefs:
            problems.append(f"index.html: missing required link target {required!r}")

    preview_anchors = [a for a in parsed.anchors if a["href"] == PREVIEW_LINK]
    if not preview_anchors:
        problems.append(
            f"index.html: missing design-reference link {PREVIEW_LINK!r}"
        )
    else:
        label = " ".join(
            (a["text"] + " " + a["aria_label"]) for a in preview_anchors
        )
        if "設計參考" not in label:
            problems.append(
                "index.html: the CIS preview link must be labelled as 設計參考"
            )

    for anchor in parsed.anchors:
        href = anchor["href"]
        if not href:
            problems.append("index.html: found an anchor with an empty href")
            continue
        if href == "#":
            problems.append("index.html: bare '#' link found (only the skip link may use '#main')")
            continue
        if href.startswith("#"):
            if href[1:] not in parsed.ids:
                problems.append(
                    f"index.html: fragment link {href!r} has no matching element id"
                )
            continue
        if href.startswith(("http://", "https://")):
            if "noopener" not in anchor["rel"].lower():
                problems.append(
                    f"index.html: external link {href!r} needs rel='noopener'"
                )
            label = (anchor["text"] + " " + anchor["aria_label"]).lower()
            if "外部連結" not in label:
                problems.append(
                    f"index.html: external link {href!r} needs an explicit 外部連結 label"
                )
            continue
        if href.startswith(("mailto:", "tel:", "data:", "javascript:")):
            problems.append(f"index.html: unsupported link scheme in {href!r}")
            continue
        if href.startswith("/"):
            problems.append(f"index.html: local link {href!r} must be relative")
            continue
        if not _resolve(root, href).is_file():
            problems.append(f"index.html: local link {href!r} does not resolve to a file")


def check_no_remote_assets(parsed: HomepageParser, css_text: str, manifest_text: str, problems: list) -> None:
    if parsed.scripts:
        problems.append("index.html: this static router must not load or inline <script>")
    if parsed.images:
        problems.append("index.html: <img> is not allowed; use local or inline SVG only")

    for link in parsed.links:
        rel = link.get("rel", "").lower()
        href = link.get("href", "")
        if set(rel.split()) & ASSET_LINK_RELS and href.startswith(("http://", "https://")):
            problems.append(f"index.html: remote asset link {href!r} is not allowed")

    stripped = strip_css_comments(css_text)
    if IMPORT_RE.search(stripped):
        problems.append("styles.css: @import is not allowed")
    if URL_RE.search(stripped):
        problems.append("styles.css: url() is not allowed")
    if "http://" in stripped or "https://" in stripped:
        problems.append("styles.css: remote URL is not allowed")

    if "http://" in manifest_text or "https://" in manifest_text:
        problems.append("manifest.webmanifest: remote URL is not allowed")


def check_manifest(manifest_text: str, tokens: dict, problems: list) -> dict:
    try:
        data = json.loads(manifest_text)
    except json.JSONDecodeError as exc:
        problems.append(f"manifest.webmanifest: not valid JSON ({exc})")
        return {}
    if not isinstance(data, dict):
        problems.append("manifest.webmanifest: root must be a JSON object")
        return {}

    for key in ("name", "short_name", "start_url", "scope", "display", "icons", "lang"):
        if key not in data:
            problems.append(f"manifest.webmanifest: missing required key {key!r}")

    if data.get("lang") != LANGUAGE:
        problems.append(f"manifest.webmanifest: lang must be {LANGUAGE!r}")
    if data.get("start_url") != "./":
        problems.append("manifest.webmanifest: start_url must be the relative './'")
    if data.get("scope") != "./":
        problems.append("manifest.webmanifest: scope must be the relative './'")
    if data.get("display") != "browser":
        problems.append(
            "manifest.webmanifest: display must be 'browser' (no app/offline claim)"
        )

    lowered = manifest_text.lower()
    for term in ("offline", "serviceworker", "service worker", "precache", "cache"):
        if term in lowered:
            problems.append(
                f"manifest.webmanifest: must not claim {term!r} without a service worker"
            )

    paper = tokens.get("--cis-color-paper", "").lower()
    network = tokens.get("--cis-color-network", "").lower()
    if data.get("background_color", "").lower() != paper:
        problems.append(
            "manifest.webmanifest: background_color must reuse --cis-color-paper"
        )
    if data.get("theme_color", "").lower() != network:
        problems.append(
            "manifest.webmanifest: theme_color must reuse --cis-color-network"
        )

    icons = data.get("icons")
    if not isinstance(icons, list) or not icons:
        problems.append("manifest.webmanifest: icons must be a non-empty list")
    else:
        for icon in icons:
            src = icon.get("src", "") if isinstance(icon, dict) else ""
            if not src:
                problems.append("manifest.webmanifest: every icon needs a src")
            elif src.startswith(("http://", "https://", "/", "data:")):
                problems.append(
                    f"manifest.webmanifest: icon src {src!r} must be a relative path"
                )
            elif ".." in Path(src).parts:
                problems.append(
                    f"manifest.webmanifest: icon src {src!r} must stay inside the app scope"
                )
    return data


def check_cis_token_usage(
    css_text: str,
    html_text: str,
    tokens: dict,
    manifest_data: dict,
    favicon_text: str,
    problems: list,
) -> int:
    stripped = strip_css_comments(css_text)
    referenced = VAR_REF_RE.findall(stripped)
    referenced_set = set(referenced)

    for key in sorted(referenced_set):
        if key not in tokens:
            problems.append(
                f"styles.css: references {key!r}, which is not declared in {TOKENS_REL}"
            )
    for bad in re.findall(r"var\(\s*(--(?!cis-)[A-Za-z0-9-]+)", stripped):
        problems.append(f"styles.css: non-canonical custom property {bad!r}")

    for match in DECLARATION_RE.finditer(stripped):
        key = match.group(1)
        if key.startswith("--"):
            problems.append(
                f"styles.css: must not declare custom property {key!r} (layout only)"
            )

    for match in HEX_RE.finditer(stripped):
        problems.append(f"styles.css: raw colour {match.group(0)!r} is not allowed")
    for match in COLOR_FN_RE.finditer(stripped):
        problems.append(
            f"styles.css: raw colour function {match.group(0)!r} is not allowed"
        )

    for match in DECLARATION_RE.finditer(stripped):
        key, value = match.group(1), match.group(2).strip()
        bare = VAR_CALL_RE.sub(" ", value).lower()
        for name in sorted(NAMED_COLORS):
            if re.search(rf"\b{name}\b", bare):
                problems.append(
                    f"styles.css: {key} uses raw colour keyword {name!r}"
                )
        if key == "font-family" and not value.startswith("var("):
            problems.append(
                f"styles.css: font-family must come from a canonical token, got {value!r}"
            )

    for match in NUMERIC_LITERAL_RE.finditer(stripped):
        if match.group(0) not in ALLOWED_LITERALS:
            problems.append(
                f"styles.css: literal value {match.group(0)!r} is not an allowed "
                "structural value"
            )

    if len(referenced_set) < MIN_TOKEN_REFS:
        problems.append(
            "styles.css: expected at least "
            f"{MIN_TOKEN_REFS} canonical token references, found {len(referenced_set)}"
        )
    for key in REQUIRED_TOKEN_REFS:
        if key not in referenced_set:
            problems.append(f"styles.css: must reference canonical token {key!r}")

    if TOKEN_DECL_RE.search(html_text):
        problems.append("index.html: must not declare or redefine a --cis- token")

    # Manifest and favicon may only reuse canonical values, never invent one.
    manifest_colors = [
        manifest_data.get("background_color", ""),
        manifest_data.get("theme_color", ""),
    ]
    canonical_values = {value.lower() for value in tokens.values()}
    for colour in manifest_colors:
        if colour and colour.lower() not in canonical_values:
            problems.append(
                f"manifest.webmanifest: colour {colour!r} is not a canonical token value"
            )
    for match in HEX_RE.finditer(favicon_text):
        if normalize_hex(match.group(0)) not in canonical_values:
            problems.append(
                f"favicon.svg: colour {match.group(0)!r} is not a canonical token value"
            )
    for pattern, label in (
        (URL_RE, "url()"),
        (re.compile(r"<image\b", re.IGNORECASE), "<image>"),
        (re.compile(r"<script\b", re.IGNORECASE), "<script>"),
        (re.compile(r"<foreignObject\b", re.IGNORECASE), "<foreignObject>"),
    ):
        if pattern.search(favicon_text):
            problems.append(f"favicon.svg: {label} is not allowed")
    favicon_probe = favicon_text.replace("http://www.w3.org/2000/svg", "").replace(
        "https://www.w3.org/2000/svg", ""
    )
    if "http://" in favicon_probe or "https://" in favicon_probe:
        problems.append("favicon.svg: remote reference is not allowed")

    return len(referenced_set)


def check_no_claims(parsed: HomepageParser, html_text: str, problems: list) -> None:
    lowered = html_text.lower()
    for pattern in PLACEHOLDER_PATTERNS:
        if re.search(pattern, lowered):
            problems.append(f"index.html: placeholder-like text matched /{pattern}/")

    visible = parsed.text.lower()
    for pattern in CLAIM_PATTERNS:
        match = re.search(pattern, visible)
        if match:
            problems.append(
                f"index.html: unsupported claim matched /{pattern}/ near {match.group(0)!r}"
            )

    for char, label in DASH_LABELS:
        if char in html_text:
            problems.append(f"index.html: forbidden {label} in canonical copy")


def self_audit_problems(source: str) -> list:
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


def check_homepage(root: Path) -> list:
    problems = check_files(root)
    if problems:
        return sorted(problems)

    home = root / HOME_DIR_REL
    index_text = read_text(home / INDEX_FILE)
    css_text = read_text(home / STYLES_FILE)
    manifest_text = read_text(home / MANIFEST_FILE)
    favicon_text = read_text(home / FAVICON_FILE)
    tokens = parse_token_values(read_text(root / TOKENS_REL))

    if not tokens:
        problems.append(f"{TOKENS_REL}: no canonical --cis- token declarations found")
        return sorted(problems)

    parsed = parse_homepage(index_text)

    check_basic_structure(parsed, problems)
    check_metadata(parsed, problems)
    check_links(root, parsed, problems)
    check_no_remote_assets(parsed, css_text, manifest_text, problems)
    manifest_data = check_manifest(manifest_text, tokens, problems)
    check_cis_token_usage(
        css_text, index_text, tokens, manifest_data, favicon_text, problems
    )
    check_no_claims(parsed, index_text, problems)

    problems.extend(syntax_balance_problems(css_text))
    problems.extend(self_audit_problems(read_text(Path(__file__))))

    return sorted(problems)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Check the Forum x Guild shared entry homepage (M2.1)."
    )
    parser.add_argument(
        "--root",
        default=None,
        help="repository root to check (default: the parent of this script's directory)",
    )
    args = parser.parse_args(argv)

    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parents[1]
    problems = check_homepage(root)

    if problems:
        print("homepage: INVALID", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    print("homepage: OK")
    print(f"  files checked: {len(ALLOWED_HOME_FILES)} (+ canonical CIS tokens + preview)")
    print("  required metadata: ok")
    print("  external link targets: 2")
    print("  remote assets: none")
    print("  unsupported claims: none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
