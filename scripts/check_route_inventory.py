#!/usr/bin/env python3
"""Deterministic route / entry-point inventory for the forum x Adventurers Guild plan (M0.2).

This script reads **local static sources only** (no network, no timestamps) and
emits two auditable artefacts:

    docs/inventory/route-inventory.json
    docs/inventory/route-inventory.md

Run it from the forum repo root::

    python3 scripts/check_route_inventory.py

It exits 0 only when the generated inventory passes its own validation:
required schema, exactly two products (forum and guild), every array sorted,
no duplicate ``(product, kind, target)`` routes, every fact tagged with a
valid source, and no obvious secret-like strings in the output.

What is a "route" here
----------------------

A route is a functional target a product exposes or links to:

* ``page``        - a literal HTML file, or an internal ``href`` / document link.
* ``api``         - a root-relative server path (``/mcp``, ``/api/...``...).
* ``action``      - an HTML/JSX form action or submit handler.
* ``live_entry``  - a product root handed to this milestone as a known live URL.

Every route carries ``source: local_static`` (parsed from a local file) or
``source: known_live_url`` (one of the two live roots supplied for this
milestone).  External URLs are *not* treated as product routes; they are kept
in a clearly separate ``external_entry_points`` array.

Conservatism rules
------------------

* Fragment-only links (``#top``), ``data:``/``mailto:`` links, CSS/JS/image
  asset references and filesystem paths are never claimed as routes.
* Dynamic NodeBB routes are recorded only when a literal path exists in a
  local file; nothing is inferred from the live site.
* Only the Python standard library is used.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

SCHEMA = "route-inventory/v1"
GENERATED_BY = "scripts/check_route_inventory.py"

SOURCE_LOCAL = "local_static"
SOURCE_LIVE = "known_live_url"
SOURCE_KINDS = (SOURCE_LOCAL, SOURCE_LIVE)

KIND_ACTION = "action"
KIND_API = "api"
KIND_LIVE = "live_entry"
KIND_PAGE = "page"
ROUTE_KINDS = (KIND_ACTION, KIND_API, KIND_LIVE, KIND_PAGE)

DEFAULT_GUILD_ROOT = "/home/j/projects/adventurers-guild"
GUILD_ROOT_ENV = "GUILD_REPO"

FORUM_KNOWN_LIVE_URL = "https://forum.928174.xyz/"
GUILD_KNOWN_LIVE_URL = "https://adventurers-guild-tan.vercel.app/"

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
        ".next",
    }
)

# Extracted link targets with these extensions are assets, not pages.
ASSET_EXTENSIONS = frozenset(
    {
        ".css",
        ".js",
        ".mjs",
        ".cjs",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".svg",
        ".webp",
        ".ico",
        ".avif",
        ".bmp",
        ".woff",
        ".woff2",
        ".ttf",
        ".otf",
        ".eot",
        ".map",
        ".mp4",
        ".webm",
        ".mp3",
        ".wav",
        ".pdf",
        ".zip",
        ".gz",
        ".tar",
    }
)

# Root-relative strings that are filesystem paths or third-party API paths,
# never product routes.
DISALLOWED_ROOTS = frozenset(
    {
        "/home",
        "/usr",
        "/tmp",
        "/etc",
        "/var",
        "/opt",
        "/mnt",
        "/bin",
        "/sbin",
        "/lib",
        "/lib64",
        "/dev",
        "/proc",
        "/sys",
        "/root",
        "/users",
        "/private",
        "/nix",
        "/snap",
        "/run",
        "/media",
        "/srv",
        "/repos",
    }
)

PRODUCT_SPECS = {
    "forum": {
        "display_name": "台灣 AI 實戰論壇 / Taiwan AI Forum",
        "known_live_url": FORUM_KNOWN_LIVE_URL,
        # Literal local HTML files (recorded as page routes).
        "html_files": ("docs/index.html",),
        # Documentation / machine-readable files: internal links and bare URLs.
        "doc_files": (
            "README.md",
            "SKILL.md",
            "llms.txt",
            "bot-policy.json",
            "docs/skill.md",
            "docs/agent-quickstart.md",
            "mcp/README.md",
            "nodebb-mcp/README.md",
        ),
        # NodeBB / MCP source and config: quoted route path literals.
        "code_files": (
            "nodebb-mcp/nodebb_client.py",
            "nodebb-mcp/http_server.py",
            "nodebb-mcp/live_e2e.mjs",
            "worker/src/index.js",
            "scripts/nodebb-branding.py",
        ),
    },
    "guild": {
        "display_name": "Adventurers Guild / 冒險者公會",
        "known_live_url": GUILD_KNOWN_LIVE_URL,
        "html_files": (
            "index.html",
            "public/index.html",
            "public/profile.html",
            "public/task.html",
            "preview/index.html",
        ),
        "doc_files": (),
        "code_files": (
            "src/App.tsx",
            "src/main.tsx",
            "src/i18n.ts",
            "src/components/FirebaseProvider.tsx",
            "src/components/GuildProvider.tsx",
            "src/lib/api.ts",
            "src/lib/firebase.ts",
            "src/lib/supabase.ts",
        ),
        "config_files": (
            "package.json",
            "tsconfig.json",
            "vercel.json",
            "vite.config.ts",
        ),
    },
}

# Config files are inspected for a router dependency and route-like literals.
# The forum's machine-readable policy file is already covered by ``doc_files``.
PRODUCT_SPECS["forum"]["config_files"] = ()

REQUIRED_TOP_LEVEL_KEYS = (
    "schema",
    "generated_by",
    "deterministic",
    "network_used",
    "source_kinds",
    "route_kinds",
    "inputs",
    "scan_policy",
    "products",
)

REQUIRED_PRODUCT_FIELDS = (
    "id",
    "display_name",
    "known_live_url",
    "local_sources",
    "router_detected",
    "routes",
    "external_entry_points",
)

REQUIRED_ROUTE_FIELDS = (
    "product",
    "kind",
    "target",
    "source",
    "source_file",
    "source_files",
    "evidence",
)

REQUIRED_EXTERNAL_FIELDS = (
    "product",
    "url",
    "source",
    "source_files",
    "labels",
)

# --------------------------------------------------------------------------- #
# Generic helpers
# --------------------------------------------------------------------------- #


def canonical_json(value: object) -> str:
    """Stable string form of a value, used for sorting and duplicate checks."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


