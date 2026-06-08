# src/hooks

Reusable React hooks that bind components to stores/selectors (e.g. typed `useSlice`,
measurement helpers). Hooks may use React + store APIs; they must not import `BusClient` or
any terminal-only API that would leak below the component layer.
