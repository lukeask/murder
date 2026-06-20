/**
 * `expandTemplates` — pure text-expansion pass for `:name:` macros, run on the chat buffer between
 * span-expansion and the prefix dispatcher (Workstream: templates).
 *
 * ## Two forms, single pass, no recursion
 *
 * An expanded body is NEVER re-scanned — a template body that itself contains `:foo:` is left as-is.
 * This is deliberate: single-pass expansion can't loop, so a template referencing itself (directly or
 * via a cycle) is impossible to hang the input loop on.
 *
 * 1. **Leading parameterized form** — when the message STARTS with `:name` (name matches
 *    `^[A-Za-z0-9_-]+$`) immediately followed by whitespace or end-of-string:
 *      - `name` ∈ builtins → message untouched (the builtin `:command` wins; `dispatchCommand` runs it).
 *      - `name` ∈ registry → the body is filled positionally: the Nth DISTINCT `{placeholder}` (in order
 *        of first appearance in the body) takes the Nth whitespace-separated arg after the name. Unfilled
 *        placeholders stay verbatim; extra args are ignored. The whole `:name args…` prefix is REPLACED
 *        by the filled body, and the result is returned WITHOUT an inline re-scan (precedence rule).
 *      - else (unknown) → untouched (falls through `dispatchCommand` literally, sent verbatim).
 * 2. **Inline form** — only when leading expansion did NOT fire: every `:name:` (double-colon delimited,
 *    `name` matches `[A-Za-z0-9_-]+`) is replaced by its registry body, or left verbatim on a miss
 *    (literal fallthrough). Inline form is templates-only — it never consults builtins.
 */

/** A leading bare name: `:name` followed by whitespace or end-of-string. Captures `name`. */
const LEADING_RE = /^:([A-Za-z0-9_-]+)(?=\s|$)/;
/** An inline macro: `:name:`. Global so we can sweep every occurrence. */
const INLINE_RE = /:([A-Za-z0-9_-]+):/g;
/** A `{placeholder}` token inside a template body. */
const PLACEHOLDER_RE = /\{([A-Za-z0-9_-]+)\}/g;

/**
 * Fill a template body's `{placeholder}` tokens positionally from `args`. The Nth distinct placeholder
 * (first-appearance order) gets `args[N]`; unfilled placeholders are left verbatim; extra args ignored.
 */
function fillPlaceholders(body: string, args: readonly string[]): string {
  // Map each distinct placeholder name → its positional index (first appearance order).
  const order = new Map<string, number>();
  let seen = 0;
  for (const match of body.matchAll(PLACEHOLDER_RE)) {
    const phName = match[1] as string;
    if (!order.has(phName)) {
      order.set(phName, seen);
      seen += 1;
    }
  }
  return body.replace(PLACEHOLDER_RE, (whole, phName: string) => {
    const idx = order.get(phName);
    if (idx === undefined || idx >= args.length) return whole;
    return args[idx] as string;
  });
}

/**
 * Expand template macros in `message`. See the file header for the full precedence rule.
 *
 * @param message  the chat buffer (already image-span-expanded).
 * @param registry template name → body. Built caller-side from `selectTemplatesByName`.
 * @param builtins the dispatcher's builtin command names — a leading `:builtin` is left untouched.
 */
export function expandTemplates(
  message: string,
  registry: ReadonlyMap<string, string>,
  builtins: ReadonlySet<string>,
): string {
  // 1. Leading parameterized form.
  const leading = LEADING_RE.exec(message);
  if (leading !== null) {
    const name = leading[1] as string;
    if (builtins.has(name)) {
      // Builtin wins — leave untouched for dispatchCommand.
      return message;
    }
    const body = registry.get(name);
    if (body !== undefined) {
      // Args = whitespace-separated tokens after `:name`. The matched prefix is `:name`; the remainder
      // (leading whitespace and all) is the arg source.
      const remainder = message.slice(leading[0].length);
      const args = remainder.split(/\s+/).filter((tok) => tok.length > 0);
      // Single pass: return the filled body WITHOUT an inline re-scan.
      return fillPlaceholders(body, args);
    }
    // Unknown leading `:name` — fall through untouched (sent verbatim / dispatched literally).
    return message;
  }

  // 2. Inline form (only reached when leading expansion did not fire).
  return message.replace(INLINE_RE, (whole, name: string) => {
    const body = registry.get(name);
    return body === undefined ? whole : body;
  });
}
