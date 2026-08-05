import { describe, expect, it, vi } from 'vitest';
import {
  fetchRecentRepos,
  initializeRepository,
  repoDisplayName,
  websocketUrlForRepository,
} from '../../src/application/reposApi.js';

describe('reposApi', () => {
  it('repoDisplayName returns the last path segment', () => {
    expect(repoDisplayName('/home/luke/Code/murder')).toBe('murder');
    expect(repoDisplayName('/tmp/demo/')).toBe('demo');
    expect(repoDisplayName('solo')).toBe('solo');
  });

  it('websocketUrlForRepository maps http daemon base to path-scoped ws URL', () => {
    expect(websocketUrlForRepository('http://127.0.0.1:62077', 'abc-123')).toBe(
      'ws://127.0.0.1:62077/api/ws/abc-123',
    );
    expect(websocketUrlForRepository('https://example.test/', 'r1')).toBe(
      'wss://example.test/api/ws/r1',
    );
  });

  it('fetchRecentRepos GETs /api/repos against the daemon base', async () => {
    const fetchImpl = vi.fn(async () =>
      Response.json({
        repositories: [
          {
            repository_id: 'r1',
            root_path: '/a/b',
            created_at: '2026-01-01T00:00:00Z',
            last_seen_at: '2026-01-02T00:00:00Z',
            active: true,
          },
        ],
      }),
    );
    const rows = await fetchRecentRepos('http://127.0.0.1:62077', fetchImpl);
    expect(fetchImpl).toHaveBeenCalledWith('http://127.0.0.1:62077/api/repos');
    expect(rows).toHaveLength(1);
    expect(rows[0]?.repository_id).toBe('r1');
  });

  it('initializeRepository POSTs /api/repos/init', async () => {
    const fetchImpl = vi.fn(async () =>
      Response.json(
        {
          repository_id: 'new',
          root_path: '/tmp/x',
          created_at: '2026-01-01T00:00:00Z',
          last_seen_at: '2026-01-01T00:00:00Z',
        },
        { status: 201 },
      ),
    );
    const created = await initializeRepository('http://127.0.0.1:62077', '/tmp/x', { fetchImpl });
    expect(fetchImpl).toHaveBeenCalledWith(
      'http://127.0.0.1:62077/api/repos/init',
      expect.objectContaining({ method: 'POST' }),
    );
    expect(created.repository_id).toBe('new');
  });
});
