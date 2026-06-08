# src/selectors

View-models: `useMemo`-shaped pure functions that turn a store slice into render-ready data —
sort, truncate-to-width, parent-indent, column tuples. Presentation lives here, never in the
store (rule 2), so the store stays reusable by a future React-DOM client.
