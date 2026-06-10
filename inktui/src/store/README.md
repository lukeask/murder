# src/store

Zustand slices and their **actions**. Actions are the only view->bus path — the sole caller of
`BusClient` (rule 3). Framework- and transport-agnostic: no Ink/terminal/socket types here
(rule 4). Domain state only; presentation belongs in `selectors/` (rule 2).
