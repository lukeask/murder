/**
 * `parseWorkflowFire` — pure parse of a leading `:name <remainder>` chat buffer into a workflow
 * firing intent, run on the chat buffer between span-expansion and template expansion (Chunk E).
 *
 * ## Precedence: builtin > workflow > template > literal
 *
 * A leading `:name` is contested by three consumers. The locked order is:
 *  1. **builtin** (`:help`, `:save`, …) — handled later by `dispatchCommand`; we must NOT fire over it,
 *     so a builtin name returns `null` here and the buffer flows on untouched.
 *  2. **workflow** (a SAVED workflow name) — fires here, short-circuiting the chat send entirely.
 *  3. **template** (`expandTemplates`) — runs only if we returned `null`.
 *  4. **literal** — an unknown `:foo` falls through everything and is sent verbatim.
 * This is WHY the fire check runs before `expandTemplates`: a name that is both a saved workflow and a
 * template must fire (a workflow is the heavier, intent-bearing action), and a builtin must still beat
 * a same-named workflow. Ordering the checks here keeps that contract in one readable place.
 *
 * ## The `{input}` arg convention (locked, v0)
 *
 * The remainder after `:name` (trimmed) becomes a SINGLE arg under the conventional key `input` —
 * workflow stage instructions reference `{input}`. Named `key=value` args are deferred to a later
 * version; v0 takes the whole tail as one blob so `:wf fix the login bug` just works.
 */

/** A leading bare name: `:name` followed by whitespace or end-of-string. Captures `name`. Mirrors
 * `expandTemplates`'s `LEADING_RE` so the three consumers agree on what "a leading `:name`" is — in
 * particular `:wf:` (double colon) is NOT a leading match (the `:` is not whitespace/EOS), so inline
 * `:name:` stays templates' domain. */
const LEADING_RE = /^:([A-Za-z0-9_-]+)(?=\s|$)/;

/**
 * Parse a leading `:name <remainder>`. Returns the workflow firing intent IFF `name` is NOT a builtin
 * command and IS a saved workflow; otherwise `null` (let the buffer flow on to template expansion /
 * literal). See the file header for the full precedence rule and the `{input}` convention.
 *
 * @param message       the chat buffer (already image-span-expanded).
 * @param builtins      the dispatcher's builtin command names — a leading `:builtin` returns null.
 * @param workflowNames the saved workflow names — a leading `:name` not in here returns null.
 */
export function parseWorkflowFire(
  message: string,
  builtins: ReadonlySet<string>,
  workflowNames: ReadonlySet<string>,
): { name: string; args: Record<string, string> } | null {
  const leading = LEADING_RE.exec(message);
  if (leading === null) {
    return null; // not a leading `:name` (or it's `:name:` inline — templates' domain).
  }
  const name = leading[1] as string;
  if (builtins.has(name)) {
    return null; // builtin wins — dispatchCommand handles it.
  }
  if (!workflowNames.has(name)) {
    return null; // not a saved workflow — fall through to template expansion / literal.
  }
  // The remainder after `:name` becomes the single `{input}` arg (v0 convention). Empty tail → no args.
  const remainder = message.slice(leading[0].length).trim();
  const args: Record<string, string> = remainder === '' ? {} : { input: remainder };
  return { name, args };
}
