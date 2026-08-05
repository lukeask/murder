/** reposApi: list + init HTTP helpers. */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { fetchRecentRepos, initializeRepository } from '../src/application/reposApi.js';

afterEach(() => {
  vi.restoreAllMocks();
});

describe('reposApi', () => {
  it('fetchRecentRepos returns the repositories array', async () => {
    const fetchImpl = vi.fn(async () => ({
      ok: true,
      json: async () => ({
        repositories: [
          {
            repository_id: 'a',
            root_path: '/a',
            created_at: 't0',
            last_seen_at: 't1',
            active: false,
          },
        ],
      }),
    })) as unknown as typeof fetch;
    const rows = await fetchRecentRepos(fetchImpl);
    expect(rows).toHaveLength(1);
    expect(rows[0]?.repository_id).toBe('a');
    expect(fetchImpl).toHaveBeenCalledWith('/api/repos');
  });

  it('initializeRepository posts path and returns the created entry', async () => {
    const fetchImpl = vi.fn(async () => ({
      ok: true,
      json: async () => ({
        repository_id: 'new',
        root_path: '/tmp/p',
        created_at: 't0',
        last_seen_at: 't1',
      }),
    })) as unknown as typeof fetch;
    const created = await initializeRepository('/tmp/p', { fetchImpl });
    expect(created.repository_id).toBe('new');
    expect(fetchImpl).toHaveBeenCalledWith(
      '/api/repos/init',
      expect.objectContaining({ method: 'POST' }),
    );
  });
});
