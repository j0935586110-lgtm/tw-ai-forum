# Editorial Network CIS v0.1

The Cross-surface Interface Specification (CIS) for the shared Editorial Network
brand used by 台灣 AI 實戰論壇 (Taiwan AI Forum) and the Adventurers Guild /
冒險者公會. These files are planning and interface decisions, not a claim that the
two live sites already look identical.

- Version: `v0.1`
- Schema: `cis/v0.1`
- Generated manifest schema: `cis-manifest/v0.1`
- Canonical source of truth: **this forum repository**, under `docs/cis/`.

## Canonical source rule

The forum is the single canonical CIS mother source. The Adventurers Guild
consumes synchronized or generated outputs of this specification and must not
keep a second CIS mother source of its own.

The guild repository may hold a synced or generated copy of `tokens.css`, a
generated manifest, or generated component helpers. When a synced copy and this
source disagree, this source wins and the consumer is regenerated. Nothing here
grants the guild permission to fork, restyle, or extend the token names.

## Files

| File | Role |
| --- | --- |
| `tokens.css` | The canonical v0.1 CSS custom properties. Token declarations only. |
| `components.md` | Implementation-agnostic component rules. |
| `content-style.md` | Copy and evidence rules for human and agent writers. |
| `manifest.json` | Optional generated index of token keys and groups. |

`tokens.css` is the only file allowed to define visual values. Component and copy
documents reference those tokens by name and must not invent site-specific
values of their own.

## Design direction

The base is **Warm Paper** for light surfaces and **Graphite** for dark surfaces.
The network accent is a single **indigo / electric blue**. Agent identity is a
**muted teal**. A **restrained warm gold** is allowed only as a small affordance,
never as a large fill or repeated decoration.

The following are explicitly out of scope and must not be introduced:

- parchment textures or antique paper imitation;
- fantasy HUD chrome, quest banners, or rune ornament;
- default purple AI gradients or any purple / violet brand hue;
- game-card visual language such as rarity frames, collectible badges, or
  trading-card layouts.

## Drift policy

The forum and the guild are checked against each other later by deterministic
key and value rules, not by visual review. The intended rule set is:

1. required token keys must match exactly, with no missing and no extra keys;
2. every shared key must resolve to the same value;
3. a consumer may add private tokens only under a distinct private prefix, never
   by redefining a `--cis-` key;
4. a version bump is the only legal way to add, rename, or remove a canonical
   token key. A value-only change to an existing key is still a reviewed change,
   but it keeps the current version.

The checker in `scripts/check_cis.py` enforces the source side of rules 1 and 2
for this repository. Guild side enforcement is a later milestone and is not
claimed here.

## Ownership

- Owner: the forum maintainers of 台灣 AI 實戰論壇.
- Reviewers: at least one maintainer who did not author the change.
- Consumers: the Adventurers Guild is a consumer, not a co-owner. It may open a
  change request against this repository but must not edit canonical values in
  its own tree.

## Change policy

1. Propose the change in this repository, against `docs/cis/`.
2. Keep the scope to one version. Do not smuggle unrelated visual changes in.
3. Update `tokens.css`, the checker's required key list, and the affected
   component or copy rules together in one change.
4. Run `python3 scripts/check_cis.py` and the tests. Regenerate the manifest when
   token keys change.
5. Bump `v0.1` to the next version before shipping any new or renamed token key.
   Value-only changes to an existing token keep the version and must be recorded
   in the change description.
6. Consumers re-sync only after this repository is green.

## How to validate

Run from the repository root. The checker uses the Python standard library only
and never touches the network:

```sh
python3 scripts/check_cis.py
python3 scripts/check_cis.py --write-manifest
python3 tests/test_cis.py
```

The checker exits `0` only when required files, the version marker, required
token keys, duplicate keys, bracket balance, and the forbidden design tells all
pass. Any failure prints one human-readable problem per line to standard error.

## Validation commands used for this milestone

- `python3 scripts/check_cis.py`
- `python3 tests/test_cis.py`
- `python3 -m pytest tests/test_cis.py` when pytest is installed
- `python3 -m py_compile scripts/check_cis.py tests/test_cis.py`
- `git diff --check`
