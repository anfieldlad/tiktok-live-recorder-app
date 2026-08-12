# Web UI redesign — "Ledger"

Date: 2026-08-12
Status: approved, ready to implement

## Goal

Replace the visual language of the web UI without changing what the app does.
Every feature, route, and API response stays exactly as it is. Nothing in this
document requires a backend change.

The present UI is competent and anonymous: Inter loaded from Google Fonts, a
two-column split panel, cards on a warm background, accent colour swapped per
platform. It looks like every other self-hosted dashboard.

## Concept

**A register of things that were broadcast once.** The app captures live
streams and posts that vanish — a catalogue, not a dashboard. So: cream card
stock on a ruled board, an oxblood margin rule, entries numbered, statuses
applied as rubber stamps.

The concept earns its details. Numbered entries because a register numbers
things. Stamps because status in a paper system is a stamp. A retention line
that reads "Taken at 13:07 — the register clears it in 22h" because that is
what the retention model actually does, said in the register's voice.

Reference mockup, built and reviewed before this spec:
`scratchpad/directions/ledger.html` (not committed — the spec is the record).

## Typography

No sans-serif anywhere. That single decision does most of the work.

| Role | Face | Notes |
|---|---|---|
| Headings, subjects, numerals | **Fraunces** | Variable. `opsz` 100–144, weight 300–400, `SOFT` 30–40. The elegance comes from the axes, not from bolding. |
| Prose, messages, ledes | **Newsreader** | Variable text serif, `opsz` 6–72. |
| Labels, data, buttons, timestamps | **Cutive Mono** | Uppercase and tracked (0.12–0.20em) for labels and buttons. |

Loaded from Google Fonts with `preconnect`, replacing the current Inter link —
same mechanism, same number of requests. `display=swap`.

## Colour

Tokens replace the current `:root` block wholesale. Names that the existing CSS
already uses (`--ink`, `--muted`, `--line`, `--danger`) keep their meaning so
unported rules degrade rather than break.

```css
:root{
  --board:#ded6c4; --board-2:#e6dfd0;      /* ruled board behind everything */
  --card:#fbf8f0; --card-edge:#efe8da;     /* card stock */
  --ink:#221d17; --ink-soft:#4a4238; --dim:#7d7365;
  --rule:#cec4ad; --rule-soft:#e0d8c6;
  --ink-2:#a8342a;                         /* series ink — TikTok, oxblood */
  --filed:#3f6b4a; --pending:#8a6a1f; --failed:#8f2c22;
}
body[data-app="instagram"]{ --ink-2:#2f4a7f; }   /* series ink — process blue */
```

**Two series, one press.** Instagram is not a theme, it is a second ink. Only
`--ink-2` changes, and it drives the margin rule, the brand mark, the active
tab underline and focus rings. The stock, type and layout are identical. This
replaces the current approach of swapping accents and hue-rotating the logo.

## Background

Three layers, no decorative gradients:

1. A repeating 32px horizontal rule (`repeating-linear-gradient`) — the board.
2. A radial wash from `--board-2` to `--board`, plus a top shadow for depth.
3. Paper grain: an inline SVG `feTurbulence` data URI at `opacity:.5`,
   `mix-blend-mode:multiply`, `position:fixed`, `pointer-events:none`.

## Structure

One centred 660px column on every page. No split panels, no second layout for
mobile — verified at 375px in the mockup.

```
masthead      mark · title · series/est · index tabs · session state
sheet         the page's form (entry)
filed-head    "Filed this session" + rule
card ×N       newest first
footer        beta note · contact · storage figure
```

Per page, only the sheet's contents differ:

| Page | Sheet | Cards |
|---|---|---|
| `record.html` | source + duration, Begin capture | recording jobs |
| `watch.html` | source + duration, File under auto-record | watch jobs |
| `download.html` | post URL, Save post | download result |
| `instagram_download.html` | post URL, Save post | download result + zip |

The session panel (`_session_panel.html`, `_ig_session_panel.html`) becomes a
drawer opened from the masthead's session state, keeping its current ids and
behaviour.

## Components

- **masthead** — brand mark (ring + dot in series ink), title in Fraunces,
  `Series TT · Est. 2026`, index tabs (file-folder tabs, active tab sits on the
  card stock with an inset series-ink underline), session state at far right.
