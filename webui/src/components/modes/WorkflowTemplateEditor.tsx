/**
 * Browser-first workflow template graph editor (Wave B3).
 *
 * Domain: ui-core workflowEditor model/reducer/validate/wire.
 * Interaction: React Flow pan/zoom, drag nodes, handle→handle connect → dependsOn edits.
 * Positions are local (not persisted). TUI cell-surface paint is intentionally not ported.
 */

import {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  SelectionMode,
  useEdgesState,
  useNodesState,
  useReactFlow,
  type Connection,
  type Edge,
  type Node,
  type OnConnect,
  type OnEdgesChange,
  type OnNodesChange,
  type OnSelectionChangeParams,
} from '@xyflow/react';
import { useAppStore, useAppStoreApi } from '@murder/ui-core/hooks/useAppStore.js';
import { useApplicationClient } from '@murder/ui-core/hooks/useApplicationClient.js';
import { decodeStaticDagStatuses } from '@murder/ui-core/workflowEditor/runState.js';
import {
  modelsFor,
  STATIC_HARNESS_MODELS,
  createHarnessModelsActions,
  type HarnessModel,
} from '@murder/ui-core/store/dialogs/harnessModelsActions.js';
import {
  buildWorktreeOptions,
  createWorktreeOptionsActions,
  type WorktreeOption,
} from '@murder/ui-core/store/dialogs/worktreeOptionsActions.js';
import type { WorkflowTemplate } from '@murder/ui-core/store/workflows/workflowsSlice.js';
import type {
  EditableField,
  EditorIssue,
  EditorWorkflow,
  StageKey,
} from '@murder/ui-core/workflowEditor/model.js';
import { workflowEqual } from '@murder/ui-core/workflowEditor/model.js';
import {
  dependencyLegality,
  initialEditorState,
  reduceEditor,
  type EditorState,
  type WorkflowEdit,
} from '@murder/ui-core/workflowEditor/reducer.js';
import { validateEditorWorkflow } from '@murder/ui-core/workflowEditor/validate.js';
import { fromWire, toWire } from '@murder/ui-core/workflowEditor/wire.js';
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type MouseEvent as ReactMouseEvent,
} from 'react';
import { Button, cx } from '../ds/index.js';
import { WORKFLOW_EDITOR_HINTS, publishModeHints } from '../../keybindModeHints.js';
import { DependencyEdge } from '../workflowEditor/DependencyEdge.js';
import {
  DEPENDENCY_EDGE_TYPE,
  STAGE_NODE_TYPE,
  parseDependencyEdgeId,
  workflowToFlow,
  type DependencyFlowEdge,
  type StageFlowNode,
} from '../workflowEditor/flowGraph.js';
import { mergePositions, type NodePosition } from '../workflowEditor/layout.js';
import { editorIssueFromServer } from '../workflowEditor/serverIssues.js';
import { StageInspector } from '../workflowEditor/StageInspector.js';
import { StageNode } from '../workflowEditor/StageNode.js';
import '@xyflow/react/dist/style.css';
import '../../styles/workflow-editor.css';

const EMPTY_WORKTREES = buildWorktreeOptions([]);

const nodeTypes = { [STAGE_NODE_TYPE]: StageNode };
const edgeTypes = { [DEPENDENCY_EDGE_TYPE]: DependencyEdge };

function blankWorkflow(): EditorWorkflow {
  return { name: '', description: '', mode: 'static', stages: [] };
}

export type WorkflowTemplateEditorProps = {
  /**
   * When set, load that registry template as an existing edit (overwrite identity).
   * When omitted (and no `initialDraft`), open a blank create draft.
   */
  readonly templateName?: string;
  /**
   * Detached copy/new draft already shaped as a wire template. Saving creates a new record
   * (`originalName` stays null). Takes precedence over `templateName` when both are set.
   */
  readonly initialDraft?: WorkflowTemplate;
  readonly onClose: () => void;
  /** Open launch review / fire path; parent owns the wizard surface. */
  readonly onLaunch?: (workflow: EditorWorkflow) => void;
  /** Optional escape hatch to a future template library surface. */
  readonly onOpenLibrary?: () => void;
};

