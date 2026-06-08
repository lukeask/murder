# src/input

Input & focus backbone: `focusStore` (state machine, derived candidate set, re-home
invariant), `panelStore` (toggle set), keymap-as-data, and the single root `useInput`
dispatcher. No `check_action`-style central gating — panels **declare** their keymaps (rule 5).
