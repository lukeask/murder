/**
 * RepoPickerModal — list fetch + select callback (mode factory, no full Ink mount).
 */

import { describe, expect, it, vi } from 'vitest';
import { createFocusStore } from '@murder/ui-core/input/focusStore.js';
import { createPanelStore } from '@murder/ui-core/input/panelStore.js';
import { createModeStore } from '../../src/input/modeStore.js';
import { repoPickerMode, REPO_PICKER_MODE_ID } from '../../src/components/RepoPickerModal.js';
import type { RepoListEntry } from '../../src/application/reposApi.js';
import { makeKey } from '../input/key.js';

function makeRepo(id: string, path: string, active = false): RepoListEntry {
  return {
    repository_id: id,
    root_path: path,
    created_at: '2026-01-01T00:00:00Z',
    last_seen_at: '2026-01-02T00:00:00Z',
    active,
  };
}

function makeModes() {
  const panels = createPanelStore();
  const focus = createFocusStore(panels);
  return createModeStore(focus);
}

describe('repoPickerMode', () => {
  it('fetches recent repos and selects via enter', async () => {
    const repos = [makeRepo('a', '/tmp/a'), makeRepo('b', '/tmp/b', true)];
    const fetchImpl = vi.fn(async () => Response.json({ repositories: repos }));
    const onSelect = vi.fn();
    const modes = makeModes();
    modes.getState().enter(
      repoPickerMode(modes, {
        daemonUrl: 'http://127.0.0.1:62077',
        activeRepositoryId: 'a',
        onSelect,
        fetchImpl,
      }),
    );

    await vi.waitFor(() => expect(fetchImpl).toHaveBeenCalled());
    // Allow load().then to re-enter with populated state.
    await vi.waitFor(() => {
      expect(modes.getState().stack[0]?.mode).toBeDefined();
    });
    // Microtask flush for the then() that sets repositories + refresh().
    await new Promise((r) => setTimeout(r, 0));

    const mode = modes.getState().stack.find((f) => f.mode.id === REPO_PICKER_MODE_ID)?.mode;
    expect(mode).toBeDefined();
    // Cursor starts on active repo 'a'; move down to 'b' then select.
    mode?.onIntent('down');
    mode?.onIntent('select');

    expect(onSelect).toHaveBeenCalledWith(repos[1]);
    expect(modes.getState().stack).toHaveLength(0);
  });

  it('dismisses without selecting', async () => {
    const fetchImpl = vi.fn(async () => Response.json({ repositories: [] }));
    const onSelect = vi.fn();
    const onDismiss = vi.fn();
    const modes = makeModes();
    modes.getState().enter(
      repoPickerMode(modes, {
        daemonUrl: 'http://127.0.0.1:62077',
        onSelect,
        onDismiss,
        fetchImpl,
      }),
    );
    await vi.waitFor(() => expect(fetchImpl).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 0));
    const mode = modes.getState().stack.find((f) => f.mode.id === REPO_PICKER_MODE_ID)?.mode;
    mode?.onIntent('dismiss');
    expect(onSelect).not.toHaveBeenCalled();
    expect(onDismiss).toHaveBeenCalled();
    expect(modes.getState().stack).toHaveLength(0);
  });

  it('types j/k/r into the init path (list chords must not capture them)', async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/api/repos/init') && init?.method === 'POST') {
        return Response.json(
          {
            repository_id: 'new',
            root_path: '/var/lib/rj',
            created_at: '2026-01-01T00:00:00Z',
            last_seen_at: '2026-01-01T00:00:00Z',
          },
          { status: 201 },
        );
      }
      return Response.json({ repositories: [] });
    });
    const onSelect = vi.fn();
    const modes = makeModes();
    modes.getState().enter(
      repoPickerMode(modes, {
        daemonUrl: 'http://127.0.0.1:62077',
        onSelect,
        fetchImpl,
      }),
    );
    await vi.waitFor(() => expect(fetchImpl).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 0));

    const mode = modes.getState().stack.find((f) => f.mode.id === REPO_PICKER_MODE_ID)?.mode;
    expect(mode).toBeDefined();
    mode?.onIntent('toggleInit');
    expect(mode?.onUncaptured?.('/var/lib/rj', makeKey())).toBe(true);
    mode?.onIntent('select');
    await vi.waitFor(() => expect(onSelect).toHaveBeenCalled());
    expect(fetchImpl).toHaveBeenCalledWith(
      'http://127.0.0.1:62077/api/repos/init',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ path: '/var/lib/rj', force: false }),
      }),
    );
    expect(onSelect).toHaveBeenCalledWith(
      expect.objectContaining({ repository_id: 'new', root_path: '/var/lib/rj' }),
    );
  });

  it('does not select after dismiss during in-flight init', async () => {
    let resolveInit: ((value: Response) => void) | undefined;
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/api/repos') && (init?.method === undefined || init.method === 'GET')) {
        return Response.json({ repositories: [] });
      }
      return await new Promise<Response>((resolve) => {
        resolveInit = resolve;
      });
    });
    const onSelect = vi.fn();
    const modes = makeModes();
    modes.getState().enter(
      repoPickerMode(modes, {
        daemonUrl: 'http://127.0.0.1:62077',
        onSelect,
        fetchImpl,
      }),
    );
    await vi.waitFor(() => expect(fetchImpl).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 0));

    const mode = modes.getState().stack.find((f) => f.mode.id === REPO_PICKER_MODE_ID)?.mode;
    expect(mode).toBeDefined();
    mode?.onIntent('toggleInit');
    mode?.onUncaptured?.('/tmp/new-repo', makeKey());
    mode?.onIntent('select');

    await vi.waitFor(() => expect(resolveInit).toBeDefined());
    mode?.onIntent('dismiss');
    expect(modes.getState().stack).toHaveLength(0);

    resolveInit?.(
      Response.json(
        {
          repository_id: 'new',
          root_path: '/tmp/new-repo',
          created_at: '2026-01-01T00:00:00Z',
          last_seen_at: '2026-01-01T00:00:00Z',
        },
        { status: 201 },
      ),
    );
    await new Promise((r) => setTimeout(r, 0));
    expect(onSelect).not.toHaveBeenCalled();
  });
});
