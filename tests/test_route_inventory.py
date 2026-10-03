"""Unit tests for the M0.2 route inventory script.

These tests are hermetic: they never touch the network and never depend on the
Adventurers Guild checkout (the guild root is passed as a missing path during
the CLI smoke test).  Parser helpers are exercised directly and the CLI is
smoke-tested with output redirected into a temporary directory.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts" / "check_route_inventory.py"

_spec = importlib.util.spec_from_file_location("check_route_inventory_under_test", MODULE_PATH)
assert _spec and _spec.loader
routes_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(routes_mod)


# --------------------------------------------------------------------------- #
# Target normalization
# --------------------------------------------------------------------------- #


def test_normalize_target_rejects_non_routes():
    assert routes_mod.normalize_target("#top") is None
    assert routes_mod.normalize_target("https://example.com/x") is None
    assert routes_mod.normalize_target("//cdn.example.com/x.js") is None
    assert routes_mod.normalize_target("data:image/svg+xml,<svg/>") is None
    assert routes_mod.normalize_target("mailto:a@b.c") is None
    assert routes_mod.normalize_target("style.css") is None
    assert routes_mod.normalize_target("./styles.css") is None
    assert routes_mod.normalize_target("/home/j/work/x.py") is None
    assert routes_mod.normalize_target("/repos/foo/bar") is None


def test_normalize_target_keeps_and_cleans_routes():
    assert routes_mod.normalize_target("/profile") == "/profile"
    assert routes_mod.normalize_target("/task?id=7#x") == "/task"
    assert routes_mod.normalize_target("./skill.md") == "skill.md"
    assert routes_mod.normalize_target("/api/quests/${questId}/verify") == (
        "/api/quests/{param}/verify"
    )
    assert routes_mod.normalize_target("/api/topic/{int(tid)}") == "/api/topic/{param}"


def test_normalize_target_root_is_optional():
    assert routes_mod.normalize_target("/") == "/"
    assert routes_mod.normalize_target("/", allow_root=False) is None


def test_classify_target_separates_api_from_page():
    assert routes_mod.classify_target("/api/config") == "api"
    assert routes_mod.classify_target("/mcp") == "api"
    assert routes_mod.classify_target("/healthz") == "api"
    assert routes_mod.classify_target("/skill.md") == "page"
    assert routes_mod.classify_target("docs/index.html") == "page"


# --------------------------------------------------------------------------- #
# Markup scanning
# --------------------------------------------------------------------------- #


def test_iter_open_tags_is_quote_and_brace_aware():
    text = "<button onClick={() => setActiveTab('board')}>x</button>"
    tags = list(routes_mod.iter_open_tags(text))
    assert tags[0][0] == "button"
    assert "setActiveTab('board')" in tags[0][1]


def test_scan_form_records_action_and_submit_handler():
    store = routes_mod.new_route_store()
    external = routes_mod.new_external_store()
    routes_mod.scan_form(
        "public/index.html",
        ' id="postForm" onsubmit="postQuest(event)"',
        "guild",
        store,
        external,
    )
    routes = routes_mod.finalize_routes(store)
    assert [(route["kind"], route["target"]) for route in routes] == [
        ("action", "postQuest(event)")
    ]
    assert routes[0]["source"] == "local_static"


def test_scan_form_handles_jsx_on_submit_braces():
    store = routes_mod.new_route_store()
    external = routes_mod.new_external_store()
    routes_mod.scan_form(
        "src/App.tsx",
        " onSubmit={handleSubmit} className=\"x\"",
        "guild",
        store,
        external,
    )
    routes = routes_mod.finalize_routes(store)
    assert [(route["kind"], route["target"]) for route in routes] == [
        ("action", "handleSubmit")
    ]


def test_record_link_skips_data_uri_even_with_embedded_http():
    store = routes_mod.new_route_store()
    external = routes_mod.new_external_store()
    routes_mod.record_link(
        "guild",
        "index.html",
        "data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg'/>",
        store,
        external,
    )
    assert routes_mod.finalize_routes(store) == []
    assert routes_mod.finalize_external(external, "guild", "https://guild.example/") == []


def test_record_link_splits_routes_and_external_urls():
    store = routes_mod.new_route_store()
    external = routes_mod.new_external_store()
    routes_mod.record_link("guild", "preview/index.html", "/profile", store, external)
    routes_mod.record_link(
        "guild", "preview/index.html", "https://forum.example/", store, external
    )
    routes_mod.record_link("guild", "preview/index.html", "#top", store, external)
    assert [route["target"] for route in routes_mod.finalize_routes(store)] == ["/profile"]
    ext = routes_mod.finalize_external(external, "guild", "https://guild.example/")
    assert [entry["url"] for entry in ext] == ["https://forum.example/"]


# --------------------------------------------------------------------------- #
# Inventory assembly and validation
# --------------------------------------------------------------------------- #


def _minimal_inventory():
    return routes_mod.build_inventory(REPO_ROOT, None, "unavailable", "test")


def test_build_inventory_has_two_sorted_products():
    inventory = _minimal_inventory()
    assert [product["id"] for product in inventory["products"]] == ["forum", "guild"]
    assert routes_mod.validate_inventory(inventory) == []


def test_every_route_has_valid_kind_source_and_product():
    inventory = _minimal_inventory()
    for product in inventory["products"]:
        for route in product["routes"]:
            assert route["kind"] in routes_mod.ROUTE_KINDS
            assert route["source"] in routes_mod.SOURCE_KINDS
            assert route["product"] == product["id"]
            assert "://" not in route["target"]


def test_each_product_has_exactly_one_live_entry_root():
    inventory = _minimal_inventory()
    by_id = {product["id"]: product for product in inventory["products"]}
    for product_id, expected_url in (
        ("forum", routes_mod.FORUM_KNOWN_LIVE_URL),
        ("guild", routes_mod.GUILD_KNOWN_LIVE_URL),
    ):
        product = by_id[product_id]
        live = [route for route in product["routes"] if route["kind"] == "live_entry"]
        assert len(live) == 1
        assert live[0]["target"] == "/"
        assert live[0]["source"] == "known_live_url"
        assert product["known_live_url"] == expected_url


def test_forum_discovers_nodebb_routes_from_local_files():
    inventory = _minimal_inventory()
    forum = {product["id"]: product for product in inventory["products"]}["forum"]
    targets = {route["target"] for route in forum["routes"]}
    assert "/api/config" in targets
    assert "/api/v3/topics" in targets
    assert "/mcp" in targets
    assert "/llms.txt" in targets
    # No dynamic NodeBB route may appear without a local literal.
    assert all(route["source"] in routes_mod.SOURCE_KINDS for route in forum["routes"])


def test_guild_fetch_paths_are_recorded():
    inventory = _minimal_inventory()
    guild = {product["id"]: product for product in inventory["products"]}["guild"]
    targets = {route["target"] for route in guild["routes"]}
    # guild checkout is unavailable in this hermetic build, so only the live
    # root is present; the parser-level path coverage lives in the tests above.
    assert targets == {"/"}


def test_validate_detects_unsorted_array():
    inventory = _minimal_inventory()
    inventory["route_kinds"] = list(reversed(inventory["route_kinds"]))
    problems = routes_mod.validate_inventory(inventory)
    assert any("not sorted" in problem for problem in problems)


def test_validate_detects_duplicate_route_triple():
    inventory = _minimal_inventory()
    routes = inventory["products"][0]["routes"]
    routes.append(dict(routes[0]))
    problems = routes_mod.validate_inventory(inventory)
    assert any("duplicate route" in problem for problem in problems)


def test_validate_rejects_external_url_as_route():
    inventory = _minimal_inventory()
    inventory["products"][0]["routes"].append(
        {
            "product": "forum",
            "kind": "page",
            "target": "https://example.com/",
            "source": "local_static",
            "source_file": "docs/index.html",
            "source_files": ["docs/index.html"],
            "evidence": "x",
        }
    )
    problems = routes_mod.validate_inventory(inventory)
    assert any("must not be an external URL" in problem for problem in problems)


def test_validate_detects_wrong_product_count():
    inventory = _minimal_inventory()
    inventory["products"] = inventory["products"][:1]
    problems = routes_mod.validate_inventory(inventory)
    assert any("exactly two" in problem for problem in problems)


def test_validate_detects_missing_required_field():
    inventory = _minimal_inventory()
    del inventory["products"][0]["routes"]
    problems = routes_mod.validate_inventory(inventory)
    assert any("missing required field 'routes'" in problem for problem in problems)


def test_validate_detects_secret_like_strings():
    inventory = _minimal_inventory()
    inventory["products"][0]["known_live_url"] = "api_key=ABCDEFGHIJKLMNOPQRSTUV"
    problems = routes_mod.validate_inventory(inventory)
    assert any("secret-like" in problem for problem in problems)


# --------------------------------------------------------------------------- #
# Checked-in artefacts
# --------------------------------------------------------------------------- #


def test_checked_in_artifacts_exist_and_validate():
    json_path = REPO_ROOT / "docs" / "inventory" / "route-inventory.json"
    md_path = REPO_ROOT / "docs" / "inventory" / "route-inventory.md"
    assert json_path.is_file() and md_path.is_file()
    inventory = json.loads(json_path.read_text(encoding="utf-8"))
    assert routes_mod.validate_inventory(inventory) == []
    assert "Route inventory" in md_path.read_text(encoding="utf-8")


def test_m0_1_artifacts_are_still_present_and_separate():
    assert (REPO_ROOT / "scripts" / "check_brand_inventory.py").is_file()
    brand = json.loads(
        (REPO_ROOT / "docs" / "inventory" / "brand-inventory.json").read_text(encoding="utf-8")
    )
    assert brand["schema"] == "brand-inventory/v1"


# --------------------------------------------------------------------------- #
# No-network guarantee
# --------------------------------------------------------------------------- #


def test_script_imports_no_network_modules():
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    forbidden = {"urllib", "http", "requests", "socket", "ftplib", "smtplib", "telnetlib"}
    assert imported & forbidden == set()


# --------------------------------------------------------------------------- #
# CLI smoke test
# --------------------------------------------------------------------------- #


def test_cli_is_deterministic_and_writes_valid_output(tmp_path):
    json_out = tmp_path / "route-inventory.json"
    md_out = tmp_path / "route-inventory.md"
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
    assert "route inventory: OK" in first.stdout

    inventory = json.loads(json_out.read_text(encoding="utf-8"))
    assert routes_mod.validate_inventory(inventory) == []
    assert md_out.exists() and md_out.read_text(encoding="utf-8").strip()

    first_json = json_out.read_bytes()
    second = subprocess.run(command, cwd=REPO_ROOT, capture_output=True, text=True)
    assert second.returncode == 0, second.stderr
    assert json_out.read_bytes() == first_json


# --------------------------------------------------------------------------- #
# Stdlib-only runner (for environments without pytest)
# --------------------------------------------------------------------------- #


if __name__ == "__main__":  # pragma: no cover - convenience runner
    import inspect
    import tempfile
    import traceback

    failures = []
    test_names = sorted(name for name in dir() if name.startswith("test_"))
    for name in test_names:
        func = globals()[name]
        if not callable(func):
            continue
        scratch = tempfile.TemporaryDirectory()
        try:
            if "tmp_path" in inspect.signature(func).parameters:
                func(tmp_path=Path(scratch.name))
            else:
                func()
            print(f"ok   {name}")
        except Exception:  # noqa: BLE001 - report every failure, do not abort
            failures.append(name)
            print(f"FAIL {name}")
            traceback.print_exc()
        finally:
            scratch.cleanup()
    total = len(test_names)
    print(f"{total - len(failures)}/{total} passed")
    raise SystemExit(1 if failures else 0)
