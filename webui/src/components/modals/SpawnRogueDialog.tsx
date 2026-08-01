/** SpawnRogueDialog — web counterpart of inktui's SpawnWizardModal (`ctrl+s`), stepped via nextStep. */

import { useAppStore, useAppStoreApi } from '@murder/ui-core/hooks/useAppStore.js';
import {
  DEFAULT_HARNESS,
  HARNESS_ORDER,
  defaultEffortCursor,
  effortMatrixFor,
  nextStep,
  stepProgress,
  stepsFor,
  type StepConditions,
  type WizardStep,
} from '@murder/ui-core/components/spawnWizardMachine.js';
import {
  modelsFor,
  STATIC_HARNESS_MODELS,
  createHarnessModelsActions,
  type HarnessModel,
} from '@murder/ui-core/store/dialogs/harnessModelsActions.js';
import { createSpawnActions } from '@murder/ui-core/store/dialogs/spawnActions.js';
import {
  createSpawnFavoritesActions,
  type SpawnFavorite,
} from '@murder/ui-core/store/dialogs/spawnFavoritesActions.js';
import {
  NEW_WORKTREE_KEY,
  buildWorktreeOptions,
  createWorktreeOptionsActions,
  resolveWorktreePayload,
  type WorktreeOption,
} from '@murder/ui-core/store/dialogs/worktreeOptionsActions.js';
import { DOC_DIR } from '@murder/ui-core/store/docView/docViewSlice.js';
import { toastStore } from '@murder/ui-core/store/toast/toastStore.js';
import { useEffect, useMemo, useState } from 'react';
import { useApplicationClient } from '@murder/ui-core/hooks/useApplicationClient.js';
import { Button, Checkbox, Dialog, Input, Select } from '../ds/index.js';
import { publishModeHints, spawnDialogHints } from '../../keybindModeHints.js';

export interface SpawnRogueDialogProps {
  /** Optional while App remounts-on-open; Dialog defaults to true. */
  readonly open?: boolean;
  readonly onClose: () => void;
}

/** Doc reference-by-path for the optional kickoff step (TUI {@link deriveSpawnContext}). */
export interface SpawnContext {
  readonly title: string;
  readonly path: string;
}

const EMPTY_WORKTREES = buildWorktreeOptions([]);
const MAX_FAVORITES = 10;

/**
 * Web spawn context: include the open doc when `docView` has one (no TUI focus gate — the stage
 * doc is the focused surface).
 */
export function deriveWebSpawnContext(
  open: { readonly kind: keyof typeof DOC_DIR; readonly name: string } | null,
): SpawnContext | null {
  if (open === null) return null;
  const dir = DOC_DIR[open.kind];
  return { title: open.name, path: `.murder/${dir}/${open.name}.md` };
}

function toastError(err: unknown): void {
  const message = err instanceof Error ? err.message : String(err);
  toastStore.getState().push(message, { severity: 'error', ttlMs: 12000 });
}

const STEP_LABEL: Readonly<Record<WizardStep, string>> = {
  harness: 'Harness',
  model: 'Model',
  effort: 'Effort',
  worktree: 'Worktree',
  branch: 'Branch',
  name: 'Name',
  context: 'Context',
  nameFavorite: 'Favorite name',
};

function prevStep(current: WizardStep, c: StepConditions): WizardStep | null {
  const steps = stepsFor(c);
  const idx = steps.indexOf(current);
  if (idx <= 0) return null;
  return steps[idx - 1] ?? null;
}