/**
 * Full-viewport workflow template editor.
 *
 * Prop contract (library wiring):
 * - `templateName` — edit existing (overwrite identity)
 * - `initialDraft` — copy/new draft (no overwrite identity); prefer over templateName
 * - omit both — blank create
 * - `onClose` — dismiss overlay
 * - `onLaunch?.(draft)` — after local validate (parent may save/compile/run)
 * - `onOpenLibrary?.()` — jump to library when it exists
 *
 * Library Edit → `{ templateName }`; New → `{}`; Copy → `{ initialDraft: copiedWire }`.
 */
export function WorkflowTemplateEditor(props: WorkflowTemplateEditorProps): React.JSX.Element {
  return (
    <ReactFlowProvider>
      <WorkflowTemplateEditorInner {...props} />
    </ReactFlowProvider>
  );
}

function WorkflowTemplateEditorInner({
  templateName,
  initialDraft,
  onClose,
  onLaunch,
  onOpenLibrary,
}: WorkflowTemplateEditorProps): React.JSX.Element {
  const bus = useApplicationClient();
  const storeApi = useAppStoreApi();
  const { fitView, getNode } = useReactFlow();

  const [editor, setEditor] = useState<EditorState>(() => initialEditorState(blankWorkflow()));
  const [originalName, setOriginalName] = useState<string | null>(null);
  const [positions, setPositions] = useState<Map<StageKey, NodePosition>>(() => new Map());
  const [serverIssues, setServerIssues] = useState<readonly EditorIssue[]>([]);
  const [status, setStatus] = useState<'idle' | 'saving' | 'error'>('idle');
  const [feedback, setFeedback] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [harnessModels, setHarnessModels] =
    useState<Record<string, readonly HarnessModel[]>>(STATIC_HARNESS_MODELS);
  const [worktrees, setWorktrees] = useState<readonly WorktreeOption[]>(EMPTY_WORKTREES);
  /** TUI `/` search — filter stages by id/title and jump on Enter. */
  const [stageSearch, setStageSearch] = useState<string | null>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);

  const positionsRef = useRef(positions);
  positionsRef.current = positions;
  const editorRef = useRef(editor);
  editorRef.current = editor;

  useEffect(() => publishModeHints(WORKFLOW_EDITOR_HINTS), []);

  // Load template + option lists once on mount / source change.
  useEffect(() => {
    let cancelled = false;
    setReady(false);
    void (async () => {
      const actions = storeApi.getState().actions.workflows;
      if (storeApi.getState().workflows.status !== 'ready') {
        await actions.load();
      }
      if (cancelled) return;
      const items = storeApi.getState().workflows.items;
      let initial = blankWorkflow();
      let orig: string | null = null;
      if (initialDraft !== undefined) {
        initial = fromWire(initialDraft);
        orig = null;
      } else if (templateName !== undefined && templateName !== '') {
        const found = items.find((w) => w.name === templateName);
        if (found !== undefined) {
          initial = fromWire(found);
          orig = found.name;
        } else {
          setFeedback(`Template “${templateName}” not found — starting blank.`);
        }
      }
      const state = initialEditorState(initial);
      setEditor(state);
      setOriginalName(orig);
      setPositions(mergePositions(state.draft, new Map(), { relayout: true }));
      setServerIssues([]);
      setStatus('idle');
      setReady(true);
    })();
    return () => {
      cancelled = true;
    };
  }, [templateName, initialDraft, storeApi]);

  useEffect(() => {
    let cancelled = false;
    void createHarnessModelsActions(bus)
      .fetch()
      .then((map) => {
        if (!cancelled) setHarnessModels(map);
      });
    void createWorktreeOptionsActions(bus)
      .fetch()
      .then((opts) => {
        if (!cancelled) setWorktrees(opts);
      });
    return () => {
      cancelled = true;
    };
  }, [bus]);

  const issues = useMemo(() => {
    const local = validateEditorWorkflow(editor.draft);
    return serverIssues.length === 0 ? local : [...local, ...serverIssues];
  }, [editor.draft, serverIssues]);

  const activeRun = useAppStore((s) => s.workflowRuns.activeRun);
  const matchingRun =
    activeRun !== null &&
    (activeRun.definition_name === editor.draft.name ||
      activeRun.definition_name === originalName)
      ? activeRun
      : null;
  /** TUI parity: while a matching run is live, paint the immutable definition_snapshot, not the editable draft. */
  const runSnapshot = useMemo((): EditorWorkflow | null => {
    if (matchingRun === null) return null;
    const snap = matchingRun.definition_snapshot;
    if (
      typeof snap !== 'object' ||
      snap === null ||
      typeof (snap as { readonly name?: unknown }).name !== 'string'
    ) {
      return null;
    }
    try {
      return fromWire(snap as WorkflowTemplate);
    } catch {
      return null;
    }
  }, [matchingRun]);
  const frozen = runSnapshot !== null;
  const displayWorkflow = runSnapshot ?? editor.draft;
  const stageStatuses = useMemo(
    () => (matchingRun === null ? undefined : decodeStaticDagStatuses(matchingRun.state)),
    [matchingRun],
  );

  const flow = useMemo(
    () =>
      workflowToFlow(displayWorkflow, positions, {
        selected: frozen
          ? (displayWorkflow.stages.find(
              (s) =>
                s.id ===
                editor.draft.stages.find((d) => d.key === editor.selected)?.id,
            )?.key ??
            displayWorkflow.stages[0]?.key ??
            null)
          : editor.selected,
        extraIssues: frozen ? [] : serverIssues,
        ...(stageStatuses === undefined ? {} : { stageStatuses }),
      }),
    [displayWorkflow, editor.draft.stages, editor.selected, frozen, positions, serverIssues, stageStatuses],
  );

  const [nodes, setNodes, onNodesChangeBase] = useNodesState<StageFlowNode>([]);
  const [edges, setEdges, onEdgesChangeBase] = useEdgesState<DependencyFlowEdge>([]);

  // Project domain → RF whenever draft / positions / selection change.
  useEffect(() => {
    setNodes(flow.nodes);
    setEdges(flow.edges);
  }, [flow, setNodes, setEdges]);

  useEffect(() => {
    if (!ready) return;
    const id = requestAnimationFrame(() => {
      void fitView({ padding: 0.18, duration: 200 });
    });
    return () => cancelAnimationFrame(id);
  }, [ready, fitView]);

  const dispatchEdit = useCallback((edit: WorkflowEdit, selected?: StageKey | null) => {
    setEditor((prev) =>
      reduceEditor(
        prev,
        selected === undefined
          ? { type: 'edit', edit }
          : { type: 'edit', edit, selected },
      ),
    );
    setServerIssues([]);
    setFeedback(null);
    setStatus('idle');
  }, []);

  // After structural edits, merge positions for new/removed stages (not every field edit).
  const stageKeySig = editor.draft.stages.map((s) => s.key).join('\0');
  useEffect(() => {
    setPositions((prev) => mergePositions(editorRef.current.draft, prev));
  }, [stageKeySig]);

  const onNodesChange: OnNodesChange<StageFlowNode> = useCallback(
    (changes) => {
      if (frozen) return;
      onNodesChangeBase(changes);
      // Persist drag positions into local map (structure stays in EditorWorkflow).
      let moved = false;
      const next = new Map(positionsRef.current);
      for (const change of changes) {
        if (change.type === 'position' && change.position !== undefined) {
          next.set(change.id, { x: change.position.x, y: change.position.y });
          moved = true;
        }
      }
      if (moved) setPositions(next);
    },
    [frozen, onNodesChangeBase],
  );

  const onEdgesChange: OnEdgesChange<DependencyFlowEdge> = useCallback(
    (changes) => {
      if (frozen) return;
      const removals = changes.filter((c) => c.type === 'remove');
      const rest = changes.filter((c) => c.type !== 'remove');
      if (rest.length > 0) onEdgesChangeBase(rest);
      // Structural deletes go through the reducer so compile/validate stay honest.
      for (const change of removals) {
        const parsed = parseDependencyEdgeId(change.id);
        if (parsed === null) continue;
        const legality = dependencyLegality(editorRef.current.draft, parsed.target, parsed.source);
        if (legality === 'remove') {
          dispatchEdit({
            type: 'toggle-dependency',
            target: parsed.target,
            source: parsed.source,
          });
        }
      }
    },
    [dispatchEdit, frozen, onEdgesChangeBase],
  );

  const isValidConnection = useCallback((connection: Connection | Edge) => {
    const source = connection.source;
    const target = connection.target;
    if (source == null || target == null || source === target) return false;
    return dependencyLegality(editorRef.current.draft, target, source) === 'add';
  }, []);

  const onConnect: OnConnect = useCallback(
    (connection) => {
      if (frozen) return;
      const source = connection.source;
      const target = connection.target;
      if (source == null || target == null) return;
      if (dependencyLegality(editorRef.current.draft, target, source) !== 'add') return;
      dispatchEdit({ type: 'toggle-dependency', target, source });
    },
    [dispatchEdit, frozen],
  );

  const onSelectionChange = useCallback((params: OnSelectionChangeParams) => {
    const node = params.nodes[0];
    const key = node?.id ?? null;
    setEditor((prev) => (prev.selected === key ? prev : { ...prev, selected: key }));
  }, []);

  const onNodeClick = useCallback((_event: ReactMouseEvent, node: Node) => {
    setEditor((prev) => ({ ...prev, selected: node.id }));
  }, []);

  const syncModelForHarness = useCallback(
    (key: StageKey, harness: string) => {
      const models = modelsFor(harness, harnessModels);
      const stage = editorRef.current.draft.stages.find((s) => s.key === key);
      if (stage === undefined) return;
      if (models.length === 0) {
        if (stage.model !== null && stage.model !== '') {
          dispatchEdit({ type: 'set-field', key, field: 'model', value: '' });
        }
        return;
      }
      if (stage.model !== null && models.some((m) => m.id === stage.model)) return;
      dispatchEdit({ type: 'set-field', key, field: 'model', value: models[0]?.id ?? '' });
    },
    [dispatchEdit, harnessModels],
  );

  const onStageField = useCallback(
    (key: StageKey, field: EditableField, value: string) => {
      dispatchEdit({ type: 'set-field', key, field, value });
      if (field === 'harness') syncModelForHarness(key, value);
    },
    [dispatchEdit, syncModelForHarness],
  );

  const onWorkflowField = useCallback(
    (field: 'name' | 'description' | 'mode', value: string) => {
      dispatchEdit({ type: 'set-field', key: 'workflow', field, value });
    },
    [dispatchEdit],
  );

  const onAddStage = useCallback(() => {
    setEditor((prev) => {
      const after = prev.selected;
      const next = reduceEditor(prev, { type: 'edit', edit: { type: 'add-stage', after } });
      const added = next.draft.stages.find(
        (stage) => !prev.draft.stages.some((s) => s.key === stage.key),
      );
      setServerIssues([]);
      setFeedback(null);
      setStatus('idle');
      return { ...next, selected: added?.key ?? next.selected };
    });
  }, []);

  const onDeleteStage = useCallback(
    (key: StageKey) => {
      dispatchEdit({ type: 'delete-stage', key }, null);
    },
    [dispatchEdit],
  );

  const onAutoLayout = useCallback(() => {
    setPositions(mergePositions(editorRef.current.draft, positionsRef.current, { relayout: true }));
    requestAnimationFrame(() => {
      void fitView({ padding: 0.18, duration: 200 });
    });
  }, [fitView]);

  const focusStage = useCallback(
    (key: StageKey) => {
      setEditor((prev) => ({ ...prev, selected: key }));
      requestAnimationFrame(() => {
        const node = getNode(key);
        if (node !== undefined) {
          void fitView({ nodes: [node], padding: 0.4, duration: 200 });
        }
      });
    },
    [fitView, getNode],
  );

  const stageSearchMatches = useMemo(() => {
    if (stageSearch === null) return [];
    const q = stageSearch.toLowerCase();
    return editor.draft.stages.filter((stage) =>
      `${stage.id} ${stage.title}`.toLowerCase().includes(q),
    );
  }, [editor.draft.stages, stageSearch]);

  const commitStageSearch = useCallback(() => {
    const match = stageSearchMatches[0];
    if (match !== undefined) {
      focusStage(match.key);
    }
    setStageSearch(null);
  }, [focusStage, stageSearchMatches]);

  const onUndo = useCallback(() => {
    setEditor((prev) => reduceEditor(prev, { type: 'undo' }));
  }, []);
  const onRedo = useCallback(() => {
    setEditor((prev) => reduceEditor(prev, { type: 'redo' }));
  }, []);

  const dirty = !workflowEqual(editor.base, editor.draft);
  const errorCount = issues.filter((i) => i.severity === 'error').length;
  const warningCount = issues.length - errorCount;

  const save = useCallback(
    async (after?: (workflow: WorkflowTemplate) => void): Promise<boolean> => {
      const local = validateEditorWorkflow(editorRef.current.draft);
      const blocking = local.find((i) => i.severity === 'error');
      if (blocking !== undefined) {
        setServerIssues([]);
        setFeedback(blocking.message);
        setStatus('error');
        if (blocking.stageKey !== undefined) {
          setEditor((prev) => ({ ...prev, selected: blocking.stageKey ?? prev.selected }));
        }
        return false;
      }
      setStatus('saving');
      setFeedback(null);
      try {
        const result = await storeApi
          .getState()
          .actions.workflows.put(toWire(editorRef.current.draft), originalName);
        if (!result.ok || result.workflow == null) {
          setStatus('error');
          const mapped = (result.issues ?? []).map((issue) =>
            editorIssueFromServer(editorRef.current.draft, issue),
          );
          setServerIssues(mapped);
          setFeedback(mapped[0]?.message ?? 'Unable to save workflow.');
          return false;
        }
        const canonical = fromWire(result.workflow);
        setEditor((prev) => {
          const prevId = prev.draft.stages.find((s) => s.key === prev.selected)?.id;
          return {
            ...prev,
            base: canonical,
            draft: canonical,
            selected:
              (prevId === undefined
                ? undefined
                : canonical.stages.find((s) => s.id === prevId)?.key) ??
              canonical.stages[0]?.key ??
              null,
            undo: [],
            redo: [],
          };
        });
        // Keys refresh on fromWire — reseeds positions.
        setPositions(mergePositions(canonical, new Map(), { relayout: true }));
        setOriginalName(canonical.name);
        setServerIssues([]);
        setStatus('idle');
        setFeedback(null);
        after?.(result.workflow);
        return true;
      } catch (error: unknown) {
        setStatus('error');
        const message = error instanceof Error ? error.message : String(error);
        setFeedback(message);
        return false;
      }
    },
    [originalName, storeApi],
  );

  const onSave = useCallback(() => {
    void save();
  }, [save]);

  const onLaunchClick = useCallback(() => {
    if (onLaunch === undefined || frozen) return;
    const local = validateEditorWorkflow(editorRef.current.draft);
    const blocking = local.find(
      (i) =>
        i.severity === 'error' || i.code === 'unsupported_mode' || i.code === 'unsupported_gate',
    );
    if (blocking !== undefined) {
      setFeedback(blocking.message);
      setStatus('error');
      if (blocking.stageKey !== undefined) {
        setEditor((prev) => ({ ...prev, selected: blocking.stageKey ?? prev.selected }));
      }
      return;
    }
    if (dirty) {
      void save((saved) => onLaunch(fromWire(saved)));
      return;
    }
    onLaunch(editorRef.current.draft);
  }, [dirty, frozen, onLaunch, save]);

  // Focus the search field when `/` opens search mode.
  useEffect(() => {
    if (stageSearch === null) return;
    searchInputRef.current?.focus();
  }, [stageSearch]);

  // Keyboard: `/` stage search, Delete selected stage/edge, undo/redo, Esc.
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      const target = e.target;
      const inField =
        target instanceof HTMLInputElement ||
        target instanceof HTMLTextAreaElement ||
        target instanceof HTMLSelectElement;

      // Search mode owns Escape / Enter on the search field (handled by the input).
      if (stageSearch !== null && inField && target === searchInputRef.current) {
        return;
      }

      if (inField) return;

      const mod = e.metaKey || e.ctrlKey;

      // TUI `/` — open stage search (not while frozen).
      if (!mod && !e.altKey && e.key === '/' && stageSearch === null && !frozen) {
        e.preventDefault();
        setStageSearch('');
        return;
      }

      if (mod && e.key === 'z' && !e.shiftKey) {
        if (frozen) return;
        e.preventDefault();
        onUndo();
        return;
      }
      if (mod && (e.key === 'y' || (e.key === 'z' && e.shiftKey))) {
        if (frozen) return;
        e.preventDefault();
        onRedo();
        return;
      }
      if (mod && e.key === 's') {
        if (frozen) return;
        e.preventDefault();
        void save();
        return;
      }
      // TUI `R` — save & run (Shift+r without command modifier).
      if (!mod && !e.altKey && e.key === 'R') {
        if (onLaunch === undefined || frozen) return;
        e.preventDefault();
        onLaunchClick();
        return;
      }
      if (frozen) return;
      if (e.key === 'Delete' || e.key === 'Backspace') {
        const selectedEdges = edges.filter((edge) => edge.selected);
        if (selectedEdges.length > 0) {
          e.preventDefault();
          for (const edge of selectedEdges) {
            const parsed = parseDependencyEdgeId(edge.id);
            if (parsed === null) continue;
            if (dependencyLegality(editorRef.current.draft, parsed.target, parsed.source) === 'remove') {
              dispatchEdit({
                type: 'toggle-dependency',
                target: parsed.target,
                source: parsed.source,
              });
            }
          }
          return;
        }
        if (editorRef.current.selected !== null) {
          e.preventDefault();
          onDeleteStage(editorRef.current.selected);
        }
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [
    dispatchEdit,
    edges,
    frozen,
    onDeleteStage,
    onLaunch,
    onLaunchClick,
    onRedo,
    onUndo,
    save,
    stageSearch,
  ]);

  return (
    <div
      className={cx('wfe', frozen && 'wfe--frozen')}
      role="dialog"
      aria-modal="true"
      aria-label="Workflow template editor"
    >
      <header className="wfe-toolbar">
        <div className="wfe-toolbar__brand">
          <span className="wfe-toolbar__mark">murder</span>
          <span className="wfe-toolbar__title">
            {displayWorkflow.name || '(unnamed)'}
            {dirty && !frozen ? ' ·' : ''}
          </span>
          {frozen ? (
            <span className="wfe-toolbar__status wfe-toolbar__status--run">
              run · definition snapshot
            </span>
          ) : null}
          {status === 'saving' ? <span className="wfe-toolbar__status">saving…</span> : null}
        </div>
        <div className="wfe-toolbar__actions">
          {onOpenLibrary !== undefined ? (
            <Button size="sm" variant="ghost" onClick={onOpenLibrary} disabled={frozen}>
              Library
            </Button>
          ) : null}
          <Button
            size="sm"
            variant="ghost"
            onClick={onUndo}
            disabled={frozen || editor.undo.length === 0}
          >
            Undo
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={onRedo}
            disabled={frozen || editor.redo.length === 0}
          >
            Redo
          </Button>
          <Button size="sm" variant="secondary" onClick={onAutoLayout} disabled={frozen}>
            Auto-layout
          </Button>
          <Button size="sm" variant="ghost" onClick={onAddStage} disabled={frozen}>
            + Stage
          </Button>
          <Button
            size="sm"
            variant="primary"
            onClick={onSave}
            disabled={frozen || status === 'saving'}
          >
            Save
          </Button>
          {onLaunch !== undefined ? (
            <Button
              size="sm"
              variant="brand"
              onClick={onLaunchClick}
              disabled={frozen || status === 'saving'}
              title="Save & run (Shift+R)"
            >
              Save & run
            </Button>
          ) : null}
          <Button size="sm" variant="ghost" onClick={onClose}>
            Close
          </Button>
        </div>
      </header>

      {frozen ? (
        <div className="wfe-run-banner" role="status">
          Active run — graph shows the frozen definition snapshot (edits paused).
        </div>
      ) : null}

      <div className="wfe-body">
        <div className="wfe-canvas">
          {ready ? (
            <ReactFlow
              nodes={nodes}
              edges={edges}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              onConnect={onConnect}
              isValidConnection={frozen ? () => false : isValidConnection}
              onSelectionChange={onSelectionChange}
              onNodeClick={onNodeClick}
              nodesDraggable={!frozen}
              nodesConnectable={!frozen}
              elementsSelectable={!frozen}
              nodeTypes={nodeTypes}
              edgeTypes={edgeTypes}
              fitView
              selectionMode={SelectionMode.Partial}
              deleteKeyCode={null}
              multiSelectionKeyCode="Shift"
              panOnScroll
              panOnDrag={[1, 2]}
              selectionOnDrag={!frozen}
              zoomOnScroll
              minZoom={0.25}
              maxZoom={2}
              proOptions={{ hideAttribution: true }}
              defaultEdgeOptions={{
                type: DEPENDENCY_EDGE_TYPE,
                animated: false,
              }}
            >
              <Background
                variant={BackgroundVariant.Dots}
                gap={18}
                size={1}
                color="var(--wfe-grid)"
              />
              <Controls showInteractive={false} />
              <MiniMap
                pannable
                zoomable
                nodeColor={() => 'var(--wfe-minimap-node)'}
                maskColor="var(--wfe-minimap-mask)"
              />
            </ReactFlow>
          ) : (
            <div className="wfe-loading">Loading template…</div>
          )}
        </div>
        <StageInspector
          draft={frozen ? displayWorkflow : editor.draft}
          selected={
            frozen
              ? (displayWorkflow.stages.find(
                  (s) =>
                    s.id ===
                    editor.draft.stages.find((d) => d.key === editor.selected)?.id,
                )?.key ??
                displayWorkflow.stages[0]?.key ??
                null)
              : editor.selected
          }
          issues={frozen ? [] : issues}
          harnessModels={harnessModels}
          worktrees={worktrees}
          onWorkflowField={frozen ? () => {} : onWorkflowField}
          onStageField={frozen ? () => {} : onStageField}
          onDeleteStage={frozen ? () => {} : onDeleteStage}
          onAddStage={frozen ? () => {} : onAddStage}
        />
      </div>

      {stageSearch !== null ? (
        <div className="wfe-search" role="search">
          <label className="wfe-search__label" htmlFor="wfe-stage-search">
            search
          </label>
          <span className="wfe-search__sep" aria-hidden="true">
            ▸
          </span>
          <input
            id="wfe-stage-search"
            ref={searchInputRef}
            className="wfe-search__input"
            type="search"
            value={stageSearch}
            placeholder="stage id or title"
            autoComplete="off"
            spellCheck={false}
            onChange={(e) => setStageSearch(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Escape') {
                e.preventDefault();
                e.stopPropagation();
                setStageSearch(null);
                return;
              }
              if (e.key === 'Enter') {
                e.preventDefault();
                e.stopPropagation();
                commitStageSearch();
              }
            }}
          />
          <span className="wfe-search__count">
            {stageSearchMatches.length} match{stageSearchMatches.length === 1 ? '' : 'es'}
          </span>
          {stageSearchMatches.length > 0 ? (
            <ul className="wfe-search__hits" aria-label="Matching stages">
              {stageSearchMatches.slice(0, 8).map((stage) => (
                <li key={stage.key}>
                  <button
                    type="button"
                    className="wfe-search__hit"
                    onClick={() => {
                      focusStage(stage.key);
                      setStageSearch(null);
                    }}
                  >
                    <span className="wfe-search__hit-id">{stage.id || '(blank)'}</span>
                    {stage.title ? (
                      <span className="wfe-search__hit-title">{stage.title}</span>
                    ) : null}
                  </button>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}

      <footer className="wfe-footer">
        <span
          className={
            status === 'error' ? 'wfe-footer__msg wfe-footer__msg--error' : 'wfe-footer__msg'
          }
        >
          {feedback ??
            (frozen
              ? 'viewing run snapshot'
              : errorCount > 0
                ? `${errorCount} error${errorCount === 1 ? '' : 's'}${warningCount > 0 ? `, ${warningCount} warning${warningCount === 1 ? '' : 's'}` : ''}`
                : warningCount > 0
                  ? `${warningCount} warning${warningCount === 1 ? '' : 's'}`
                  : `${editor.draft.stages.length} stage${editor.draft.stages.length === 1 ? '' : 's'}`)}
        </span>
        <span className="wfe-footer__hint">
          {frozen
            ? 'run in progress · edits resume when the run ends'
            : 'drag · connect · / search · Del removes · ⌘Z undo · Shift+R save & run'}
        </span>
      </footer>
    </div>
  );
}
