# WebUI aesthetic brief (provisional)

Corrected taste model for Murder’s browser cockpit. Use this when restyling CSS/tokens.
Do **not** begin with generic SaaS polish and sprinkle personality afterward.

## Central rule (corrected)

**Earned complexity** is a strong organizing principle, not the sole root.

Prefer a slightly sharper hierarchy:

1. **Macro coherence first** — the layout must read as one stable system at a distance (Scarpa’s masses, Bass’s silhouette). If the large-scale shape fails, detail cannot rescue it.
2. **Earned complexity** — local irregularity, texture, and ornament are welcome when they clarify hierarchy, bind materials, or emerge from real constraints. Complexity that does not earn its keep should be removed.
3. **Personality in the governing system** — type, geometry, spacing rhythm, and interaction timing carry the character. Decorative garnish is a failure mode.

“Earned complexity” alone understates how often you reject work that is *locally* interesting but *globally* weak (Klimt palette, Noguchi cube, Venturi awkwardness). Lead with equilibrium; let complexity arrive as consequence.

## What works

- Stable composition with internal irregularity (Fallingwater, Bernini diagonal, Scarpa apertures, Bass silhouette).
- Strong constraints that visibly produce decisions (Judd material variation, Müller-Brockmann grid, Bass reduced shapes with rough execution). Mere reduction without decision (Farnsworth/Linear blankness) is dull, not refined.
- Material/textural contrast that clarifies structure (masonry vs stone, polish vs bark, rough edges that unify).
- Integrated personality (Aesop warmth through the whole system; Casa Gilardi geometry+color as structure).
- Legible large-scale shapes before detail credit.

## What fails

- Asymmetry as deliberate awkwardness (Vanna Venturi).
- Half-committed expressive devices (The Verge).
- Ornament or texture as the entire idea (*Unknown Pleasures* linework alone).
- Generic professional polish with personality bolted on (Linear).
- Palettes that veto otherwise-good structure (Klimt *Kiss*; weak/infantile light blue; bile yellow-greens; indecisive “almost accent” concrete greys; pastels that soften without atmosphere).

## Color model (corrected)

Color is high-leverage and can veto. Preferences lean **earth / stone / wood / cream / amber / vegetation**, plus **dark neutrals with deliberate contrast**, and **bold color when structurally placed**.

Corrections:

- Do **not** treat Everforest-as-shipped as automatically “your” palette. It is a competent vegetation-adjacent system, but default dark-green terminal chrome can read as *generic developer UI* if typography, geometry, and material contrast stay sterile.
- Prefer colors with a **clear job**: ground, mass, accent, warning, brand. Multiple border/trim colors without compositional role are noise.
- Warmth is not mandatory everywhere; cold stone/concrete can work if contrast and material reading stay decisive. The aversion is to **indecisive** neutrals (neither ground nor accent).
- Accents should be sparse and committed (one primary accent family, maybe one semantic status set)—not a rainbow of equally loud chips.
- Avoid: infantile light blue; mold/bile yellow-green combos; pastel softening; glass-on-white SaaS sterility.

Murder brand red (`#e67e80` / coral-rose) can remain a decisive accent if it participates in hierarchy (focus, error, brand mark)—not as decorative sprinkles.

## Translation to this WebUI

Current shell (`webui/`) is a three-rail cockpit with Everforest tokens, system/JetBrains Mono, and design-system components. Move toward:

| Keep | Change |
|------|--------|
| Stable 3-region macro-grid (rails + stage) | Stronger large-scale silhouette: denser stage mass, quieter rails, clearer header bar as a single horizontal beam |
| Semantic `--color-*` theme bridge | Ground palette warmer/stonier; reduce competing accent treatments in DS chips/badges |
| Mono for working surfaces (terminal, keybinds, ids) | Pair with one expressive display face for brand/titles—not Inter/Roboto/system for chrome |
| Functional panels | Remove card-stack monotony: fewer identical bordered boxes; let stage be the dominant plane; rails as recessed masonry |
| Sparse keybind bar | Make it structurally integrated (ledger/footer), not a floating hint strip |

Avoid: excessive radius/glass, pill clusters, multi-layer shadows, purple glow, white-on-wood sterility, ornamental stickers, half-maximalist gimmicks.

### Concrete CSS targets

- `webui/src/styles/theme.css`, `tokens.css` — palette, type, radius, spacing rhythm
- `webui/src/styles/cockpit.css` — macro composition (one violation of the grid is allowed if it earns hierarchy)
- `webui/src/styles/ds*.css`, `panels*.css`, `app.css` — reduce card chrome; integrate texture only where it binds structure

### Constraint that should produce variety

Keep the cockpit grid strict. Variation comes from: panel density, stage vs rail contrast, type scale, one irregular header/brand treatment, and material contrast between terminal black mass and warm stone chrome—not from inventing new layout paradigms per panel.
