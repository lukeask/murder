/** Web shell: repo picker → path-scoped WebSocket bus → {@link App}. */

import { AppStoreProvider } from '@murder/ui-core/hooks/useAppStore.js';
import { createAppStore } from '@murder/ui-core/store/store.js';
import { type WorkspaceStores } from '@murder/ui-core/input/workspaceSwitch.js';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { App } from './App.js';
import { ApplicationClientProvider } from '@murder/ui-core/hooks/useApplicationClient.js';
import {
  createBrowserApplicationClient,
  repositoryIdFromLocation,
} from './application/createBrowserApplicationClient.js';
import { fetchRecentRepos, type RepoListEntry } from './application/reposApi.js';
import { RepoPicker } from './components/RepoPicker.js';
import { ComposerStoresProvider } from './composer/ComposerStoresProvider.js';
import {
  createComposerStores,
  toWorkspaceStores,
  webFreshWorkspaceSnapshot,
  type ComposerStores,
} from './composer/createComposerStores.js';
import {
  parkWebRepositoryWorkspace,
  resumeWebRepositoryWorkspace,
} from './composer/workspaceActions.js';

function setRepoSearchParam(repositoryId: string | null): void {
  const url = new URL(globalThis.location.href);
  if (repositoryId === null) {
    url.searchParams.delete('repo');
  } else {
    url.searchParams.set('repo', repositoryId);
  }
  const next = `${url.pathname}${url.search}${url.hash}`;
  globalThis.history.replaceState(null, '', next);
}

function basename(rootPath: string): string {
  const trimmed = rootPath.replace(/\/+$/, '');
  const slash = trimmed.lastIndexOf('/');
  return slash >= 0 ? trimmed.slice(slash + 1) : trimmed;
}

type Session = {
  readonly bus: ReturnType<typeof createBrowserApplicationClient>;
  readonly store: ReturnType<typeof createAppStore>['store'];
  readonly dispose: () => void;
};

/**
 * One live WS + store for a single repository.
 *
 * Bus/store are created inside the effect so React StrictMode's mount→cleanup→remount
 * cycle gets a fresh client (ApplicationWebSocketClient rejects connect() after close()).
 *
 * Park/resume runs in the same effect before `setSession`, so the first paint of
 * `<App>` already has the incoming repo's workspace bag (no outgoing-layout flash).
 */
export function ConnectedSession({
  repositoryId,
  rootPath,
  onSwitchRepo,
  composer,
}: {
  readonly repositoryId: string;
  readonly rootPath: string | null;
  readonly onSwitchRepo: () => void;
  readonly composer: ComposerStores;
}): React.JSX.Element | null {
  const [session, setSession] = useState<Session | null>(null);

  useEffect(() => {
    const bus = createBrowserApplicationClient({ repositoryId });
    const { store, dispose } = createAppStore(bus);
    const workspaceStores: WorkspaceStores = toWorkspaceStores(composer, store);
    // Resume before exposing the session so the first paint is already hydrated.
    resumeWebRepositoryWorkspace(workspaceStores, repositoryId, {
      freshSnapshot: webFreshWorkspaceSnapshot(),
    });
    setSession({ bus, store, dispose });
    void bus.connect().catch((error: unknown) => {
      console.warn('initial bus connect failed:', error);
    });
    return () => {
      parkWebRepositoryWorkspace(workspaceStores, repositoryId);
      dispose();
      bus.close();
      setSession(null);
    };
  }, [repositoryId, composer]);

  useEffect(() => {
    setRepoSearchParam(repositoryId);
  }, [repositoryId]);

  if (session === null) return null;

  const projectHint = rootPath !== null && rootPath !== '' ? basename(rootPath) : null;

  return (
    <AppStoreProvider value={session.store}>
      <ApplicationClientProvider value={session.bus}>
        <App bus={session.bus} onSwitchRepo={onSwitchRepo} repositoryHint={projectHint} />
      </ApplicationClientProvider>
    </AppStoreProvider>
  );
}

export function Boot(): React.JSX.Element {
  // Survive picker ↔ session remounts so per-repo workspace bags stay in memory.
  const composer = useMemo(() => createComposerStores(), []);
  const [repositories, setRepositories] = useState<readonly RepoListEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selected, setSelected] = useState<{
    readonly repositoryId: string;
    readonly rootPath: string | null;
  } | null>(null);
  /** Consumed once list loads; cleared on switch or stale deep-link. */
  const pendingDeepLink = useRef<string | null>(repositoryIdFromLocation(globalThis.location));
  /** Drop stale overlapping refresh results (StrictMode double-mount, switch+retry). */
  const refreshGen = useRef(0);

  const refresh = useCallback(async () => {
    const gen = ++refreshGen.current;
    setLoading(true);
    setLoadError(null);
    try {
      const rows = await fetchRecentRepos();
      if (gen !== refreshGen.current) return;
      setRepositories(rows);

      // Resolve a pending `/?repo=` deep link only after the list confirms it exists —
      // avoids opening a permanently-failing WS for stale ids.
      const deepLink = pendingDeepLink.current;
      if (deepLink !== null) {
        pendingDeepLink.current = null;
      }
      const deepMatch =
        deepLink === null ? undefined : rows.find((r) => r.repository_id === deepLink);
      if (deepLink !== null && deepMatch === undefined) {
        setRepoSearchParam(null);
      }

      setSelected((prev) => {
        if (prev !== null) {
          const match = rows.find((r) => r.repository_id === prev.repositoryId);
          return match === undefined
            ? prev
            : { repositoryId: match.repository_id, rootPath: match.root_path };
        }
        if (deepMatch === undefined) return null;
        return {
          repositoryId: deepMatch.repository_id,
          rootPath: deepMatch.root_path,
        };
      });
    } catch (error: unknown) {
      if (gen !== refreshGen.current) return;
      const raw = error instanceof Error ? error.message : String(error);
      // Dev without a live daemon (or proxy) surfaces as Failed to fetch / HTML parse noise.
      const unreachable = /failed to fetch|networkerror|load failed|unexpected token/i.test(raw);
      setLoadError(
        unreachable
          ? 'Daemon unreachable — is it listening on :62077?'
          : raw,
      );
    } finally {
      if (gen === refreshGen.current) {
        setLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const onSelect = useCallback((repo: RepoListEntry) => {
    pendingDeepLink.current = null;
    setSelected({ repositoryId: repo.repository_id, rootPath: repo.root_path });
  }, []);

  const onSwitchRepo = useCallback(() => {
    pendingDeepLink.current = null;
    setRepoSearchParam(null);
    setSelected(null);
    void refresh();
  }, [refresh]);

  if (selected !== null) {
    return (
      <ComposerStoresProvider stores={composer}>
        <ConnectedSession
          key={selected.repositoryId}
          repositoryId={selected.repositoryId}
          rootPath={selected.rootPath}
          onSwitchRepo={onSwitchRepo}
          composer={composer}
        />
      </ComposerStoresProvider>
    );
  }

  return (
    <RepoPicker
      repositories={repositories}
      loading={loading}
      loadError={loadError}
      onSelect={onSelect}
      onRetry={() => void refresh()}
    />
  );
}
