/**
 * SpawnRogueDialog — web counterpart of inktui's SpawnWizardModal (`ctrl+s`).
 *
 * Single form covering the essential {@link SpawnRogueParams}: harness, model, effort (model-aware
 * via {@link effortMatrixFor}), worktree (+ branch when "+ new"), optional name. Loads harness
 * models / worktree options / spawn favorites on open (cancelled-flag stale-load guards). Submits
 * through `createSpawnActions(bus, store).spawnRogue`.
 */

import { useAppStore, useAppStoreApi } from '@core/hooks/useAppStore.js';
import {
  DEFAULT_HARNESS,
  HARNESS_ORDER,
  defaultEffortCursor,
  effortMatrixFor,
} from '@core/components/spawnWizardMachine.js';
import {
  modelsFor,
  STATIC_HARNESS_MODELS,
  createHarnessModelsActions,
  type HarnessModel,
} from '@core/store/dialogs/harnessModelsActions.js';
import { createSpawnActions } from '@core/store/dialogs/spawnActions.js';
import {
  createSpawnFavoritesActions,
  type SpawnFavorite,
} from '@core/store/dialogs/spawnFavoritesActions.js';
import {
  NEW_WORKTREE_KEY,
  buildWorktreeOptions,
  createWorktreeOptionsActions,
  resolveWorktreePayload,
  type WorktreeOption,
} from '@core/store/dialogs/worktreeOptionsActions.js';
import { toastStore } from '@core/store/toast/toastStore.js';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useApplicationClient } from '../../application/ApplicationClientContext.js';
import { Button, Dialog, Input, Select } from '../ds/index.js';

export interface SpawnRogueDialogProps {
  readonly open: boolean;
  readonly onClose: () => void;
}

function harnessLabel(id: string): string {
  return id.replace(/_/g, '-');
}

