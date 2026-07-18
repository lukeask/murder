# Styling guide (webui)

The #1 goal: **restyle the whole app by editing CSS, never TSX.** There is no CSS-in-JS and no
inline thematic style objects. Every visual decision lives in plain CSS driven by CSS custom
properties. This doc says where each kind of change goes.

## The two layers of variables

### 1. Colors — `--color-*` (theme-driven, runtime)
These come from the active **semantic theme** and are written onto `:root` at runtime by
`useThemeCssVars()` (`src/theme/useThemeCssVars.ts` → `src/theme/cssVars.ts`). The full contract is
documented with fallback values in **`src/styles/theme.css`**.

- Each theme role (`text`, `focus`, `rowSelectedBg`, `gaugeHigh`, …) becomes a kebab var:
  `rowSelectedBg` → `--color-row-selected-bg`.
- Components/CSS reference them only as `var(--color-…)`. **Never hard-code a hex in a component or
  in app.css.**
- To **re-theme** (change the palette): switch the theme in the Settings panel, or call
  `setTheme(id)` from `@core/theme/themeStore`. New palettes are added in `@core/theme/palettes.ts`
  (shared with the Ink TUI) — add a role there and one mapping line is enough; the bridge is
  mechanical.
- The available roles: `brand text muted focus border-blurred title-blurred row-selected-bg
  row-alt-bg panel-header-bg panel-selected-bg error warning success heading accent active inactive
  gauge-normal gauge-high gauge-track gauge-label-text`.

### 2. Layout tokens — `--space-*`, `--radius*`, `--font-*`, dimensions (not theme-driven)
Defined once in **`src/styles/theme.css`** under "Non-thematic layout tokens". Change these to alter
the app's rhythm globally without touching any component:

| Token | Controls |
| --- | --- |
| `--space-1 … --space-5` | the spacing scale (padding/gaps everywhere) |
| `--radius`, `--radius-sm` | corner rounding |
| `--font-mono`, `--font-sans` | typefaces |
| `--rail-width` | desktop left/right rail column width |
| `--header-height`, `--tabbar-height` | chrome heights |
| `--tap-min` | minimum touch-target size (mobile thumb-friendliness) |
| `--font-input-mobile` | text-field font-size on mobile (≥16px so iOS Safari doesn't zoom on focus) |
| `--safe-top` / `--safe-bottom` / `--safe-left` / `--safe-right` | `env(safe-area-inset-*)` values (notch / home indicator), `0px` fallback; pad edge-anchored chrome (header, tab bar) with these |
| `--bp-mobile` | **documentation** mirror of the mobile breakpoint |

## File organization

- **`src/styles/theme.css`** — the variable contract: `--color-*` fallbacks + all layout tokens +
  the base reset (`box-sizing`, body font/colors). Start here for global changes.
- **`src/styles/app.css`** — all structural + per-component styling, organised top-to-bottom:
  shell → connection pill → rails/stage → Panel chrome → list rows → per-domain panel styles
  (roster, tickets, docs, history, usage, transit, settings) → Stage (chat/tmux/doc/ticket) →
  mobile tab bar → responsive `@media`. Every rule references the variables above.

## Where to change common things

- **A color** → `src/styles/theme.css` (a theme role fallback) or the palette in
  `@core/theme/palettes.ts` (the real source for all themes).
- **Spacing / radius / fonts** → the layout tokens in `src/styles/theme.css`.
- **One panel's look** (e.g. the usage gauges, the chat bubbles) → its section in `app.css`
  (sections are labelled with `── name ──` banners).
- **The breakpoint** → it lives in TWO places that must agree: the `@media (max-width: 768px)` query
  at the bottom of `app.css` AND `MOBILE_QUERY` in `src/useMediaQuery.ts` (CSS media queries can't
  read a CSS var, so the literal is duplicated; `--bp-mobile` documents it).

## Responsive model

- **Desktop (> 768px):** `[ left rail | Stage | right rail ]` — three regions, each rail an
  independently-scrolling column of panels (`.app__body--desktop`, `.rail`).
- **Mobile (≤ 768px):** a single panel at a time chosen by a bottom tab bar
  (`.app__body--mobile`, `.tabbar`). This is a genuinely different DOM tree, so the switch is made in
  JS (`useMediaQuery(MOBILE_QUERY)` → `data-layout` on `.app`); everything else is CSS.
- Touch targets use `--tap-min` (44px) on rows, inputs and buttons; there are no hover-only
  affordances (hover only enhances, never hides function).

## The tmux terminal view

`TmuxFrameView` renders raw ANSI snapshots from the application protocol's terminal stream via the lightweight
`ansi-to-html` converter into a `<pre class="tmux__frame">` (styled with the mono font on a black
ground). We chose `ansi-to-html` over `xterm.js` because the frames are full-screen *snapshot*
strings (`tmux capture-pane -e`), not an incremental PTY byte stream — xterm wants a live stream and
explicit geometry, and is ~10× heavier for no gain here.