_SORT_PREFERENCE = (
    "product",
    "kind",
    "target",
    "url",
    "id",
    "source_file",
    "value",
    "path",
    "label",
    "name",
    "root",
)


def sort_key(item: object) -> tuple:
    """Deterministic ordering key that works for every array in the inventory.

    For objects the key is the tuple of preferred fields that are present, so
    routes sort by ``(product, kind, target)`` and external entries by
    ``(product, url)``.
    """

    if isinstance(item, dict):
        parts = []
        for field in _SORT_PREFERENCE:
            candidate = item.get(field)
            if isinstance(candidate, str):
                parts.append(candidate)
        if parts:
            return (0, tuple(parts), canonical_json(item))
        return (1, (), canonical_json(item))
    if isinstance(item, str):
        return (2, (), item, item)
    return (3, (), canonical_json(item), canonical_json(item))


def sort_deep(value: object) -> object:
    """Recursively sort every list so the output is byte-for-byte reproducible."""

    if isinstance(value, dict):
        return {key: sort_deep(item) for key, item in value.items()}
    if isinstance(value, list):
        return sorted((sort_deep(item) for item in value), key=sort_key)
    return value


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def rel_posix(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


# --------------------------------------------------------------------------- #
# Target normalization
# --------------------------------------------------------------------------- #

_URL_TRAILING = ".,;:!?)]}>\"'。，、；：！？）】》」』"

_TEMPLATE_EXPR_RE = re.compile(r"\$?\{[^{}]*\}")

PATH_LITERAL_RE = re.compile(
    r"""["'`](?P<p>/[A-Za-z0-9._~/-]*(?:\$?\{[^{}"'`]*\}[A-Za-z0-9._~/-]*)*)["'`]"""
)

MD_LINK_RE = re.compile(
    r"\]\(\s*<?([^)\s<>]+)>?(?:\s+[\"'][^\"']*[\"'])?\s*\)"
)

BARE_URL_RE = re.compile(
    r"https?://[^\s\"'<>`)\]}\u3002\uff0c\uff1b\uff1a\uff01\uff1f\u3001\uff09\u3011\u300b\u300d\u300f]+"
)

OPEN_TAG_RE = re.compile(r"<\s*([a-zA-Z][a-zA-Z0-9-]*)")


def clean_url(url: str) -> str:
    """Strip trailing punctuation from a URL found in prose/markup."""

    return url.strip().rstrip(_URL_TRAILING)


def normalize_target(raw: str, allow_root: bool = True):
    """Turn a raw link/path literal into a route target, or return ``None``.

    Returns ``None`` for fragments, assets, filesystem paths, external URLs
    and anything else that is not a product route.
    """

    target = raw.strip()
    if not target:
        return None
    if target.startswith(
        ("#", "//", "data:", "mailto:", "javascript:", "tel:", "blob:")
    ):
        return None
    if "://" in target:
        return None
    if target.lower().startswith(("http:", "https:")):
        return None
    if target.startswith("./"):
        target = target[2:]
    if target.startswith("../"):
        return None
    if "?" in target:
        target = target.split("?", 1)[0]
    if "#" in target:
        target = target.split("#", 1)[0]
    target = _TEMPLATE_EXPR_RE.sub("{param}", target)
    target = re.sub(r"\s+", "", target)
    if not target or ".." in target:
        return None
    if not allow_root and target == "/":
        return None
    if any(char in target for char in "<>`\"'()[]"):
        return None
    if "://" in target:
        return None
    suffix = Path(target).suffix.lower()
    if suffix in ASSET_EXTENSIONS:
        return None
    if target.startswith("/"):
        lowered = target.lower()
        for root in DISALLOWED_ROOTS:
            if lowered == root or lowered.startswith(root + "/"):
                return None
    else:
        # A relative target must look like a repo file/page reference.
        if "/" not in target and "." not in target:
            return None
    return target


def classify_target(target: str) -> str:
    """Classify a root-relative path as ``api`` or ``page``."""

    if target in ("/mcp", "/health", "/healthz"):
        return KIND_API
    if target == "/api" or target.startswith("/api/"):
        return KIND_API
    return KIND_PAGE


# --------------------------------------------------------------------------- #
# Markup scanning
# --------------------------------------------------------------------------- #


def iter_open_tags(text: str):
    """Yield ``(tag, attrs)`` for each opening tag, quote/brace aware.

    A ``>`` inside quotes or ``{...}`` (JSX handlers) does not end the tag.
    Comments and closing/declaration tags are skipped.
    """

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
        match = OPEN_TAG_RE.match(text, lt)
        if not match:
            index = lt + 1
            continue
        name = match.group(1).lower()
        cursor = match.end()
        quote = None
        brace = 0
        while cursor < length:
            char = text[cursor]
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
                break
            cursor += 1
        yield name, text[match.end():cursor]
        index = cursor + 1


def attr_value(attrs: str, name: str):
    match = re.search(rf"\b{re.escape(name)}\s*=\s*\"([^\"]*)\"", attrs, re.IGNORECASE)
    if match:
        return match.group(1)
    match = re.search(rf"\b{re.escape(name)}\s*=\s*'([^']*)'", attrs, re.IGNORECASE)
    if match:
        return match.group(1)
    return None


def jsx_attr_value(attrs: str, name: str):
    match = re.search(rf"\b{re.escape(name)}\s*=\s*\{{([^{{}}]*)\}}", attrs)
    return match.group(1).strip() if match else None


# --------------------------------------------------------------------------- #
# Route / external stores
# --------------------------------------------------------------------------- #


def new_route_store() -> dict:
    return {}


def add_route(
    store: dict,
    product: str,
    kind: str,
    target: str,
    source: str,
    source_file,
    evidence: str,
) -> None:
    key = (product, kind, target)
    entry = store.setdefault(
        key,
        {
            "product": product,
            "kind": kind,
            "target": target,
            "source": source,
            "source_files": set(),
            "evidence": set(),
        },
    )
    if source_file:
        entry["source_files"].add(source_file)
    if evidence:
        entry["evidence"].add(evidence)


def finalize_routes(store: dict) -> list:
    routes = []
    for entry in store.values():
        files = sorted(entry["source_files"])
        routes.append(
            {
                "product": entry["product"],
                "kind": entry["kind"],
                "target": entry["target"],
                "source": entry["source"],
                "source_file": files[0] if files else None,
                "source_files": files,
                "evidence": "; ".join(sorted(entry["evidence"])) or None,
            }
        )
    return sorted(routes, key=sort_key)


def new_external_store() -> dict:
    return {}


def add_external(store: dict, product: str, url: str, source_file, label: str) -> None:
    url = clean_url(url)
    if not url.lower().startswith(("http://", "https://")):
        return
    key = (product, url)
    entry = store.setdefault(
        key,
        {
            "product": product,
            "url": url,
            "source": SOURCE_LOCAL,
            "source_files": set(),
            "labels": set(),
        },
    )
    if source_file:
        entry["source_files"].add(source_file)
    if label:
        entry["labels"].add(label)


def finalize_external(store: dict, product: str, own_live_url: str) -> list:
    own = own_live_url.rstrip("/")
    entries = []
    for entry in store.values():
        if entry["url"].rstrip("/") == own:
            continue
        entries.append(
            {
                "product": entry["product"],
                "url": entry["url"],
                "source": entry["source"],
                "source_files": sorted(entry["source_files"]),
                "labels": sorted(entry["labels"]),
            }
        )
    return sorted(entries, key=sort_key)


# --------------------------------------------------------------------------- #
# Per-file scanners
# --------------------------------------------------------------------------- #


def record_link(
    product: str,
    rel: str,
    raw: str,
    route_store: dict,
    external_store: dict,
    label: str = "",
    evidence_kind: str = "href",
) -> None:
    raw = raw.strip()
    if not raw:
        return
    if raw.lower().startswith(
        ("data:", "mailto:", "javascript:", "tel:", "blob:", "file:", "about:")
    ):
        return
    if "://" in raw:
        add_external(external_store, product, raw, rel, label)
        return
    target = normalize_target(raw)
    if target is None:
        return
    add_route(
        route_store,
        product,
        classify_target(target),
        target,
        SOURCE_LOCAL,
        rel,
        f'{evidence_kind}="{raw}"',
    )


def scan_html(rel: str, text: str, product: str, route_store: dict, external_store: dict) -> None:
    for tag, attrs in iter_open_tags(text):
        if tag in ("a", "area", "link"):
            href = attr_value(attrs, "href")
            if href is not None:
                record_link(product, rel, href, route_store, external_store)
        elif tag == "form":
            scan_form(rel, attrs, product, route_store, external_store)


def scan_form(rel: str, attrs: str, product: str, route_store: dict, external_store: dict) -> None:
    action = attr_value(attrs, "action")
    if action and action.strip() not in ("", "#"):
        raw = action.strip()
        if "://" in raw:
            add_external(external_store, product, raw, rel, "")
        else:
            target = normalize_target(raw) or raw
            add_route(
                route_store,
                product,
                KIND_ACTION,
                target,
                SOURCE_LOCAL,
                rel,
                f'form action="{raw}"',
            )
    onsubmit = attr_value(attrs, "onsubmit")
    jsx_submit = jsx_attr_value(attrs, "onSubmit")
    handler = onsubmit or jsx_submit
    if handler and handler.strip() not in ("", "#"):
        attribute = "onsubmit" if onsubmit else "onSubmit"
        add_route(
            route_store,
            product,
            KIND_ACTION,
            handler.strip(),
            SOURCE_LOCAL,
            rel,
            f"form submit handler ({attribute})",
        )


def scan_doc(rel: str, text: str, product: str, route_store: dict, external_store: dict) -> None:
    for match in MD_LINK_RE.finditer(text):
        record_link(
            product,
            rel,
            match.group(1),
            route_store,
            external_store,
            evidence_kind="markdown link target",
        )
    for match in BARE_URL_RE.finditer(text):
        add_external(external_store, product, match.group(0), rel, "")
    for match in PATH_LITERAL_RE.finditer(text):
        raw = match.group("p")
        target = normalize_target(raw)
        if target is None:
            continue
        add_route(
            route_store,
            product,
            classify_target(target),
            target,
            SOURCE_LOCAL,
            rel,
            "quoted route path literal",
        )


def scan_code(rel: str, text: str, product: str, route_store: dict) -> None:
    for match in PATH_LITERAL_RE.finditer(text):
        raw = match.group("p")
        target = normalize_target(raw, allow_root=False)
        if target is None:
            continue
        add_route(
            route_store,
            product,
            classify_target(target),
            target,
            SOURCE_LOCAL,
            rel,
            "quoted route path literal",
        )


# --------------------------------------------------------------------------- #
# Inventory assembly
# --------------------------------------------------------------------------- #


def resolve_source_files(root: Path, rel_files):
    resolved = []
    for rel in rel_files:
        path = root / rel
        if path.is_file():
            resolved.append((rel, path))
    return resolved


def collect_product(product_id: str, root, root_available: bool) -> dict:
    spec = PRODUCT_SPECS[product_id]
    route_store = new_route_store()
    external_store = new_external_store()
    local_sources = []
    router_detected = False

    if root_available and Path(root).is_dir():
        root_path = Path(root)

        for rel, path in resolve_source_files(root_path, spec["html_files"]):
            local_sources.append(rel)
            add_route(
                route_store,
                product_id,
                KIND_PAGE,
                rel,
                SOURCE_LOCAL,
                rel,
                "literal HTML page file",
            )
            scan_html(rel, read_text(path), product_id, route_store, external_store)

        for rel, path in resolve_source_files(root_path, spec["doc_files"]):
            local_sources.append(rel)
            scan_doc(rel, read_text(path), product_id, route_store, external_store)

        for rel, path in resolve_source_files(root_path, spec["code_files"]):
            local_sources.append(rel)
            text = read_text(path)
            if "react-router" in text.lower():
                router_detected = True
            scan_code(rel, text, product_id, route_store)
            # React/JSX components can carry literal hrefs and form handlers too.
            if Path(rel).suffix.lower() in (".tsx", ".jsx"):
                scan_html(rel, text, product_id, route_store, external_store)

        for rel, path in resolve_source_files(root_path, spec.get("config_files", ())):
            local_sources.append(rel)
            text = read_text(path)
            if "react-router" in text.lower():
                router_detected = True
            scan_code(rel, text, product_id, route_store)

    add_route(
        route_store,
        product_id,
        KIND_LIVE,
        "/",
        SOURCE_LIVE,
        None,
        f"supplied known live URL: {spec['known_live_url']}",
    )

    routes = finalize_routes(route_store)
    externals = finalize_external(external_store, product_id, spec["known_live_url"])

    return {
        "id": product_id,
        "display_name": spec["display_name"],
        "known_live_url": spec["known_live_url"],
        "local_sources": sorted(set(local_sources)),
        "router_detected": router_detected,
        "routes": routes,
        "external_entry_points": externals,
    }


def build_inventory(
    forum_root,
    guild_root,
    guild_root_label: str,
    guild_root_source: str,
) -> dict:
    forum_available = Path(forum_root).is_dir()
    guild_available = guild_root is not None and Path(guild_root).is_dir()

    products = [
        collect_product("forum", forum_root, forum_available),
        collect_product("guild", guild_root, guild_available),
    ]

    inventory = {
        "schema": SCHEMA,
        "generated_by": GENERATED_BY,
        "deterministic": True,
        "network_used": False,
        "source_kinds": sorted(SOURCE_KINDS),
        "route_kinds": sorted(ROUTE_KINDS),
        "inputs": {
            "forum_root": ".",
            "guild_root": guild_root_label,
            "guild_root_available": guild_available,
            "guild_root_configured_via": guild_root_source,
        },
        "scan_policy": {
            "forum_html_files": sorted(PRODUCT_SPECS["forum"]["html_files"]),
            "forum_doc_files": sorted(PRODUCT_SPECS["forum"]["doc_files"]),
            "forum_code_files": sorted(PRODUCT_SPECS["forum"]["code_files"]),
            "forum_config_files": sorted(PRODUCT_SPECS["forum"]["config_files"]),
            "guild_html_files": sorted(PRODUCT_SPECS["guild"]["html_files"]),
            "guild_doc_files": sorted(PRODUCT_SPECS["guild"]["doc_files"]),
            "guild_code_files": sorted(PRODUCT_SPECS["guild"]["code_files"]),
            "guild_config_files": sorted(PRODUCT_SPECS["guild"]["config_files"]),
            "excluded_dir_parts": sorted(EXCLUDE_DIR_PARTS),
            "note": (
                "Routes are discovered only from the files listed above plus the two "
                "supplied known live roots. Fragment links, external URLs, asset "
                "references (CSS/JS/images) and filesystem paths are never recorded as "
                "product routes; external URLs live in a separate array."
            ),
        },
        "products": products,
    }
    return sort_deep(inventory)


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


def validate_inventory(inventory) -> list:
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
    if inventory.get("source_kinds") != sorted(SOURCE_KINDS):
        problems.append(f"source_kinds must be {sorted(SOURCE_KINDS)!r}")
    if inventory.get("route_kinds") != sorted(ROUTE_KINDS):
        problems.append(f"route_kinds must be {sorted(ROUTE_KINDS)!r}")

    products = inventory.get("products")
    if not isinstance(products, list):
        problems.append("products must be a list")
        return problems
    if len(products) != 2:
        problems.append(f"products must contain exactly two entries, found {len(products)}")

    all_routes = []
    product_ids = []
    for product in products:
        if not isinstance(product, dict):
            problems.append("every product must be an object")
            continue
        product_id = product.get("id")
        product_ids.append(product_id)
        for field in REQUIRED_PRODUCT_FIELDS:
            if field not in product:
                problems.append(f"{product_id}: missing required field {field!r}")
        if not isinstance(product.get("router_detected"), bool):
            problems.append(f"{product_id}: router_detected must be a boolean")
        if not isinstance(product.get("local_sources"), list):
            problems.append(f"{product_id}: local_sources must be a list")

        routes = product.get("routes")
        if not isinstance(routes, list):
            problems.append(f"{product_id}: routes must be a list")
        else:
            live_entries = [route for route in routes if route.get("kind") == KIND_LIVE]
            if len(live_entries) != 1:
                problems.append(f"{product_id}: exactly one live_entry route required")
            for route in routes:
                if not isinstance(route, dict):
                    problems.append(f"{product_id}: every route must be an object")
                    continue
                for field in REQUIRED_ROUTE_FIELDS:
                    if field not in route:
                        problems.append(f"{product_id}: route missing field {field!r}")
                if route.get("product") != product_id:
                    problems.append(
                        f"{product_id}: route product {route.get('product')!r} mismatch"
                    )
                if route.get("kind") not in ROUTE_KINDS:
                    problems.append(f"{product_id}: invalid route kind {route.get('kind')!r}")
                if route.get("source") not in SOURCE_KINDS:
                    problems.append(f"{product_id}: invalid route source {route.get('source')!r}")
                target = route.get("target")
                if not isinstance(target, str) or not target:
                    problems.append(f"{product_id}: route target must be a non-empty string")
                elif "://" in target:
                    problems.append(f"{product_id}: route target must not be an external URL")
                if route.get("source") == SOURCE_LOCAL:
                    if not isinstance(route.get("source_file"), str) or not route.get("source_file"):
                        problems.append(
                            f"{product_id}: local route {target!r} needs a source_file"
                        )
                source_files = route.get("source_files")
                if not isinstance(source_files, list):
                    problems.append(f"{product_id}: route source_files must be a list")
                all_routes.append(route)

        externals = product.get("external_entry_points")
        if not isinstance(externals, list):
            problems.append(f"{product_id}: external_entry_points must be a list")
        else:
            for item in externals:
                if not isinstance(item, dict):
                    problems.append(f"{product_id}: every external entry must be an object")
                    continue
                for field in REQUIRED_EXTERNAL_FIELDS:
                    if field not in item:
                        problems.append(f"{product_id}: external entry missing field {field!r}")
                if item.get("product") != product_id:
                    problems.append(f"{product_id}: external entry product mismatch")
                url = item.get("url")
                if not isinstance(url, str) or "://" not in url:
                    problems.append(f"{product_id}: external entry url must be absolute")
                if item.get("source") != SOURCE_LOCAL:
                    problems.append(f"{product_id}: external entry source must be local_static")

    if product_ids != ["forum", "guild"]:
        problems.append(f"product ids must be ['forum', 'guild'], found {product_ids!r}")

    # No duplicate (product, kind, target) routes.
    seen = set()
    for route in all_routes:
        key = (route.get("product"), route.get("kind"), route.get("target"))
        if key in seen:
            problems.append(f"duplicate route (product, kind, target): {key!r}")
        seen.add(key)

    # Every array sorted and free of exact duplicates.
    for path, array in _walk_arrays(inventory):
        if array != sorted(array, key=sort_key):
            problems.append(f"{path}: array is not sorted")
        markers = [canonical_json(item) for item in array]
        if len(markers) != len(set(markers)):
            problems.append(f"{path}: array contains duplicate entries")

    # No obvious secret-like strings.
    blob = json.dumps(inventory, ensure_ascii=False, sort_keys=True)
    for label, pattern in SECRET_PATTERNS:
        if pattern.search(blob):
            problems.append(f"secret-like string detected ({label})")

    return problems


# --------------------------------------------------------------------------- #
# Markdown rendering
# --------------------------------------------------------------------------- #


def _md_table(rows, header, empty="_(none detected)_"):
    rows = list(rows)
    if not rows:
        return empty
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    for row in rows:
        cells = [str(cell).replace("|", "\\|") for cell in row]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def render_markdown(inventory: dict) -> str:
    lines = [
        "# Route inventory — forum × Adventurers Guild",
        "",
        f"Schema: `{inventory['schema']}`",
        (
            f"Generated by: `{inventory['generated_by']}` "
            "(deterministic, no network, no timestamps)."
        ),
        "",
        inventory["scan_policy"]["note"],
        "",
        "## Inputs",
        "",
        f"- Forum root: `{inventory['inputs']['forum_root']}`",
        f"- Guild root: `{inventory['inputs']['guild_root']}` "
        f"(available: {str(inventory['inputs']['guild_root_available']).lower()}, "
        f"via `{inventory['inputs']['guild_root_configured_via']}`)",
        f"- Network used: {str(inventory['network_used']).lower()}",
        f"- Route kinds: " + ", ".join(f"`{kind}`" for kind in inventory["route_kinds"]),
        f"- Fact sources: " + ", ".join(f"`{kind}`" for kind in inventory["source_kinds"]),
        "",
        "External URLs are **not** product routes; they are listed separately as "
        "`external_entry_points`.",
        "",
    ]

    for product in inventory["products"]:
        lines.extend(
            [
                f"## {product['id'].capitalize()} — {product['display_name']}",
                "",
                f"- Known live URL: `{product['known_live_url']}`",
                f"- React Router detected: `{str(product['router_detected']).lower()}`",
                f"- Local sources: {len(product['local_sources'])} file(s)",
                f"- Routes: {len(product['routes'])}",
                f"- External entry points: {len(product['external_entry_points'])}",
                "",
                "### Local sources",
                "",
                (
                    "\n".join(f"- `{rel}`" for rel in product["local_sources"])
                    if product["local_sources"]
                    else "_(none)_"
                ),
                "",
                "### Routes",
                "",
                _md_table(
                    (
                        (
                            route["kind"],
                            f"`{route['target']}`",
                            route["source"],
                            ", ".join(f"`{rel}`" for rel in route["source_files"]) or "_null_",
                            route["evidence"] or "",
                        )
                        for route in product["routes"]
                    ),
                    ("kind", "target", "source", "source file(s)", "evidence"),
                ),
                "",
                "### External entry points",
                "",
                _md_table(
                    (
                        (
                            entry["url"],
                            ", ".join(f"`{rel}`" for rel in entry["source_files"]) or "",
                            ", ".join(entry["labels"]),
                        )
                        for entry in product["external_entry_points"]
                    ),
                    ("url", "source file(s)", "labels"),
                ),
                "",
            ]
        )

    return "\n".join(lines).rstrip() + "\n"


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def default_paths(forum_root: Path):
    return (
        forum_root / "docs" / "inventory" / "route-inventory.json",
        forum_root / "docs" / "inventory" / "route-inventory.md",
    )


def resolve_guild_root(explicit):
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
    guild_root_label, guild_root_source = resolve_guild_root(args.guild_root)
    guild_root = Path(guild_root_label) if Path(guild_root_label).is_dir() else None

    inventory = build_inventory(
        forum_root, guild_root, guild_root_label, guild_root_source
    )
    problems = validate_inventory(inventory)

    if problems:
        print("route inventory: INVALID", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    json_out, md_out = default_paths(forum_root)
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

    print("route inventory: OK")
    print(f"  schema: {inventory['schema']}")
    print(f"  products: {', '.join(product['id'] for product in inventory['products'])}")
    for product in inventory["products"]:
        counts = {}
        for route in product["routes"]:
            counts[route["kind"]] = counts.get(route["kind"], 0) + 1
        summary = ", ".join(f"{kind}={counts[kind]}" for kind in sorted(counts))
        print(f"  {product['id']}: {len(product['routes'])} routes ({summary})")
    print(f"  wrote {json_out}")
    print(f"  wrote {md_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