export function SpawnRogueDialog({ open, onClose }: SpawnRogueDialogProps): React.JSX.Element {
  const bus = useApplicationClient();
  const storeApi = useAppStoreApi();
  const enabledHarnesses = useAppStore((s) => s.settings.effectiveCrowHarnesses);
  const openDoc = useAppStore((s) => s.docView.open);
  const spawnContext = useMemo(() => deriveWebSpawnContext(openDoc), [openDoc]);

  const harnessOptions = useMemo(() => {
    const list =
      enabledHarnesses.length > 0 ? enabledHarnesses : ([...HARNESS_ORDER] as readonly string[]);
    return list.map((h) => ({ value: h, label: h.replace(/_/g, '-') }));
  }, [enabledHarnesses]);

  const initialHarness = harnessOptions[0]?.value ?? DEFAULT_HARNESS;

  const [modelMap, setModelMap] = useState<Record<string, readonly HarnessModel[]>>(
    STATIC_HARNESS_MODELS,
  );
  const [worktreeOptions, setWorktreeOptions] =
    useState<readonly WorktreeOption[]>(EMPTY_WORKTREES);
  const [favorites, setFavorites] = useState<readonly SpawnFavorite[]>([]);
  const [favoritesReady, setFavoritesReady] = useState(false);
  const [favoriteKey, setFavoriteKey] = useState('');
  const [favoriteNameDraft, setFavoriteNameDraft] = useState('');
  const [favoriteMode, setFavoriteMode] = useState<'idle' | 'rename' | 'confirmDelete'>('idle');
  const [includeDoc, setIncludeDoc] = useState(true);

  const [step, setStep] = useState<WizardStep>('harness');
  const [harness, setHarness] = useState(initialHarness);
  const [model, setModel] = useState('');
  const [effort, setEffort] = useState('');
  const [worktreeKey, setWorktreeKey] = useState(EMPTY_WORKTREES[0]?.key ?? '');
  const [branch, setBranch] = useState('');
  const [name, setName] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const favoriteActions = useMemo(() => createSpawnFavoritesActions(bus), [bus]);

  const conditions: StepConditions = useMemo(
    () => ({
      harness,
      model,
      modelMap,
      newWorktree: worktreeKey === NEW_WORKTREE_KEY,
      hasContext: spawnContext !== null,
      creatingFavorite: false,
    }),
    [harness, model, modelMap, worktreeKey, spawnContext],
  );

  const progress = stepProgress(step, conditions);
  const following = nextStep(step, conditions);
  const isLast = following === null;

  // Fetch live models / worktrees / favorites; ignore late replies after unmount.
  useEffect(() => {
    let cancelled = false;
    void createHarnessModelsActions(bus)
      .fetch()
      .then((map) => {
        if (!cancelled) setModelMap(map);
      });
    void createWorktreeOptionsActions(bus)
      .fetch()
      .then((opts) => {
        if (!cancelled) {
          setWorktreeOptions(opts);
          setWorktreeKey(opts[0]?.key ?? '');
        }
      });
    void favoriteActions.load().then((f) => {
      if (!cancelled) {
        setFavorites(f);
        setFavoritesReady(true);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [bus, favoriteActions]);

  // Default include-doc on when a doc is open at dialog mount / when it appears.
  useEffect(() => {
    setIncludeDoc(spawnContext !== null);
  }, [spawnContext]);

  // KeybindBar mode hints track the active wizard step (TUI spawnWizardHints parity).
  useEffect(() => {
    if (open === false) return;
    return publishModeHints(
      spawnDialogHints(step, {
        favoritesFocused: step === 'harness' && favoriteKey !== '',
      }),
    );
  }, [open, step, favoriteKey]);

  // Resync step if harness/worktree change skipped the current step.
  useEffect(() => {
    const active = stepsFor(conditions);
    if (!active.includes(step)) {
      setStep(active[0] ?? 'harness');
    }
  }, [conditions, step]);

  const modelList = useMemo(() => modelsFor(harness, modelMap), [harness, modelMap]);
  const effortSpec = useMemo(() => effortMatrixFor(harness, model), [harness, model]);

  // Seed model/effort defaults when harness or model list changes.
  useEffect(() => {
    if (modelList.length > 0) {
      const stillValid = modelList.some((m) => m.id === model);
      if (!stillValid) {
        const next = modelList[0]?.id ?? '';
        setModel(next);
        setEffort(effortMatrixFor(harness, next).options[defaultEffortCursor(harness, next)] ?? '');
      }
    } else if (model !== '') {
      setModel('');
      setEffort('');
    }
  }, [harness, modelList, model]);

  const selectedFavoriteIndex =
    favoriteKey === '' ? -1 : Number.parseInt(favoriteKey, 10);
  const selectedFavorite =
    selectedFavoriteIndex >= 0 ? (favorites[selectedFavoriteIndex] ?? null) : null;

  const persistFavorites = (next: readonly SpawnFavorite[]): void => {
    void favoriteActions
      .save(next)
      .then((saved) => {
        setFavorites(saved);
        setFavoriteMode('idle');
        setFavoriteNameDraft('');
      })
      .catch((err: unknown) => {
        toastError(err);
        setError(err instanceof Error ? err.message : String(err));
      });
  };

  const saveCurrentAsFavorite = (): void => {
    const trimmed = favoriteNameDraft.trim();
    if (trimmed.length === 0) {
      setError('Favorite name is required.');
      return;
    }
    if (favorites.length >= MAX_FAVORITES) {
      setError(`At most ${MAX_FAVORITES} favorites.`);
      return;
    }
    setError(null);
    persistFavorites([
      ...favorites,
      { name: trimmed, harness, model, effort },
    ]);
    setFavoriteNameDraft('');
  };

  const commitRename = (): void => {
    if (selectedFavorite === null || selectedFavoriteIndex < 0) return;
    const trimmed = favoriteNameDraft.trim();
    if (trimmed.length === 0) {
      setError('Favorite name is required.');
      return;
    }
    setError(null);
    const next = favorites.map((f, i) =>
      i === selectedFavoriteIndex ? { ...f, name: trimmed } : f,
    );
    persistFavorites(next);
  };

  const commitDelete = (): void => {
    if (selectedFavoriteIndex < 0) return;
    const next = favorites.filter((_, i) => i !== selectedFavoriteIndex);
    setFavoriteKey('');
    persistFavorites(next);
  };

  const applyFavorite = (idxStr: string): void => {
    setFavoriteKey(idxStr);
    setFavoriteMode('idle');
    setFavoriteNameDraft('');
    if (idxStr === '') return;
    const f = favorites[Number(idxStr)];
    if (f === undefined) return;
    setHarness(f.harness);
    setModel(f.model);
    setEffort(f.effort);
  };

  const submit = (): void => {
    if (pending) return;
    if (worktreeKey === NEW_WORKTREE_KEY && branch.trim().length === 0) {
      setError('Branch name is required.');
      return;
    }
    setPending(true);
    setError(null);
    const wt = resolveWorktreePayload(worktreeKey, branch);
    const trimmedName = name.trim();
    const kickoffMessage =
      spawnContext !== null && includeDoc
        ? `Please read ${spawnContext.path} before starting.`
        : null;
    const actions = createSpawnActions(bus, storeApi);
    void actions
      .spawnRogue({
        harness,
        model,
        ...(effort !== '' ? { effort } : {}),
        ...(trimmedName !== '' ? { name: trimmedName } : {}),
        ...wt,
        kickoffMessage,
      })
      .then(() => {
        onClose();
        toastStore.getState().push('rogue spawned', { ttlMs: 6000 });
      })
      .catch((err: unknown) => {
        const message = err instanceof Error ? err.message : String(err);
        setPending(false);
        setError(message);
        toastStore.getState().push(message, { severity: 'error', ttlMs: 12000 });
      });
  };

  const goNext = (): void => {
    if (step === 'branch' && branch.trim().length === 0) {
      setError('Branch name is required.');
      return;
    }
    setError(null);
    const n = nextStep(step, conditions);
    if (n === null) {
      submit();
      return;
    }
    setStep(n);
  };

  const goBack = (): void => {
    setError(null);
    const p = prevStep(step, conditions);
    if (p !== null) setStep(p);
  };

  const canCreate = favoritesReady && favorites.length < MAX_FAVORITES;

  const favoritesBlock =
    step === 'harness' ? (
      <div className="creation-form__favorites">
        <Select
          label="Favorite"
          value={favoriteKey}
          disabled={pending || !favoritesReady}
          onChange={(e) => applyFavorite(e.target.value)}
          options={[
            { value: '', label: favoritesReady ? '— none —' : 'loading…' },
            ...favorites.map((f, i) => ({ value: String(i), label: f.name })),
          ]}
        />

        {selectedFavorite !== null && favoriteMode === 'idle' ? (
          <div className="creation-form__row">
            <Button
              size="sm"
              disabled={pending}
              onClick={() => {
                setFavoriteMode('rename');
                setFavoriteNameDraft(selectedFavorite.name);
                setError(null);
              }}
            >
              Rename
            </Button>
            <Button
              size="sm"
              variant="danger"
              disabled={pending}
              onClick={() => {
                setFavoriteMode('confirmDelete');
                setError(null);
              }}
            >
              Delete
            </Button>
          </div>
        ) : null}

        {favoriteMode === 'rename' && selectedFavorite !== null ? (
          <div className="creation-form__row creation-form__row--stack">
            <Input
              label="Rename favorite"
              value={favoriteNameDraft}
              disabled={pending}
              onChange={(e) => {
                setFavoriteNameDraft(e.target.value);
                setError(null);
              }}
            />
            <div className="creation-form__row">
              <Button size="sm" variant="primary" disabled={pending} onClick={commitRename}>
                Save name
              </Button>
              <Button
                size="sm"
                variant="ghost"
                disabled={pending}
                onClick={() => {
                  setFavoriteMode('idle');
                  setFavoriteNameDraft('');
                }}
              >
                Cancel
              </Button>
            </div>
          </div>
        ) : null}

        {favoriteMode === 'confirmDelete' && selectedFavorite !== null ? (
          <div className="creation-form__row">
            <span className="creation-form__confirm">
              Delete “{selectedFavorite.name}”?
            </span>
            <Button size="sm" variant="danger" disabled={pending} onClick={commitDelete}>
              Delete
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={pending}
              onClick={() => setFavoriteMode('idle')}
            >
              Cancel
            </Button>
          </div>
        ) : null}

        {canCreate && favoriteMode === 'idle' ? (
          <div className="creation-form__row creation-form__row--stack">
            <Input
              label="Save current as favorite"
              value={favoriteNameDraft}
              placeholder="e.g. OpusMed"
              disabled={pending}
              onChange={(e) => {
                setFavoriteNameDraft(e.target.value);
                setError(null);
              }}
            />
            <Button size="sm" disabled={pending} onClick={saveCurrentAsFavorite}>
              Save favorite
            </Button>
          </div>
        ) : null}
      </div>
    ) : null;

  let stepBody: React.ReactNode = null;
  switch (step) {
    case 'harness':
      stepBody = (
        <Select
          label="Harness"
          value={harness}
          disabled={pending}
          onChange={(e) => {
            setHarness(e.target.value);
            setFavoriteKey('');
          }}
          options={harnessOptions}
        />
      );
      break;
    case 'model':
      stepBody = (
        <Select
          label="Model"
          value={model}
          disabled={pending}
          onChange={(e) => {
            const next = e.target.value;
            setModel(next);
            setEffort(
              effortMatrixFor(harness, next).options[defaultEffortCursor(harness, next)] ?? '',
            );
            setFavoriteKey('');
          }}
          options={modelList.map((m) => ({ value: m.id, label: m.label }))}
        />
      );
      break;
    case 'effort':
      stepBody = (
        <Select
          label="Effort"
          value={effort}
          disabled={pending}
          onChange={(e) => {
            setEffort(e.target.value);
            setFavoriteKey('');
          }}
          options={effortSpec.options.map((o) => ({ value: o, label: o }))}
        />
      );
      break;
    case 'worktree':
      stepBody = (
        <Select
          label="Worktree"
          value={worktreeKey}
          disabled={pending}
          onChange={(e) => setWorktreeKey(e.target.value)}
          options={worktreeOptions.map((o) => ({ value: o.key, label: o.label }))}
        />
      );
      break;
    case 'branch':
      stepBody = (
        <Input
          label="Branch name"
          value={branch}
          placeholder="e.g. feature/my-work"
          disabled={pending}
          invalid={error !== null && branch.trim().length === 0}
          onChange={(e) => {
            setBranch(e.target.value);
            setError(null);
          }}
        />
      );
      break;
    case 'name':
      stepBody = (
        <Input
          label="Name"
          value={name}
          placeholder="blank = autogenerate"
          disabled={pending}
          onChange={(e) => setName(e.target.value)}
        />
      );
      break;
    case 'context':
      stepBody =
        spawnContext !== null ? (
          <Checkbox
            checked={includeDoc}
            disabled={pending}
            onChange={(e) => setIncludeDoc(e.target.checked)}
            label={`Read “${spawnContext.title}” before starting (${spawnContext.path})`}
          />
        ) : null;
      break;
    default:
      stepBody = null;
  }

  return (
    <Dialog
      open={open ?? true}
      title="Spawn Rogue"
      onClose={onClose}
      className="spawn-wizard"
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={pending}>
            Cancel
          </Button>
          {progress.index > 1 ? (
            <Button variant="ghost" onClick={goBack} disabled={pending}>
              Back
            </Button>
          ) : null}
          <Button variant="primary" onClick={goNext} disabled={pending}>
            {pending ? 'Spawning…' : isLast ? 'Spawn' : 'Next'}
          </Button>
        </>
      }
    >
      <form
        className="creation-form"
        onSubmit={(e) => {
          e.preventDefault();
          goNext();
        }}
      >
        <p className="spawn-wizard__progress">
          {progress.index}/{progress.total} · {STEP_LABEL[step]}
        </p>
        {favoritesBlock}
        {stepBody}
        {error !== null ? <p className="mds-field__hint mds-field__hint--error">{error}</p> : null}
      </form>
    </Dialog>
  );
}
