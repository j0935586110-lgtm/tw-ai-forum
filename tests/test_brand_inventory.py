"""Unit tests for the M0.1 brand inventory script.

These tests are hermetic: they never touch the network.  Parser helpers are
exercised directly, and the CLI is smoke-tested with output redirected into a
temporary directory.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts" / "check_brand_inventory.py"

_spec = importlib.util.spec_from_file_location("check_brand_inventory_under_test", MODULE_PATH)
assert _spec and _spec.loader
brand = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(brand)


# --------------------------------------------------------------------------- #
# Color parsing
# --------------------------------------------------------------------------- #


def test_extract_colors_normalizes_hex_and_functions():
    text = "color:#FFF; background: rgba(212, 175, 55, 0.5); border: 1px solid #0B0D10"
    assert brand.extract_colors(text) == [
        ("#0b0d10", "hex"),
        ("#fff", "hex"),
        ("rgba(212,175,55,0.5)", "function"),
    ]


def test_extract_colors_needs_a_color_context_for_named_tokens():
    # "tan" inside a hostname must not be mistaken for the CSS colour "tan".
    text = 'href="https://adventurers-guild-tan.vercel.app/"'
    assert brand.extract_colors(text) == []


def test_extract_colors_reads_css_variables_and_tailwind_classes():
    text = ":root { --gold: #d4af37; }\n<button class=\"bg-black/80 text-white border-red-500/40\">"
    values = {value for value, _kind in brand.extract_colors(text)}
    assert {"gold", "black", "white", "red", "red-500", "#d4af37"} <= values


# --------------------------------------------------------------------------- #
# External links
# --------------------------------------------------------------------------- #


def test_extract_external_links_strips_trailing_punctuation_and_sorts():
    text = "See https://example.com/a. Also (https://example.com/b) and https://example.com/a"
    assert brand.extract_external_links(text) == [
        "https://example.com/a",
        "https://example.com/b",
    ]


# --------------------------------------------------------------------------- #
# Fonts
# --------------------------------------------------------------------------- #


def test_extract_fonts_handles_family_shorthand_and_custom_properties():
    text = (
        'body { font-family: "Noto Sans TC", system-ui, sans-serif; }\n'
        "h1 { font: 16px/1.75 -apple-system, serif; }\n"
        "@theme { --font-serif: \"Cinzel\", serif; }\n"
        '<h1 className="font-serif">x</h1>'
    )
    tokens = set(brand.extract_fonts(text))
    assert ("Noto Sans TC", "font_family") in tokens
    assert ("system-ui", "font_family") in tokens
    assert ("-apple-system", "font_family") in tokens
    assert ("Cinzel", "font_family") in tokens
    assert ("--font-serif", "font_reference") in tokens
    assert ("font-serif", "font_reference") in tokens


# --------------------------------------------------------------------------- #
# CTAs
# --------------------------------------------------------------------------- #


def test_extract_ctas_handles_href_handlers_and_t_keys():
    text = (
        '<a class="cta" href="https://example.com/join">進入論壇 →</a>'
        '<button onclick="postQuest(event)">發佈任務</button>'
        "<button onClick={() => setActiveTab('board')}>{t('post_commission')}</button>"
    )
    ctas = brand.extract_ctas(text)
    by_label = {cta["label"]: cta for cta in ctas}
    assert by_label["進入論壇 →"]["target"] == "https://example.com/join"
    assert by_label["進入論壇 →"]["target_kind"] == "href"
    assert by_label["發佈任務"]["target"] == "postQuest(event)"
    assert by_label["發佈任務"]["target_kind"] == "handler"
    # The ">" inside the arrow function must not truncate the opening tag.
    assert by_label["post_commission"]["label_key"] == "post_commission"
    assert by_label["post_commission"]["target"] == "() => setActiveTab('board')"


def test_extract_ctas_reads_nested_labels_and_ignores_script_templates():
    text = (
        '<a class="wordmark" href="#top"><strong>冒險者之公會</strong>'
        "<small>Guild Network</small></a>"
        "<script>const html = `<a href=\"x\">not static</a>`;</script>"
    )
    ctas = brand.extract_ctas(text)
    assert len(ctas) == 1
    assert "冒險者之公會" in ctas[0]["label"]
    assert ctas[0]["target"] == "#top"


# --------------------------------------------------------------------------- #
# Names and brand markers
# --------------------------------------------------------------------------- #


def test_extract_name_candidates_uses_only_first_markdown_h1():
    text = "# Real title\n\ntext\n\n# Section one\n\n# Section two\n"
    candidates = brand.extract_name_candidates(text, "doc.md")
    assert candidates == [{"value": "Real title", "kind": "markdown_h1", "path": "doc.md"}]


def test_extract_name_candidates_reads_html_title_and_h1():
    text = "<title>Site — tagline</title><h1>Product</h1>"
    kinds = {candidate["kind"] for candidate in brand.extract_name_candidates(text, "index.html")}
    assert kinds == {"html_title", "html_h1"}


def test_extract_brand_markers_finds_wordmark_favicon_and_og_image():
    text = (
        '<link rel="icon" href="data:image/svg+xml,<svg/>">'
        '<meta property="og:image" content="https://example.com/og.png">'
        '<a class="wordmark" href="/"><span>G</span><strong>冒險者之公會</strong></a>'
    )
    markers = brand.extract_brand_markers(text, "index.html")
    assert markers["og_image"] == "https://example.com/og.png"
    assert markers["favicon"] == "data:image/svg+xml,<svg/>"
    assert markers["wordmarks"] == ["G 冒險者之公會"]


# --------------------------------------------------------------------------- #
# Deterministic ordering and validation
# --------------------------------------------------------------------------- #


def test_sort_deep_sorts_lists_and_uses_id_first():
    value = {"products": [{"id": "guild", "x": [3, 1, 2]}, {"id": "forum", "x": []}]}
    sorted_value = brand.sort_deep(value)
    assert [product["id"] for product in sorted_value["products"]] == ["forum", "guild"]
    assert sorted_value["products"][1]["x"] == [1, 2, 3]


def _minimal_inventory():
    return brand.build_inventory(REPO_ROOT, None, "unavailable")


def test_build_inventory_produces_two_valid_products():
    inventory = _minimal_inventory()
    assert [product["id"] for product in inventory["products"]] == ["forum", "guild"]
    assert brand.validate_inventory(inventory) == []


def test_validate_detects_unsorted_array():
    inventory = _minimal_inventory()
    inventory["source_kinds"] = list(reversed(inventory["source_kinds"]))
    problems = brand.validate_inventory(inventory)
    assert any("not sorted" in problem for problem in problems)


def test_validate_detects_duplicate_urls():
    inventory = _minimal_inventory()
    live = inventory["products"][0]["live_urls"]
    live.append(dict(live[0]))
    problems = brand.validate_inventory(inventory)
    assert any("duplicate URLs" in problem for problem in problems)


def test_validate_detects_missing_required_field():
    inventory = _minimal_inventory()
    del inventory["products"][0]["colors"]
    problems = brand.validate_inventory(inventory)
    assert any("missing required field 'colors'" in problem for problem in problems)


def test_validate_rejects_wrong_product_count():
    inventory = _minimal_inventory()
    inventory["products"] = inventory["products"][:1]
    problems = brand.validate_inventory(inventory)
    assert any("exactly two" in problem for problem in problems)


def test_validate_detects_secret_like_strings():
    inventory = _minimal_inventory()
    inventory["products"][0]["name"] = "api_key=ABCDEFGHIJKLMNOPQRSTUV"
    problems = brand.validate_inventory(inventory)
    assert any("secret-like" in problem for problem in problems)


# --------------------------------------------------------------------------- #
# CLI smoke test
# --------------------------------------------------------------------------- #


def test_cli_is_deterministic_and_writes_valid_output(tmp_path):
    json_out = tmp_path / "brand-inventory.json"
    md_out = tmp_path / "brand-inventory.md"
    command = [
        sys.executable,
        str(MODULE_PATH),
        "--guild-root",
        str(tmp_path / "missing-guild"),  # hermetic: no dependence on a checkout
        "--json-out",
        str(json_out),
        "--md-out",
        str(md_out),
    ]
    first = subprocess.run(command, cwd=REPO_ROOT, capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    assert "brand inventory: OK" in first.stdout

    inventory = json.loads(json_out.read_text(encoding="utf-8"))
    assert brand.validate_inventory(inventory) == []
    assert md_out.exists() and md_out.read_text(encoding="utf-8").strip()

    first_json = json_out.read_bytes()
    second = subprocess.run(command, cwd=REPO_ROOT, capture_output=True, text=True)
    assert second.returncode == 0, second.stderr
    assert json_out.read_bytes() == first_json
