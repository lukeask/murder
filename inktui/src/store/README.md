# src/store

Zustand slices and their **actions**. Rule 3 governs **writes**: actions are the only view->bus
command path — the sole caller of `BusClient` for RPC. High-rate read **streams** may subscribe to
the bus directly (via `useBusClient`); the only such case today is tmux frames (`TmuxMode`), kept
out of the store on purpose. Framework- and transport-agnostic: no Ink/terminal/socket types here
(rule 4). Domain state only; presentation belongs in `selectors/` (rule 2).
