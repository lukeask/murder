/**
 * `RepoPickerModal` — in-TUI repository switcher (`alt+e` / `ctrl+e`). Fetches recent repos from
 * the daemon (`GET {MURDER_DAEMON_URL}/api/repos`), lets the user pick one, then the shell remounts
 * a fresh `ApplicationWebSocketClient` + store against `/api/ws/{repository_id}`.
 *
 * Same mode-factory idiom as {@link ./HelpOverlay.js} / {@link ./NewPlanModal.js}: mutable closure
 * state, `presentation: 'modal'`, bottom-bar hints.
 */

import { Box, Text } from 'ink';
import type { JSX } from 'react';
import { useModalHeight, useModalWidth } from '../hooks/useTerminalSize.js';
import type { Mode, ModeStoreApi } from '../input/modeStore.js';
import {
  fetchRecentRepos,
  initializeRepository,
  repoDisplayName,
  type RepoListEntry,
} from '../application/reposApi.js';
import { useTheme } from '@murder/ui-core/theme/themeStore.js';

import '@murder/ui-core/input/dispatcher.js';

export const REPO_PICKER_MODE_ID = 'repo-picker';

export interface RepoPickerModeOptions {
  /** Daemon HTTP base (`http://127.0.0.1:62077`). */
  readonly daemonUrl: string;
  /** Currently connected repository id (highlight + skip no-op select). */
  readonly activeRepositoryId?: string | undefined;
  /** Called after the mode exits with the chosen repo. */
  readonly onSelect: (repo: RepoListEntry) => void;
  readonly onDismiss?: () => void;
  readonly fetchImpl?: typeof fetch;
}

type RepoPickerIntent =
  | 'up'
  | 'down'
  | 'select'
  | 'retry'
  | 'toggleInit'
  | 'backspace'
  | 'dismiss';

interface RepoPickerState {
  repositories: readonly RepoListEntry[];
  loading: boolean;
  loadError: string | null;
  cursor: number;
  /** When true, the init-path field is focused instead of the list. */
  initMode: boolean;
  initPath: string;
  initBusy: boolean;
  initError: string | null;
}

/**
 * Build the repo-picker {@link Mode}. Enter via
 * `modes.getState().enter(repoPickerMode(modes, opts))`.
 */
export function repoPickerMode(
  modes: ModeStoreApi,
  opts: RepoPickerModeOptions,
): Mode<RepoPickerIntent> {
  const id = REPO_PICKER_MODE_ID;
  const fetchImpl = opts.fetchImpl ?? globalThis.fetch;

  const s: RepoPickerState = {
    repositories: [],
    loading: true,
    loadError: null,
    cursor: 0,
    initMode: false,
    initPath: '',
    initBusy: false,
    initError: null,
  };

  /** True while this exact mode instance is still on the stack (not a later picker with the same id). */
  function isCurrent(): boolean {
    return modes.getState().stack.some((f) => f.mode === mode);
  }

  function refresh(): void {
    // Async loads belong to this exact instance — an older dismissed picker must not re-enter a
    // newer one that shares `repo-picker` id (SpawnWizardModal pattern).
    if (!isCurrent()) return;
    modes.getState().enter(mode);
  }

  function dismiss(): void {
    if (!isCurrent()) return;
    modes.getState().exit(id);
    opts.onDismiss?.();
  }

  function load(): void {
    s.loading = true;
    s.loadError = null;
    refresh();
    void fetchRecentRepos(opts.daemonUrl, fetchImpl)
      .then((rows) => {
        s.repositories = rows;
        s.loading = false;
        const activeIdx =
          opts.activeRepositoryId === undefined
            ? -1
            : rows.findIndex((r) => r.repository_id === opts.activeRepositoryId);
        s.cursor = activeIdx >= 0 ? activeIdx : 0;
        refresh();
      })
      .catch((error: unknown) => {
        s.loading = false;
        s.loadError = error instanceof Error ? error.message : String(error);
        refresh();
      });
  }

  function move(delta: number): void {
    if (s.initMode || s.repositories.length === 0) return;
    const len = s.repositories.length;
    s.cursor = (((s.cursor + delta) % len) + len) % len;
    refresh();
  }

  function selectCurrent(): void {
    if (s.initMode) {
      submitInit();
      return;
    }
    const repo = s.repositories[s.cursor];
    if (repo === undefined) return;
    if (
      opts.activeRepositoryId !== undefined &&
      repo.repository_id === opts.activeRepositoryId
    ) {
      dismiss();
      return;
    }
    if (!isCurrent()) return;
    modes.getState().exit(id);
    opts.onSelect(repo);
  }

  function submitInit(): void {
    if (s.initBusy) return;
    const trimmed = s.initPath.trim();
    if (trimmed === '') {
      s.initError = 'Enter a filesystem path';
      refresh();
      return;
    }
    s.initBusy = true;
    s.initError = null;
    refresh();
    void initializeRepository(opts.daemonUrl, trimmed, { fetchImpl })
      .then((created) => {
        // Dismiss during init must not remount the live session onto the new repo.
        if (!isCurrent()) return;
        const entry: RepoListEntry = {
          repository_id: created.repository_id,
          root_path: created.root_path,
          created_at: created.created_at,
          last_seen_at: created.last_seen_at,
          active: false,
        };
        modes.getState().exit(id);
        opts.onSelect(entry);
      })
      .catch((error: unknown) => {
        s.initBusy = false;
        s.initError = error instanceof Error ? error.message : String(error);
        refresh();
      });
  }

  const mode: Mode<RepoPickerIntent> = {
    id,
    presentation: 'modal',
    get hints() {
      if (s.initMode) {
        return [
          { key: 'enter', description: 'initialize' },
          { key: 'tab', description: 'list' },
          { key: 'esc', description: 'cancel' },
        ];
      }
      return [
        { key: 'j/k', description: 'move' },
        { key: 'enter', description: 'open' },
        { key: 'tab', description: 'init' },
        { key: 'r', description: 'retry' },
        { key: 'esc', description: 'cancel' },
      ];
    },
    keymap: [
      {
        chord: [{ input: 'k' }, { key: { upArrow: true } }],
        intent: 'up',
        description: 'prev',
      },
      {
        chord: [{ input: 'j' }, { key: { downArrow: true } }],
        intent: 'down',
        description: 'next',
      },
      { chord: { key: { return: true } }, intent: 'select', description: 'open' },
      { chord: { input: 'r' }, intent: 'retry', description: 'retry' },
      { chord: { key: { tab: true } }, intent: 'toggleInit', description: 'toggle init' },
      { chord: { key: { backspace: true } }, intent: 'backspace', description: 'delete' },
      { chord: { key: { escape: true } }, intent: 'dismiss', description: 'cancel' },
    ],
    onIntent(intent) {
      switch (intent) {
        case 'up':
          move(-1);
          break;
        case 'down':
          move(1);
          break;
        case 'select':
          selectCurrent();
          break;
        case 'retry':
          if (!s.initMode) load();
          break;
        case 'toggleInit':
          s.initMode = !s.initMode;
          s.initError = null;
          refresh();
          break;
        case 'backspace':
          if (s.initMode && !s.initBusy) {
            s.initPath = s.initPath.slice(0, -1);
            s.initError = null;
            refresh();
          }
          break;
        case 'dismiss':
          dismiss();
          break;
        default:
          return intent satisfies never;
      }
    },
    onUncaptured(input, key) {
      if (!s.initMode || s.initBusy) return false;
      if (key.ctrl || key.meta || key.return || key.escape || key.tab) return false;
      if (input.length === 0) return false;
      // Printable path characters.
      if (/^[\x20-\x7e]+$/.test(input)) {
        s.initPath += input;
        s.initError = null;
        refresh();
        return true;
      }
      return false;
    },
    render: () => <RepoPickerDialog state={s} activeId={opts.activeRepositoryId} />,
  };

  // Kick off the list fetch after `mode` exists so `isCurrent()` can resolve.
  load();

  return mode;
}

