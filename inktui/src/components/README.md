# src/components

Ink components: pure functions of a slice, `React.memo` + a narrow selector as the standard
(rule 1). Local UI state (cursor/scroll/expanded) via `useState`. Zero bus knowledge —
components read selectors and dispatch store actions, never call `ApplicationClient`.
