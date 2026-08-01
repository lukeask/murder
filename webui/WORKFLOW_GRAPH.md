# Web workflow graph editor — browser-first brief

The TUI editor (`WorkflowTemplateEditorMode` + cell-surface paint) is the **domain reference**, not the interaction model to port. The browser should steal the best of n8n-style canvases: click, drag, pan, zoom, handle-to-handle connect.

## Split of ownership

| Layer | Owner | Notes |
|-------|--------|------|
| Document model (`EditorWorkflow`, stages, gates, dependsOn) | `ui-core/workflowEditor/model` | Unchanged |
| Edits / undo (`applyWorkflowEdit`, legality) | `ui-core/workflowEditor/reducer` | Map UI gestures → these edits |
| Validate / compile / wire / run launch | `ui-core/workflowEditor/*` + workflows actions | Shared with TUI |
| Character-grid layout (`NODE_WIDTH=24`, `layoutWorkflow` cell metrics) | TUI only | Do **not** drive the web canvas |
| Paint / keymap / inspector chrome | TUI only | Web rebuilds in DOM/CSS |
| Canvas interaction | **WebUI** | Pointer graph library + Murder chrome |

## Interaction target (n8n-shaped)

Must-have:

- **Pan / zoom** canvas (wheel, trackpad, space-drag or middle-drag)
- **Drag nodes** to rearrange (view positions; see persistence note below)
- **Connect by drag** from output handle → input handle → `dependsOn` edit via reducer (`dependencyLegality` before commit)
- **Click node** → docked inspector (harness, model, worktree, gate, title, instructions) — same fields as TUI stage panel
- **Add / delete / duplicate stage** from canvas chrome + inspector
- **Auto-layout** action (ELK/dagre or a web port of rank layout) as a *command*, not the only layout
- **Minimap** optional but cheap with React Flow
- **Keyboard**: delete selected, undo/redo if history is local, Esc cancel connect — without requiring TUI chord literacy

Nice-to-have (use the browser):

- Multi-select + align
- Edge click to remove dependency
- Search/jump to stage (TUI has search — keep it)
- Run-status overlay on nodes when viewing a live run (`runState` / `statusDisplay` roles)
- Smooth edge routing (bezier / smoothstep), not ASCII orthogals

## Library recommendation

Prefer **`@xyflow/react` (React Flow)** unless a strong reason not to:

- Mature drag/connect/pan/zoom/minimap
- Custom node/edge components styled to Murder aesthetic (`webui/AESTHETIC.md`)
- Fits Vite/React 19

Do **not** invent a bespoke SVG drag system on day one. Do **not** wrap the TUI `paintWorkflow` cell grid in a `<pre>`.

## Persistence of positions

`EditorWorkflow` currently has **no node coordinates** — layout is derived. For v1 web editor:

1. Keep **structure** in ui-core (dependsOn is source of truth).
2. Keep **positions** in web-local React Flow state, seeded by an auto-layout pass on open / on structural change.
3. Optional later: persist `{ x, y }` hints in template metadata if the wire format gains a place for them — do not block the canvas on that.

Connecting edges must always round-trip through `applyWorkflowEdit` so compile/validate stay honest.

## Aesthetic

Nodes should feel like **stage blocks in a material system** (stone/earth chrome, decisive status accent), not pastel n8n clones or generic blue SaaS nodes. Large silhouette: canvas mass + slim inspector rail. Roughness only where it binds (e.g. edge stubs for illegal deps), not as decoration.

## Out of scope for the canvas itself

- Launch review wizard (separate mode/dialog; reuse compile + `workflows.run`)
- Prompt template manager
- Replacing TUI editor

## Acceptance sketch

A user can open a template, drag two stages apart, drag a new dependency edge, edit instructions in the inspector, save via `workflows.put`, and run via launch review — without touching the keyboard except optional shortcuts.