function RepoPickerDialog({
  state: s,
  activeId,
}: {
  readonly state: RepoPickerState;
  readonly activeId: string | undefined;
}): JSX.Element {
  const theme = useTheme();
  const width = useModalWidth(64);
  const height = useModalHeight(0.7);
  const listBudget = Math.max(3, height - 8);

  return (
    <Box
      flexDirection="column"
      borderStyle="round"
      borderColor={theme.heading}
      paddingX={2}
      paddingY={1}
      width={width}
      height={height}
    >
      <Text bold color={theme.heading}>
        Switch repository
      </Text>
      <Text color={theme.muted}>Choose a recent murder repository</Text>

      {s.initMode ? (
        <Box flexDirection="column" marginTop={1}>
          <Text bold color={theme.accent}>
            Initialize new
          </Text>
          <Text color={theme.text}>
            Path: {s.initPath}
            <Text color={theme.focus}>█</Text>
          </Text>
          {s.initError !== null ? <Text color={theme.error}>{s.initError}</Text> : null}
          {s.initBusy ? <Text color={theme.muted}>Initializing…</Text> : null}
        </Box>
      ) : (
        <Box flexDirection="column" marginTop={1} height={listBudget} overflow="hidden">
          {s.loading ? (
            <Text color={theme.muted}>Loading…</Text>
          ) : s.loadError !== null ? (
            <Text color={theme.error}>{s.loadError}</Text>
          ) : s.repositories.length === 0 ? (
            <Text color={theme.muted}>No repositories yet — press tab to initialize.</Text>
          ) : (
            (() => {
              const windowStart = Math.max(
                0,
                Math.min(s.cursor - Math.floor(listBudget / 2), s.repositories.length - listBudget),
              );
              return s.repositories.slice(windowStart, windowStart + listBudget).map((repo, i) => {
                const index = windowStart + i;
                const selected = index === s.cursor;
                const isActive = repo.repository_id === activeId;
                const name = repoDisplayName(repo.root_path);
                const mark = selected ? '▸' : ' ';
                const suffix = isActive ? ' (active)' : repo.active ? ' · live' : '';
                return (
                  <Text
                    key={repo.repository_id}
                    color={selected ? theme.focus : theme.text}
                    bold={selected}
                  >
                    {mark} {name}
                    <Text color={theme.muted}>{suffix}</Text>
                  </Text>
                );
              });
            })()
          )}
        </Box>
      )}

      {!s.initMode && s.repositories[s.cursor] !== undefined ? (
        <Box marginTop={1}>
          <Text color={theme.muted} wrap="truncate">
            {s.repositories[s.cursor]?.root_path}
          </Text>
        </Box>
      ) : null}
    </Box>
  );
}
