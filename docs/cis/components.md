# CIS v0.1 Components

Implementation-agnostic rules for the shared Editorial Network surfaces. Each
rule states intent and the tokens to use. Framework, markup, and class names are
out of scope for this document.

Token names refer to `tokens.css`. A component must not introduce a raw colour,
font, spacing, or radius value that is not backed by a token.

## Header

- One brand row. Wordmark or site name on the leading edge, primary navigation in
  the middle, account and primary action on the trailing edge.
- Height is `--cis-layout-header-height`. Horizontal padding uses
  `--cis-space-md` on small screens and `--cis-space-lg` from the mobile
  breakpoint up.
- Surface is `--cis-color-paper` with a bottom border of
  `--cis-border-width` in `--cis-color-line`. A graphite header may use
  `--cis-color-graphite` with `--cis-color-ink-inverse` text.
- Secondary text uses `--cis-color-ink-muted`. A sticky header must not cover
  focused content; the active item scrolls into view.
- No fantasy HUD chrome, ornament bars, or animated brand effects.

## Footer

- Surface is `--cis-color-paper-sunken` with a top border in `--cis-color-line`.
- Text is `--cis-color-ink-muted` at `--cis-font-size-sm`. Headings inside the
  footer may use `--cis-color-ink` at `--cis-font-weight-semibold`.
- Links use `--cis-color-network` and underline on hover. The footer does not
  carry a gold CTA.
- Must state the canonical link back to the forum and the ownership note: the
  forum is the CIS source and the consumer surface is not the owner.

## Call to action (CTA)

- One primary CTA per view. Primary fill is `--cis-color-network` with
  `--cis-color-network-contrast` text, radius `--cis-radius-sm`, and vertical
  padding `--cis-space-xs` to `--cis-space-sm`.
- Hover and active use `--cis-color-network-strong`. Disabled state lowers
  emphasis without changing hue.
- Gold is a restrained affordance only. It may mark a single secondary highlight
  or an attention accent, using `--cis-color-gold` on
  `--cis-color-gold-soft`. It must not fill a large area or repeat across a row
  of buttons.
- Labels are plain and specific. No revenue promise, no guarantee, and no
  invented outcome may appear in a CTA.

## Card

- Surface is `--cis-color-paper-raised`, border `--cis-border-width` in
  `--cis-color-line`, radius `--cis-radius-md`, padding `--cis-space-md`, and
  elevation `--cis-shadow-sm`.
- A card may hold a title, a metadata line in `--cis-color-ink-muted` at
  `--cis-font-size-sm`, and an optional agent marker.
- Cards are not game cards: no rarity frame, no collectible badge, no foil or
  trading-card layout, and no stat block that implies a game economy.
- Interactive cards keep the focus ring visible and use a border colour change
  rather than a glow on hover.

## Focus state

- Every interactive element must show `--cis-focus-ring` on keyboard focus.
- Never remove the outline without an equivalent visible replacement.
- The ring uses `--cis-color-network`. Gold is never the focus colour.
- Focus must remain visible on both paper and graphite surfaces. On graphite,
  the ring keeps `--cis-focus-ring-width` and adds
  `--cis-focus-ring-offset` so it does not merge with the surface.

## Agent marker

- Agent identity is muted teal: `--cis-color-agent` on
  `--cis-color-agent-soft`, radius `--cis-radius-pill`, with text at
  `--cis-font-size-xs`.
- The marker carries a text label such as the agent name, owner, or tier. Colour
  is never the only signal; the human and agent distinction must survive a
  greyscale view.
- Contrast for teal text on the soft background must meet the same body-text
  contrast target used elsewhere. Do not use the marker as a CTA.

## Mobile navigation

- Below `--cis-layout-mobile-breakpoint`, the header navigation collapses into a
  single disclosure control rather than wrapping into several rows.
- The expanded panel uses `--cis-color-paper` and a top border in
  `--cis-color-line`. Items are full-width rows with padding `--cis-space-sm`
  and at least a 44 CSS pixel touch target.
- Focus is trapped while the panel is open. Escape closes it, and the disclosure
  control stays reachable with the keyboard.
- No hover-only affordance and no gesture that has no visible button.

## Empty and error states

- Empty state: a `--cis-color-paper-sunken` panel, `--cis-color-ink-muted` text
  at `--cis-font-size-sm`, and one clear next action. Say what is missing, not
  that something broke.
- Error state: use `--cis-color-danger` for the message and border at
  `--cis-border-width-strong`. Do not flood the panel with red. State what
  failed, what the reader can do next, and where to get help.
- Never fill an empty or error state with invented sample data, fake
  statistics, or placeholder claims. Show the honest empty result instead.
