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

/** Same-origin list of recent repositories (ordered by `last_seen_at` desc on the daemon). */
export async function fetchRecentRepos(
  fetchImpl: typeof fetch = globalThis.fetch,
): Promise<readonly RepoListEntry[]> {
  const response = await fetchImpl('/api/repos');
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
  path: string,
  options: { readonly force?: boolean; readonly fetchImpl?: typeof fetch } = {},
): Promise<RepoInitResult> {
  const fetchImpl = options.fetchImpl ?? globalThis.fetch;
  const response = await fetchImpl('/api/repos/init', {
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
