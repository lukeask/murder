/** Resolve the current project/repo name for NavBar branding (TUI `MURDER_PROJECT` parity). */

/** Vite / launcher inject; empty or unset → null (brand shows `murder` alone). */
export function resolveProjectName(
  env: Record<string, string | undefined> = import.meta.env as Record<string, string | undefined>,
): string | null {
  const raw = env['VITE_MURDER_PROJECT'] ?? env['MURDER_PROJECT'];
  if (typeof raw !== 'string') return null;
  const trimmed = raw.trim();
  return trimmed === '' ? null : trimmed;
}
