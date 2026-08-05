/** Web entrypoint: repo picker → path-scoped WebSocket bus → {@link App}. */

import { AppStoreProvider } from '@murder/ui-core/hooks/useAppStore.js';
import { createAppStore } from '@murder/ui-core/store/store.js';
import {
  parkRepositoryWorkspace,
  resumeRepositoryWorkspace,
  type WorkspaceStores,
} from '@murder/ui-core/input/workspaceSwitch.js';
import { StrictMode, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
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
} from './composer/createComposerStores.js';
import './styles/theme.css';
// Design-system token foundation: imported AFTER theme.css (so DS values win on overlapping names
// like --space-*/--radius/--font-mono) and BEFORE app.css (so the existing app chrome still reads
// the runtime --color-* vars unchanged). ds.css holds the ported .mds-* component rules.
import './styles/tokens.css';
import './styles/ds.css';
import './styles/ds-forms.css';
import './styles/ds-data.css';
import './styles/ds-navigation.css';
import './styles/ds-feedback.css';
import './styles/ds-sheet.css';
import './styles/app.css';
// Cockpit shell layout (the DS reskin frame) + panel CSS. Imported AFTER the ds-*.css component
// sheets (so `.mds-*` rules exist) and after app.css (so the new `.cockpit*`/`.mw-*`/`.ticket-meta*`
// shell + panel rules win where intent overlaps). Later C2 panel groups each add their own
// `panels-<group>.css` import here during integration.
import './styles/cockpit.css';
import './styles/picker.css';
import './styles/panels.css';
import './styles/panels-roster.css';
import './styles/panels-history.css';
import './styles/panels-docs.css';
import './styles/panels-usage.css';
import './styles/panels-transit.css';
import './styles/panels-settings.css';
import './styles/panels-stage.css';
import './styles/panels-workflows.css';
import './styles/panels-modes.css';

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
};

/**
 * One live WS + store for a single repository.
 *
 * Bus/store are created inside the effect so React StrictMode's mount→cleanup→remount
 * cycle gets a fresh client (ApplicationWebSocketClient rejects connect() after close()).
 */
function ConnectedSession({
  repositoryId,
  rootPath,
  onSwitchRepo,
  composer,
}: {
  readonly repositoryId: string;
  readonly rootPath: string | null;
  readonly onSwitchRepo: () => void;
  readonly composer: ReturnType<typeof createComposerStores>;
}): React.JSX.Element | null {
  const [session, setSession] = useState<Session | null>(null);

  useEffect(() => {
    const bus = createBrowserApplicationClient({ repositoryId });
    const { store } = createAppStore(bus);
    setSession({ bus, store });
    void bus.connect().catch((error: unknown) => {
      console.warn('initial bus connect failed:', error);
    });
    return () => {
      bus.close();
    };
  }, [repositoryId]);

  // Phase 8: park/resume per-repo workspace bags on the shared composer stores.
  useEffect(() => {
    if (session === null) return;
    const workspaceStores: WorkspaceStores = toWorkspaceStores(composer, session.store);
    resumeRepositoryWorkspace(workspaceStores, repositoryId, {
      freshSnapshot: webFreshWorkspaceSnapshot(),
    });
    return () => {
      parkRepositoryWorkspace(workspaceStores, repositoryId);
    };
  }, [session, composer, repositoryId]);

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

function Boot(): React.JSX.Element {
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

  const refresh = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const rows = await fetchRecentRepos();
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
      setLoadError(error instanceof Error ? error.message : String(error));
    } finally {
      setLoading(false);
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

const rootEl = document.getElementById('root');
if (rootEl === null) {
  throw new Error('missing #root element');
}

createRoot(rootEl).render(
  <StrictMode>
    <Boot />
  </StrictMode>,
);
