/** Resolve the current project/repo name for NavBar branding (TUI `MURDER_PROJECT` parity). */

export interface ProjectNameSources {
  /** Prefer this when set (settings/service `project` from roles.yaml). */
  readonly fromStore?: string | null;
  readonly env?: Record<string, string | undefined>;
}

/** Vite / launcher inject; empty or unset → null (brand shows `murder` alone).
 * Store/service project wins over env when present. */
export function resolveProjectName(
  envOrSources:
    | Record<string, string | undefined>
    | ProjectNameSources = import.meta.env as Record<string, string | undefined>,
): string | null {
  const sources: ProjectNameSources =
    envOrSources !== null &&
    typeof envOrSources === 'object' &&
    ('fromStore' in envOrSources || 'env' in envOrSources)
      ? (envOrSources as ProjectNameSources)
      : { env: envOrSources as Record<string, string | undefined> };

  const fromStore = sources.fromStore;
  if (typeof fromStore === 'string') {
    const trimmed = fromStore.trim();
    if (trimmed !== '') return trimmed;
  }

  const env = sources.env ?? (import.meta.env as Record<string, string | undefined>);
  const raw = env['VITE_MURDER_PROJECT'] ?? env['MURDER_PROJECT'];
  if (typeof raw !== 'string') return null;
  const trimmed = raw.trim();
  return trimmed === '' ? null : trimmed;
}
