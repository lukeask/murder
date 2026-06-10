# src/input

Input & focus backbone: `focusStore` (state machine, derived candidate set, re-home
invariant), `panelStore` (toggle set), keymap-as-data, and the single root `useInput`
dispatcher. No `check_action`-style central gating — panels **declare** their keymaps (rule 5).
`modeStore` (C7M) adds the transient-mode stack — the dispatcher's layer 0: a mode captures
input exclusively and restores prior focus on exit. Build a modal/editor/full-screen surface by
declaring a `Mode` (`keymap` + `onIntent` + `presentation` + `render`) and calling `enter()`;
the `<Overlay>` (in `components/`) paints it. See `modeStore.ts` and `components/ConfirmModal.tsx`
(the reference) — copy that, don't hand-roll input capture or focus juggling.
