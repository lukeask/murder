/** RepoPicker: lists recent repos, selects, and posts init. */

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { RepoPicker } from '../src/components/RepoPicker.js';
import type { RepoListEntry } from '../src/application/reposApi.js';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const REPOS: readonly RepoListEntry[] = [
  {
    repository_id: 'repo-a',
    root_path: '/home/luke/Code/alpha',
    created_at: '2026-01-01T00:00:00Z',
    last_seen_at: '2026-08-05T12:00:00Z',
    active: true,
  },
  {
    repository_id: 'repo-b',
    root_path: '/home/luke/Code/beta',
    created_at: '2026-01-02T00:00:00Z',
    last_seen_at: '2026-08-04T12:00:00Z',
    active: false,
  },
];

describe('RepoPicker', () => {
  beforeEach(() => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new Error('unexpected fetch');
      }),
    );
  });

  it('renders recent repos ordered as given and selects on click', () => {
    const onSelect = vi.fn();
    render(<RepoPicker repositories={REPOS} onSelect={onSelect} />);
    expect(screen.getByText('alpha')).toBeTruthy();
    expect(screen.getByText('/home/luke/Code/alpha')).toBeTruthy();
    expect(screen.getByText('active')).toBeTruthy();
    fireEvent.click(screen.getByText('beta'));
    expect(onSelect).toHaveBeenCalledWith(REPOS[1]);
  });

  it('initializes a new repository via POST /api/repos/init then selects it', async () => {
    const onSelect = vi.fn();
    const fetchImpl = vi.fn(async (_url: string, init?: RequestInit) => {
      expect(init?.method).toBe('POST');
      expect(JSON.parse(String(init?.body))).toEqual({ path: '/tmp/fresh', force: false });
      return {
        ok: true,
        json: async () => ({
          repository_id: 'repo-new',
          root_path: '/tmp/fresh',
          created_at: 't0',
          last_seen_at: 't1',
        }),
      };
    });
    vi.stubGlobal('fetch', fetchImpl);

    render(<RepoPicker repositories={[]} onSelect={onSelect} />);
    fireEvent.change(screen.getByLabelText('Path'), { target: { value: '/tmp/fresh' } });
    fireEvent.click(screen.getByRole('button', { name: 'Initialize' }));

    await waitFor(() => {
      expect(onSelect).toHaveBeenCalledWith(
        expect.objectContaining({ repository_id: 'repo-new', root_path: '/tmp/fresh' }),
      );
    });
    expect(fetchImpl).toHaveBeenCalledWith('/api/repos/init', expect.any(Object));
  });

  it('surfaces init errors without selecting', async () => {
    const onSelect = vi.fn();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: false,
        status: 409,
        text: async () => 'already initialized',
      })),
    );
    render(<RepoPicker repositories={[]} onSelect={onSelect} />);
    fireEvent.change(screen.getByLabelText('Path'), { target: { value: '/tmp/x' } });
    fireEvent.click(screen.getByRole('button', { name: 'Initialize' }));
    await waitFor(() => {
      expect(screen.getByText('already initialized')).toBeTruthy();
    });
    expect(onSelect).not.toHaveBeenCalled();
  });
});