- **sheet** — the form. Card stock, 3px series-ink margin rule on the left,
  eyebrow in Cutive Mono, `h1` in Fraunces, lede in Newsreader.
- **field** — label in tracked Cutive Mono caps; input is borderless with a
  1.5px bottom rule that turns series ink on focus. No boxes.
- **btn** — Cutive Mono caps, tracked. Primary is solid ink; `btn-quiet`
  (mapping to today's `btn-secondary`) is outlined in `--rule`.
- **card** — a filed entry: `No. 0418 · 12 Aug 2026, 17:44`, subject in
  Fraunces, message in Newsreader, a 4-column `meta` grid in Cutive Mono, and
  actions. A live capture gets `.live`, which pulses its margin rule.
- **stamp** — replaces `status-pill`. Bordered, rotated −4°, in the status
  colour: Filed / Recording / Watching / Failed / Stopped.
- **empty** — dashed rule, no stock, "No entries filed yet".
- **retention line** — dashed top rule, Cutive Mono, shown when `fetched_at` is
  set: "Taken at 13:07 — the register clears it in 22h".

## Motion

One orchestrated page load, then near-silence:

- Masthead → sheet → cards rise 12px and fade, delays 40/100/180/260/340ms,
  `cubic-bezier(.2,.7,.25,1)`, 600ms.
- Stamps land: rotate −14°→−4°, scale 1.5→1, 500ms overshoot.
- A live card pulses its margin rule, 2.4s ease-in-out — no blinking dot.
- Buttons lift 1px on hover with a shadow.

Every animation is disabled under `prefers-reduced-motion: reduce`.

## Constraint: the JS writes markup

Four scripts build HTML strings with hardcoded class names. These must move in
lockstep with the CSS or pages render unstyled:

```
btn btn-danger btn-primary btn-secondary btn-warn elapsed-counter empty
empty-icon empty-title file-list file-meta file-name file-path file-row good
job-actions job-card job-header job-id job-message job-stats job-title live
row soft status-pill wide
```

Approach: **keep every class name that survives conceptually** (`job-card`,
`file-row`, `job-stats`, `btn`) and restyle it; rename only where the concept
changes (`status-pill` → `stamp`, `btn-secondary` → `btn-quiet`), updating the
emitting script in the same commit. Element ids are untouched — all 44 of them
keep their names, so event wiring and `getElementById` lookups are unaffected.

Cache-busting `?v=` on every changed asset, or the browser serves the old file.

## Accessibility

- `--ink` on `--card` is ~14:1; `--dim` on `--card` ~4.8:1, used only for
  secondary text at ≥12px.
- Stamp colour is never the only signal — the stamp's text says the status.
- Focus: 2px series-ink outline at 2px offset on every interactive element.
- Tabs use `aria-current="page"`; the session drawer keeps its existing
  `aria-expanded` wiring.
- Motion respects `prefers-reduced-motion`.

## Verification

Visual, because that is what changed:

1. Every page at 1440px and 375px, screenshotted in the browser and compared
   against the mockup.
2. Both series (`data-app` unset and `instagram`) on a page that has cards.
3. Card states exercised with seeded data: recording, filed, failed, empty,
   and a fetched card showing its retention line.
4. `prefers-reduced-motion` emulated — nothing animates.
5. The existing Python suite must stay green. One test asserts on rendered
   markup, so the redesign has two literal obligations:
   `test_instagram_page_renders_with_session_panel` requires the string
   **"Instagram session"** to appear on `/instagram`, and a link whose href is
   exactly **`/instagram`**. Rewording that heading or turning the link into a
   button breaks the suite — change the test deliberately if the copy must
   change, rather than discovering it in CI.

## Decisions taken

- **Single column, not two.** With 0–2 entries typical, a split panel spends
  half the viewport on emptiness.
- **Light, not dark.** The concept is paper. A dark ledger would be a different
  concept wearing this one's clothes.
- **No Bahasa.** Right texture for the riso direction that lost; mixing
  registers here would muddy it.

## Out of scope

- Any behaviour, route, payload or feature change.
- Renaming the app. The mockup showed "The Ephemera Register" and it fits the
  concept, but the spec keeps **TikTok Media Saver** because the rename was not
  agreed. It is one string in `base.html` plus the `<title>`, changeable later.
- The Android client, which shares nothing with these templates.
- Dark mode.
