/** Boot / switcher screen: recent repos + initialize-new path form. */

import { useCallback, useState, type FormEvent } from 'react';
import type { RepoListEntry } from '../application/reposApi.js';
import { initializeRepository } from '../application/reposApi.js';
import { Badge, Button, Input, ListRow, Panel } from './ds/index.js';
import { CrowMark } from './CrowMark.js';

export interface RepoPickerProps {
  readonly repositories: readonly RepoListEntry[];
  readonly loading?: boolean;
  readonly loadError?: string | null;
  readonly onSelect: (repo: RepoListEntry) => void;
  readonly onInitialized?: (repo: RepoListEntry) => void;
  /** Refresh the recent list after a failed load or manual retry. */
  readonly onRetry?: () => void;
}

function displayName(rootPath: string): string {
  const trimmed = rootPath.replace(/\/+$/, '');
  const slash = trimmed.lastIndexOf('/');
  return slash >= 0 ? trimmed.slice(slash + 1) : trimmed;
}

function formatSeen(iso: string): string {
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return iso;
  try {
    return new Intl.DateTimeFormat(undefined, {
      dateStyle: 'medium',
      timeStyle: 'short',
    }).format(new Date(ms));
  } catch {
    return iso;
  }
}

/** Full-viewport picker: pick a recent repo or initialize a new murder repository. */
export function RepoPicker({
  repositories,
  loading = false,
  loadError = null,
  onSelect,
  onInitialized,
  onRetry,
}: RepoPickerProps): React.JSX.Element {
  const [path, setPath] = useState('');
  const [initError, setInitError] = useState<string | null>(null);
  const [initBusy, setInitBusy] = useState(false);

  const onInit = useCallback(
    async (event: FormEvent) => {
      event.preventDefault();
      const trimmed = path.trim();
      if (trimmed === '') {
        setInitError('Enter a filesystem path');
        return;
      }
      setInitBusy(true);
      setInitError(null);
      try {
        const created = await initializeRepository(trimmed);
        const entry: RepoListEntry = {
          repository_id: created.repository_id,
          root_path: created.root_path,
          created_at: created.created_at,
          last_seen_at: created.last_seen_at,
          active: false,
        };
        onInitialized?.(entry);
        onSelect(entry);
      } catch (error: unknown) {
        setInitError(error instanceof Error ? error.message : String(error));
      } finally {
        setInitBusy(false);
      }
    },
    [path, onInitialized, onSelect],
  );

  return (
    <div className="repo-picker">
      <header className="repo-picker__header">
        <span className="repo-picker__brand">
          <CrowMark size={28} />
          murder
        </span>
        <p className="repo-picker__lede">Choose a repository to open</p>
      </header>

      <div className="repo-picker__body">
        <Panel title="Recent repositories" flush count={repositories.length}>
          {loading ? (
            <p className="repo-picker__empty">Loading…</p>
          ) : loadError !== null ? (
            <div className="repo-picker__empty">
              <p>{loadError}</p>
              {onRetry !== undefined ? (
                <Button size="sm" onClick={onRetry}>
                  Retry
                </Button>
              ) : null}
            </div>
          ) : repositories.length === 0 ? (
            <p className="repo-picker__empty">No repositories yet — initialize one below.</p>
          ) : (
            repositories.map((repo) => (
              <ListRow
                key={repo.repository_id}
                title={displayName(repo.root_path)}
                meta={repo.root_path}
                trailing={
                  <>
                    {repo.active ? (
                      <Badge tone="running" dot>
                        active
                      </Badge>
                    ) : null}
                    <span className="repo-picker__seen">{formatSeen(repo.last_seen_at)}</span>
                  </>
                }
                onClick={() => onSelect(repo)}
              />
            ))
          )}
        </Panel>

        <Panel title="Initialize new murder repository">
          <form className="repo-picker__init" onSubmit={(e) => void onInit(e)}>
            <Input
              label="Path"
              placeholder="/path/to/project"
              value={path}
              onChange={(e) => setPath(e.target.value)}
              invalid={initError !== null}
              hint={initError ?? 'Creates `.murder/` scaffolding and registers the repo with the daemon.'}
              disabled={initBusy}
              autoComplete="off"
              spellCheck={false}
            />
            <Button type="submit" variant="primary" disabled={initBusy || path.trim() === ''}>
              {initBusy ? 'Initializing…' : 'Initialize'}
            </Button>
          </form>
        </Panel>
      </div>
    </div>
  );
}