export function SpawnRogueDialog({ open, onClose }: SpawnRogueDialogProps): React.JSX.Element {
  const bus = useApplicationClient();
  const storeApi = useAppStoreApi();
  const enabledHarnesses = useAppStore((s) => s.settings.effectiveCrowHarnesses);

  const harnessOptions = useMemo(() => {
    const list =
      enabledHarnesses.length > 0 ? enabledHarnesses : ([...HARNESS_ORDER] as readonly string[]);
    return list.map((h) => ({ value: h, label: harnessLabel(h) }));
  }, [enabledHarnesses]);

  const initialHarness = harnessOptions[0]?.value ?? DEFAULT_HARNESS;

  const [modelMap, setModelMap] = useState<Record<string, readonly HarnessModel[]>>(
    STATIC_HARNESS_MODELS,
  );
  const [worktreeOptions, setWorktreeOptions] = useState<readonly WorktreeOption[]>(() =>
    buildWorktreeOptions([]),
  );
  const [favorites, setFavorites] = useState<readonly SpawnFavorite[]>([]);
  const [favoriteKey, setFavoriteKey] = useState('');

  const [harness, setHarness] = useState(initialHarness);
  const [model, setModel] = useState('');
  const [effort, setEffort] = useState('');
  const [worktreeKey, setWorktreeKey] = useState(worktreeOptions[0]?.key ?? '');
  const [branch, setBranch] = useState('');
  const [name, setName] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  // Load live models / worktrees / favorites on open; ignore late replies after close.
  useEffect(() => {
    if (!open) return;
    let cancelled = false;

    setModelMap(STATIC_HARNESS_MODELS);
    setWorktreeOptions(buildWorktreeOptions([]));
    setFavorites([]);
    setFavoriteKey('');
    setHarness(initialHarness);
    setModel('');
    setEffort('');
    setWorktreeKey(buildWorktreeOptions([])[0]?.key ?? '');
    setBranch('');
    setName('');
    setError(null);
    setPending(false);

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
    void createSpawnFavoritesActions(bus)
      .load()
      .then((f) => {
        if (!cancelled) setFavorites(f);
      });

    return () => {
      cancelled = true;
    };
  }, [open, bus, initialHarness]);

  const modelList = useMemo(() => modelsFor(harness, modelMap), [harness, modelMap]);
  const effortSpec = useMemo(() => effortMatrixFor(harness, model), [harness, model]);

  // Seed model/effort defaults when harness or model list changes.
  useEffect(() => {
    if (!open) return;
    if (modelList.length > 0) {
      const stillValid = modelList.some((m) => m.id === model);
      if (!stillValid) {
        const next = modelList[0]?.id ?? '';
        setModel(next);
        const spec = effortMatrixFor(harness, next);
        setEffort(spec.options[defaultEffortCursor(harness, next)] ?? '');
      }
    } else if (model !== '') {
      setModel('');
      setEffort('');
    }
  }, [open, harness, modelList, model]);

  useEffect(() => {
    if (!open) return;
    if (effortSpec.options.length === 0) {
      if (effort !== '') setEffort('');
      return;
    }
    if (!effortSpec.options.includes(effort)) {
      setEffort(effortSpec.options[defaultEffortCursor(harness, model)] ?? '');
    }
  }, [open, effortSpec, effort, harness, model]);

  const onHarnessChange = (next: string): void => {
    setHarness(next);
    setFavoriteKey('');
    setModel('');
    setEffort('');
  };

  const applyFavorite = (idxStr: string): void => {
    setFavoriteKey(idxStr);
    if (idxStr === '') return;
    const f = favorites[Number(idxStr)];
    if (f === undefined) return;
    setHarness(f.harness);
    setModel(f.model);
    setEffort(f.effort);
  };

  const submit = useCallback(() => {
    if (pending) return;
    if (worktreeKey === NEW_WORKTREE_KEY && branch.trim().length === 0) {
      setError('Branch name is required.');
      return;
    }
    setPending(true);
    setError(null);
    const wt = resolveWorktreePayload(worktreeKey, branch);
    const trimmedName = name.trim();
    const actions = createSpawnActions(bus, storeApi);
    void actions
      .spawnRogue({
        harness,
        model,
        ...(effort !== '' ? { effort } : {}),
        ...(trimmedName !== '' ? { name: trimmedName } : {}),
        ...wt,
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
  }, [branch, bus, effort, harness, model, name, onClose, pending, storeApi, worktreeKey]);

  return (
    <Dialog
      open={open}
      title="Spawn Rogue"
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={pending}>
            Cancel
          </Button>
          <Button variant="primary" onClick={submit} disabled={pending}>
            {pending ? 'Spawning…' : 'Spawn'}
          </Button>
        </>
      }
    >
      <form
        className="creation-form"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        {favorites.length > 0 ? (
          <Select
            label="Favorite"
            value={favoriteKey}
            disabled={pending}
            onChange={(e) => applyFavorite(e.target.value)}
            options={[
              { value: '', label: '— none —' },
              ...favorites.map((f, i) => ({ value: String(i), label: f.name })),
            ]}
          />
        ) : null}

        <Select
          label="Harness"
          value={harness}
          disabled={pending}
          onChange={(e) => onHarnessChange(e.target.value)}
          options={harnessOptions}
        />

        {modelList.length > 0 ? (
          <Select
            label="Model"
            value={model}
            disabled={pending}
            onChange={(e) => {
              setModel(e.target.value);
              setFavoriteKey('');
            }}
            options={modelList.map((m) => ({ value: m.id, label: m.label }))}
          />
        ) : null}

        {effortSpec.options.length > 0 ? (
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
        ) : null}

        <Select
          label="Worktree"
          value={worktreeKey}
          disabled={pending}
          onChange={(e) => setWorktreeKey(e.target.value)}
          options={worktreeOptions.map((o) => ({ value: o.key, label: o.label }))}
        />

        {worktreeKey === NEW_WORKTREE_KEY ? (
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
        ) : null}

        <Input
          label="Name"
          value={name}
          placeholder="blank = autogenerate"
          disabled={pending}
          onChange={(e) => setName(e.target.value)}
        />

        {error !== null ? <p className="mds-field__hint mds-field__hint--error">{error}</p> : null}
      </form>
    </Dialog>
  );
}
