"""Stdlib-only tests for the Forum x Guild shared entry homepage (M2.1).

The tests are hermetic: they never touch the network and they never mutate the
committed homepage files. Mutation cases copy ``docs/home/``, ``docs/cis/`` and
``docs/cis-preview.html`` into a temporary directory and point the checker at it
with ``--root``.

They run under pytest (plain functions with no fixtures) and, if pytest is not
installed, under the fallback runner at the bottom of this file::

    python3 tests/test_homepage.py
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
MODULE_PATH = REPO_ROOT / "scripts" / "check_homepage.py"
HOME_DIR = REPO_ROOT / "docs" / "home"
CIS_DIR = REPO_ROOT / "docs" / "cis"

_spec = importlib.util.spec_from_file_location("check_homepage_under_test", MODULE_PATH)
assert _spec and _spec.loader
home = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(home)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _index_text() -> str:
    return (HOME_DIR / "index.html").read_text(encoding="utf-8")


def _styles_text() -> str:
    return (HOME_DIR / "styles.css").read_text(encoding="utf-8")


def _manifest_text() -> str:
    return (HOME_DIR / "manifest.webmanifest").read_text(encoding="utf-8")


def _tokens() -> dict:
    return home.parse_token_values(
        (REPO_ROOT / "docs" / "cis" / "tokens.css").read_text(encoding="utf-8")
    )


def _copy_tree(destination: Path) -> Path:
    docs = destination / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    shutil.copytree(HOME_DIR, docs / "home")
    shutil.copytree(CIS_DIR, docs / "cis")
    shutil.copy2(REPO_ROOT / "docs" / "cis-preview.html", docs / "cis-preview.html")
    return docs / "home"


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
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Unit tests
# --------------------------------------------------------------------------- #


def test_normalize_hex_expands_shorthand():
    assert home.normalize_hex("#ABC") == "#aabbcc"
    assert home.normalize_hex("#3D5AFE") == "#3d5afe"


def test_parse_token_values_reads_canonical_keys():
    tokens = _tokens()
    assert tokens["--cis-color-paper"] == "#f6f4ef"
    assert tokens["--cis-color-network"] == "#3d5afe"
    assert len(tokens) > 50


def test_parse_homepage_reads_title_meta_links_and_products():
    parsed = home.parse_homepage(
        '<!DOCTYPE html><html lang="zh-Hant"><head><title>T</title>'
        '<meta name="description" content="D">'
        '<meta property="og:title" content="O">'
        '<link rel="canonical" href="https://example.invalid/x/">'
        "</head><body><a href='#main'>skip</a>"
        "<main id='main'><article data-cis-product='forum'></article></main>"
        "</body></html>"
    )
    assert parsed.title == "T"
    assert parsed.meta_map()["description"] == "D"
    assert parsed.meta_map()["og:title"] == "O"
    assert parsed.anchors[0]["href"] == "#main"
    assert parsed.data_cis_products == ["forum"]
    assert "main" in parsed.ids


def test_parser_reports_unbalanced_tags():
    parsed = home.parse_homepage("<div><span></div>")
    assert parsed.structure_problems


def test_check_manifest_rejects_absolute_icon():
    tokens = _tokens()
    manifest = json.dumps(
        {
            "name": "n",
            "short_name": "s",
            "start_url": "./",
            "scope": "./",
            "display": "browser",
            "lang": "zh-Hant",
            "background_color": tokens["--cis-color-paper"],
            "theme_color": tokens["--cis-color-network"],
            "icons": [{"src": "https://example.invalid/i.svg", "sizes": "any"}],
        }
    )
    problems = []
    home.check_manifest(manifest, tokens, problems)
    assert any("relative path" in problem for problem in problems)


# --------------------------------------------------------------------------- #
# Repository smoke tests
# --------------------------------------------------------------------------- #


def test_checker_passes_on_repository():
    result = _run_default()
    assert result.returncode == 0, result.stderr
    assert "homepage: OK" in result.stdout


def test_repository_metadata_is_complete():
    parsed = home.parse_homepage(_index_text())
    meta = parsed.meta_map()
    assert parsed.html_lang == "zh-Hant"
    assert parsed.title
    assert meta.get("description")
    assert meta.get("og:title")
    assert meta.get("og:description")
    assert meta.get("canonical") is None  # canonical lives on the link element
    links = parsed.links_by_rel()
    assert links["canonical"] == [home.CANONICAL_URL]
    assert "width=device-width" in meta.get("viewport", "")
    assert links["icon"] == [home.FAVICON_LINK]
    assert links["manifest"] == [home.MANIFEST_LINK]


def test_repository_link_targets_are_real():
    parsed = home.parse_homepage(_index_text())
    hrefs = [anchor["href"] for anchor in parsed.anchors]
    assert home.FORUM_URL in hrefs
    assert home.GUILD_URL in hrefs
    assert home.PREVIEW_LINK in hrefs

    for anchor in parsed.anchors:
        target = anchor["href"]
        if target.startswith(("http://", "https://")):
            assert "noopener" in anchor["rel"].lower()
            label = (anchor["text"] + " " + anchor["aria_label"]).lower()
            assert "外部連結" in label
        elif target.startswith("#"):
            assert target[1:] in parsed.ids
        else:
            assert (HOME_DIR / target).resolve().is_file(), target


def test_repository_has_no_bare_hash_links():
    parsed = home.parse_homepage(_index_text())
    hrefs = [anchor["href"] for anchor in parsed.anchors]
    assert "#" not in hrefs
    assert hrefs[0] == "#main"


def test_repository_has_no_remote_assets():
    parsed = home.parse_homepage(_index_text())
    assert parsed.scripts == []
    assert parsed.images == []
    for link in parsed.links:
        rel = set(link.get("rel", "").lower().split())
        if rel & home.ASSET_LINK_RELS:
            assert not link.get("href", "").startswith(("http://", "https://"))

    styles = home.strip_css_comments(_styles_text())
    assert "@import" not in styles
    assert "url(" not in styles
    assert "http://" not in styles and "https://" not in styles

    manifest = _manifest_text()
    assert "http://" not in manifest and "https://" not in manifest


def test_repository_has_no_fake_claims_or_placeholders():
    parsed = home.parse_homepage(_index_text())
    problems = []
    home.check_no_claims(parsed, _index_text(), problems)
    assert problems == []


def test_repository_relative_paths_are_subpath_safe():
    parsed = home.parse_homepage(_index_text())
    links = parsed.links_by_rel()
    for rel in ("icon", "manifest", "stylesheet"):
        for href in links[rel]:
            assert not href.startswith(("/", "http://", "https://"))

    data = json.loads(_manifest_text())
    assert data["start_url"] == "./"
    assert data["scope"] == "./"
    for icon in data["icons"]:
        assert not icon["src"].startswith(("/", "http://", "https://", "data:"))
        assert ".." not in Path(icon["src"]).parts


def test_repository_uses_only_canonical_cis_tokens():
    tokens = _tokens()
    styles = home.strip_css_comments(_styles_text())
    referenced = set(home.VAR_REF_RE.findall(styles))
    assert referenced
    assert referenced <= set(tokens)
    assert not home.HEX_RE.search(styles)

    manifest = json.loads(_manifest_text())
    canonical_values = {value.lower() for value in tokens.values()}
    assert manifest["background_color"].lower() in canonical_values
    assert manifest["theme_color"].lower() in canonical_values


def test_repository_structure_has_landmarks_and_one_h1():
    parsed = home.parse_homepage(_index_text())
    assert parsed.has_doctype
    assert len(parsed.h1_texts) == 1
    assert not parsed.structure_problems
    for tag in ("header", "main", "footer", "nav"):
        assert tag in parsed.tag_names
    assert "main" in parsed.ids


# --------------------------------------------------------------------------- #
# Mutation tests on a temporary copy
# --------------------------------------------------------------------------- #


def test_temporary_copy_passes():
    with tempfile.TemporaryDirectory() as tmp:
        _copy_tree(Path(tmp))
        result = _run(Path(tmp))
        assert result.returncode == 0, result.stderr


def test_missing_favicon_fails():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _copy_tree(root)
        (root / "docs" / "home" / "favicon.svg").unlink()
        result = _run(root)
        assert result.returncode != 0
        assert "favicon.svg" in result.stderr


def test_unexpected_home_file_fails():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _copy_tree(root)
        (root / "docs" / "home" / "app.js").write_text("// not allowed\n", encoding="utf-8")
        result = _run(root)
        assert result.returncode != 0
        assert "unexpected file" in result.stderr


def test_remote_stylesheet_fails():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        target = _copy_tree(root) / "index.html"
        _replace_in(
            target,
            '<link rel="stylesheet" href="styles.css">',
            '<link rel="stylesheet" href="https://cdn.example.invalid/style.css">',
        )
        result = _run(root)
        assert result.returncode != 0
        assert "remote asset" in result.stderr


def test_bare_hash_link_fails():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        target = _copy_tree(root) / "index.html"
        _replace_in(target, 'href="#products"', 'href="#"')
        result = _run(root)
        assert result.returncode != 0
        assert "bare '#'" in result.stderr


def test_fake_revenue_claim_fails():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        target = _copy_tree(root) / "index.html"
        _replace_in(
            target,
            '<div class="page-intro">',
            '<div class="page-intro"><p>保證收益、月入十萬。</p>',
        )
        result = _run(root)
        assert result.returncode != 0
        assert "unsupported claim" in result.stderr


def test_raw_colour_in_styles_fails():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        target = _copy_tree(root) / "styles.css"
        _replace_in(target, "background: var(--cis-color-paper);", "background: #ff0000;")
        result = _run(root)
        assert result.returncode != 0
        assert "raw colour" in result.stderr


def test_second_token_set_fails():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        target = _copy_tree(root) / "styles.css"
        _replace_in(
            target,
            "body {",
            "body { --brand-color: #ff0000;\n  color: var(--brand-color);",
        )
        result = _run(root)
        assert result.returncode != 0
        assert "custom property" in result.stderr or "non-canonical" in result.stderr


def test_manifest_offline_claim_fails():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        target = _copy_tree(root) / "manifest.webmanifest"
        data = json.loads(target.read_text(encoding="utf-8"))
        data["display"] = "standalone"
        data["offline"] = True
        target.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        result = _run(root)
        assert result.returncode != 0
        assert "display must be" in result.stderr
        assert "offline" in result.stderr


def test_canonical_mismatch_fails():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        target = _copy_tree(root) / "index.html"
        _replace_in(
            target,
            home.CANONICAL_URL,
            "https://example.invalid/home/",
        )
        result = _run(root)
        assert result.returncode != 0
        assert "canonical" in result.stderr


def test_missing_og_description_fails():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        target = _copy_tree(root) / "index.html"
        _replace_in(
            target,
            '<meta property="og:description" content="',
            '<meta property="og:other" content="',
        )
        result = _run(root)
        assert result.returncode != 0
        assert "og:description" in result.stderr


def test_dead_fragment_link_fails():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        target = _copy_tree(root) / "index.html"
        _replace_in(target, 'href="#products"', 'href="#nowhere"')
        result = _run(root)
        assert result.returncode != 0
        assert "no matching element id" in result.stderr


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
