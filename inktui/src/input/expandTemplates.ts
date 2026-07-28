/**
 * Prompt-template expansion helpers — pure text passes for `:name:` macros.
 *
 * ## Chat entrypoint: {@link expandTemplates}
 *
 * Run on the chat buffer between span-expansion and the prefix dispatcher. Contains chat-specific
 * policy (builtin shadowing, leading `:foo args` form, single-pass). Prefer the lower-level helpers
 * below from the workflow editor / compile path — do not call `expandTemplates` there.
 *
 * ## Lower-level operations (workflow-safe)
 *
 * - {@link expandInlinePromptTemplates} — unambiguous inline `:foo:` only (single pass, no recursion).
 * - {@link parseLeadingTemplateInvocation} — leading `:name args…` with positional placeholder fill.
 *
 * `parseLeadingWorkflowInvocation` is deferred: workflow leading-fire already lives in
 * `parseWorkflowFire` (`fireWorkflow.ts`) with the v0 `{input}` remainder convention.
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
 *
 * Unknown inline `:name:` tokens are left verbatim for chat. Workflow compile will treat unknowns as
 * errors later; {@link ExpansionResult.missing} surfaces those names for that path.
 */

/** A leading bare name: `:name` followed by whitespace or end-of-string. Captures `name`. */
const LEADING_RE = /^:([A-Za-z0-9_-]+)(?=\s|$)/;
/** An inline macro: `:name:`. Global so we can sweep every occurrence. */
const INLINE_RE = /:([A-Za-z0-9_-]+):/g;
/** A `{placeholder}` token inside a template body. */
const PLACEHOLDER_RE = /\{([A-Za-z0-9_-]+)\}/g;

/** Result of an inline `:name:` expansion pass. */
export type ExpansionResult = {
  /** Text after single-pass inline expansion (unknown `:name:` left verbatim). */
  text: string;
  /** Distinct template names that appeared as `:name:` but were not in the map (first-seen order). */
  missing: string[];
  /** Distinct template names successfully expanded (first-seen order). */
  expanded: string[];
};

/** A leading `:name args…` parse that resolved to a template in the map. */
export type LeadingTemplateInvocation = {
  /** Matched template name. */
  name: string;
  /** Whitespace-separated args after `:name`. */
  args: readonly string[];
  /** Template body with positional `{placeholder}` fill applied. */
  text: string;
};

/** Shared low-level match: leading `:name` plus the remainder after the matched prefix. */
export type LeadingColonName = {
  name: string;
  /** Slice after `:name` (may include leading whitespace). */
  remainder: string;
};

/**
 * Match a leading `:name` (whitespace or EOS after the name). Returns null when the text does not
 * start with that form — including inline `:name:` (the trailing `:` is neither whitespace nor EOS).
 */
export function parseLeadingColonName(text: string): LeadingColonName | null {
  const leading = LEADING_RE.exec(text);
  if (leading === null) return null;
  const name = leading[1] as string;
  return { name, remainder: text.slice(leading[0].length) };
}

/**
 * Fill a template body's `{placeholder}` tokens positionally from `args`. The Nth distinct placeholder
 * (first-appearance order) gets `args[N]`; unfilled placeholders are left verbatim; extra args ignored.
 *
 * Chat-oriented helper used by leading template invocation; workflow inputs use a different
 * substitution path.
 */
export function fillPlaceholders(body: string, args: readonly string[]): string {
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
 * Expand unambiguous inline `:name:` macros in `text` (single pass, no recursion).
 *
 * Unknown `:name:` tokens are left verbatim and listed in `missing`. Chat sends those literally;
 * workflow compile will treat unknowns as errors later using the same list.
 *
 * Does not interpret leading `:name args` syntax.
 */
export function expandInlinePromptTemplates(
  text: string,
  templates: ReadonlyMap<string, string>,
): ExpansionResult {
  const missing: string[] = [];
  const expanded: string[] = [];
  const seenMissing = new Set<string>();
  const seenExpanded = new Set<string>();

  const result = text.replace(INLINE_RE, (whole, name: string) => {
    const body = templates.get(name);
    if (body === undefined) {
      if (!seenMissing.has(name)) {
        seenMissing.add(name);
        missing.push(name);
      }
      return whole;
    }
    if (!seenExpanded.has(name)) {
      seenExpanded.add(name);
      expanded.push(name);
    }
    return body;
  });

  return { text: result, missing, expanded };
}

/**
 * Parse a leading `:name args…` as a template invocation.
 *
 * Returns null when the text is not leading form, or when `name` is not in `templates`.
 * Does NOT consult builtins — callers that need chat builtin shadowing must check that themselves
 * (see {@link expandTemplates}).
 *
 * On a hit, returns the filled body in `text` WITHOUT an inline re-scan (single-pass rule).
 */
export function parseLeadingTemplateInvocation(
  text: string,
  templates: ReadonlyMap<string, string>,
): LeadingTemplateInvocation | null {
  const leading = parseLeadingColonName(text);
  if (leading === null) return null;
  const body = templates.get(leading.name);
  if (body === undefined) return null;
  const args = leading.remainder.split(/\s+/).filter((tok) => tok.length > 0);
  return {
    name: leading.name,
    args,
    text: fillPlaceholders(body, args),
  };
}

/**
 * Expand template macros in `message`. See the file header for the full precedence rule.
 *
 * Chat-only entrypoint: applies builtin shadowing and leading-vs-inline precedence. Workflow
 * nodes should use {@link expandInlinePromptTemplates} instead.
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
  const leading = parseLeadingColonName(message);
  if (leading !== null) {
    if (builtins.has(leading.name)) {
      // Builtin wins — leave untouched for dispatchCommand.
      return message;
    }
    const invocation = parseLeadingTemplateInvocation(message, registry);
    if (invocation !== null) {
      // Single pass: return the filled body WITHOUT an inline re-scan.
      return invocation.text;
    }
    // Unknown leading `:name` — fall through untouched (sent verbatim / dispatched literally).
    return message;
  }

  // 2. Inline form (only reached when leading expansion did not fire).
  return expandInlinePromptTemplates(message, registry).text;
}
