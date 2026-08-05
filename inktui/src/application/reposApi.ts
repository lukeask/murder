/** HTTP helpers for the daemon repo picker API (`GET /api/repos`, `POST /api/repos/init`). */

export interface RepoListEntry {
  readonly repository_id: string;
  readonly root_path: string;
  readonly created_at: string;
  readonly last_seen_at: string;
  readonly active: boolean;
}

export interface RepoInitResult {
  readonly repository_id: string;
  readonly root_path: string;
  readonly created_at: string;
  readonly last_seen_at: string;
}

export interface ListReposResponse {
  readonly repositories: readonly RepoListEntry[];
}

/** Directory basename of a repo root path (TopBar branding). */
export function repoDisplayName(rootPath: string): string {
  const trimmed = rootPath.replace(/\/+$/, '');
  const slash = trimmed.lastIndexOf('/');
  return slash >= 0 ? trimmed.slice(slash + 1) : trimmed;
}

/**
 * Build the path-scoped application WebSocket URL for `repositoryId` from the daemon HTTP base.
 * `http://127.0.0.1:62077` → `ws://127.0.0.1:62077/api/ws/{id}`.
 */
export function websocketUrlForRepository(daemonHttpUrl: string, repositoryId: string): string {
  const http = new URL(daemonHttpUrl);
  const wsProtocol = http.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${wsProtocol}//${http.host}/api/ws/${encodeURIComponent(repositoryId)}`;
}

/** List recent repositories from the daemon (`GET {daemonUrl}/api/repos`). */
export async function fetchRecentRepos(
  daemonUrl: string,
  fetchImpl: typeof fetch = globalThis.fetch,
): Promise<readonly RepoListEntry[]> {
  const base = daemonUrl.replace(/\/+$/, '');
  const response = await fetchImpl(`${base}/api/repos`);
  if (!response.ok) {
    throw new Error(`GET /api/repos failed: ${response.status} ${response.statusText}`);
  }
  const body = (await response.json()) as ListReposResponse;
  if (!Array.isArray(body.repositories)) {
    throw new Error('GET /api/repos: missing repositories array');
  }
  return body.repositories;
}

/** Scaffold + register a murder repository at `path`; returns the new (or existing) entry. */
export async function initializeRepository(
  daemonUrl: string,
  path: string,
  options: { readonly force?: boolean; readonly fetchImpl?: typeof fetch } = {},
): Promise<RepoInitResult> {
  const fetchImpl = options.fetchImpl ?? globalThis.fetch;
  const base = daemonUrl.replace(/\/+$/, '');
  const response = await fetchImpl(`${base}/api/repos/init`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path, force: options.force ?? false }),
  });
  if (!response.ok) {
    const text = await response.text().catch(() => '');
    throw new Error(text !== '' ? text : `POST /api/repos/init failed: ${response.status}`);
  }
  return (await response.json()) as RepoInitResult;
}
