/**
 * palettes — the RAW color schemes the UI can wear.
 *
 * A {@link Palette} is the unopinionated layer: named slots (`bg0`, `green`, `grey1`, …) holding
 * pure hex, with no notion of *where* a color is used. The semantic-role mapping lives in
 * {@link ../theme/buildTheme.ts buildTheme} — components reference only roles, never these slots,
 * so adding a scheme is "register a palette + metadata". Every palette MUST expose the same slot
 * keys (enforced by the {@link Palette} type) so {@link buildTheme} works over any of them.
 *
 * Runtime catalog is fed from `tui.load_themes` → {@link applyThemeRecords}; everforest dark/light
 * are seeded at module load so tests and pre-connect boot always have a paintable default.
 */

/** Everforest Dark — hard background variant (canonical upstream hex). */
export const everforestDarkHard = {
  bgDim: '#1e2326',
  bg0: '#272e33',
  bg1: '#2e383c',
  bg2: '#374145',
  bg3: '#414b50',
  bg4: '#495156',
  bg5: '#4f5b58',
  bgVisual: '#4c3743',
  bgRed: '#493b40',
  bgGreen: '#3c4841',
  bgBlue: '#384b55',
  bgYellow: '#45443c',
  fg: '#d3c6aa',
  red: '#e67e80',
  orange: '#e69875',
  yellow: '#dbbc7f',
  green: '#a7c080',
  aqua: '#83c092',
  blue: '#7fbbb3',
  purple: '#d699b6',
  grey0: '#7a8478',
  grey1: '#859289',
  grey2: '#9da9a0',
} as const;

/** Everforest Light — hard background variant (canonical upstream light-hard hex). */
export const everforestLightHard = {
  bgDim: '#f2efdf',
  bg0: '#fffbef',
  bg1: '#f8f5e4',
  bg2: '#f2efdf',
  bg3: '#edeada',
  bg4: '#e8e5d5',
  bg5: '#bec5b2',
  bgVisual: '#f0f2d4',
  bgRed: '#fbe3da',
  bgGreen: '#f3f5d9',
  bgBlue: '#eaedf3',
  bgYellow: '#fbecd4',
  fg: '#5c6a72',
  red: '#f85552',
  orange: '#f57d26',
  yellow: '#dfa000',
  green: '#8da101',
  aqua: '#35a77c',
  blue: '#3a94c5',
  purple: '#df69ba',
  grey0: '#a6b0a0',
  grey1: '#939f91',
  grey2: '#829181',
} as const;

/** The shape every palette satisfies. Typed off the dark palette since both share identical keys. */
export type Palette = { readonly [K in keyof typeof everforestDarkHard]: string };

export type ThemeVariant = 'light' | 'dark';

export interface ThemeMeta {
  readonly name: string;
  readonly variant: ThemeVariant;
  readonly builtin: boolean;
}

/** One theme row from `themes.yaml` / `tui.load_themes`. */
export interface ThemeRecord {
  readonly id: string;
  readonly name: string;
  readonly variant: ThemeVariant;
  readonly builtin: boolean;
  readonly palette: Palette;
}

/** A scheme id usable by the settings menu / persisted config. */
export type ThemeId = string;

/** Default scheme when nothing is persisted or the id is unknown. */
export const DEFAULT_THEME_ID: ThemeId = 'everforest-dark';

const registry = new Map<string, { readonly palette: Palette; readonly meta: ThemeMeta }>();

function registerOne(id: string, palette: Palette, meta: ThemeMeta): void {
  registry.set(id, { palette, meta });
}

registerOne('everforest-dark', everforestDarkHard, {
  name: 'Everforest Dark',
  variant: 'dark',
  builtin: true,
});
registerOne('everforest-light', everforestLightHard, {
  name: 'Everforest Light',
  variant: 'light',
  builtin: true,
});

/** Merge server-loaded theme records into the in-memory registry (server wins per id). */
export function applyThemeRecords(records: readonly ThemeRecord[]): void {
  for (const rec of records) {
    registerOne(rec.id, rec.palette, {
      name: rec.name,
      variant: rec.variant,
      builtin: rec.builtin,
    });
  }
}

/** Drop a theme id from the registry (tests only — production removes via reload). */
export function clearThemeRegistryForTests(): void {
  registry.clear();
  registerOne('everforest-dark', everforestDarkHard, {
    name: 'Everforest Dark',
    variant: 'dark',
    builtin: true,
  });
  registerOne('everforest-light', everforestLightHard, {
    name: 'Everforest Light',
    variant: 'light',
    builtin: true,
  });
}

export function getPalette(id: string): Palette | undefined {
  return registry.get(id)?.palette;
}

export function getThemeMeta(id: string): ThemeMeta | undefined {
  return registry.get(id)?.meta;
}

export function hasTheme(id: string): boolean {
  return registry.has(id);
}

export function listThemeIds(): readonly ThemeId[] {
  return [...registry.keys()].sort();
}

export function listThemeRecords(): readonly ThemeRecord[] {
  return listThemeIds().map((id) => {
    const entry = registry.get(id);
    if (entry === undefined) {
      throw new Error(`missing registry entry for ${id}`);
    }
    return {
      id,
      name: entry.meta.name,
      variant: entry.meta.variant,
      builtin: entry.meta.builtin,
      palette: entry.palette,
    };
  });
}

/**
 * @deprecated Prefer {@link getPalette} / {@link listThemeIds}. Kept for gradual migration.
 * Returns only currently registered palettes (dynamic after `applyThemeRecords`).
 */
export function getPalettesMap(): Readonly<Record<string, Palette>> {
  const out: Record<string, Palette> = {};
  for (const id of listThemeIds()) {
    const palette = getPalette(id);
    if (palette !== undefined) {
      out[id] = palette;
    }
  }
  return out;
}
