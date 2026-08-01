/**
 * RosterPanel — live agent roster (collaborator / planners / rogue / ticket) with health and
 * favorites. Click selects the crow as the active chat target; ★ toggles favorite; ticket-bound
 * crows can be reset (with confirm). Keyboard: j/k/Enter when panel focused; f star; m murder.
 */

import { selectCrowsView } from '@murder/ui-core/selectors/crowsSelectors.js';
import type { CrowGroup } from '@murder/ui-core/selectors/crowsSelectors.js';
import type { Health } from '@murder/ui-core/selectors/crowHealthSelectors.js';
import { useAppStore } from '@murder/ui-core/hooks/useAppStore.js';
import { murderConfirmStore } from '@murder/ui-core/store/murder/murderConfirmStore.js';
import { toastStore } from '@murder/ui-core/store/toast/toastStore.js';
import { shallow } from 'zustand/shallow';
import { useCallback, useMemo, useState } from 'react';
import { useCreationDialogs } from '../../creationDialogs.js';
import { panelFocusStore, useIsPanelFocused } from '../../panelFocus.js';
import { usePanelListKeys } from '../../usePanelListKeys.js';
import {
  Panel,
  ListRow,
  StatusDot,
  Avatar,
  Tag,
  IconButton,
  Icon,
  Button,
  Dialog,
  cx,
} from '../ds/index.js';
import type { StatusDotStatus } from '../ds/index.js';
import { SliceHint } from '../SliceHint.js';

/** Map selector crow health onto a DS StatusDot status. */
const HEALTH_TO_DOT: Readonly<Record<Health, StatusDotStatus>> = {
  green: 'running',
  yellow: 'pending',
  red: 'failed',
  neutral: 'idle',
};

interface ResetTarget {
  readonly ticketId: string;
  readonly name: string;
}

export function RosterPanel(): React.JSX.Element {
  const roster = useAppStore((s) => s.roster, shallow);
  const favorites = useAppStore((s) => s.favorites, shallow);
  const rosterRows = useAppStore((s) => s.roster.rows);
  const toggleFavorite = useAppStore((s) => s.actions.favorites.toggle);
  const resetCrow = useAppStore((s) => s.actions.roster.resetCrow);
  const setActivePane = useAppStore((s) => s.actions.conversations.setActivePaneAgentId);
  const setTranscriptPaneOpen = useAppStore((s) => s.actions.conversations.setTranscriptPaneOpen);
  const activeAgentId = useAppStore((s) => s.conversations.activePaneAgentId);
  const { openSpawn } = useCreationDialogs();
  const [resetTarget, setResetTarget] = useState<ResetTarget | null>(null);
  const [resetPending, setResetPending] = useState(false);
  const [collapsedSections, setCollapsedSections] = useState<ReadonlySet<CrowGroup>>(
    () => new Set(),
  );
  const focused = useIsPanelFocused('crows');
  const [cursor, setCursor] = useState(0);

  const view = selectCrowsView(roster, Date.now(), favorites);

  const flatRows = useMemo(
    () =>
      view.sections.flatMap((section) =>
        collapsedSections.has(section.group) ? [] : section.rows,
      ),
    [view.sections, collapsedSections],
  );

  const ticketIdFor = (agentId: string): string | null =>
    rosterRows.find((r) => r.agentId === agentId)?.ticketId ?? null;

  const rowCount = view.sections.reduce((n, s) => n + s.rows.length, 0);

  const openCrow = useCallback(
    (agentId: string): void => {
      setActivePane(agentId);
      setTranscriptPaneOpen(agentId, true);
    },
    [setActivePane, setTranscriptPaneOpen],
  );

  const confirmReset = (): void => {
    if (resetTarget === null || resetPending) return;
    setResetPending(true);
    void resetCrow(resetTarget.ticketId)
      .then(() => {
        setResetTarget(null);
        toastStore.getState().push(`reset ${resetTarget.name}`, { ttlMs: 6000 });
      })
      .catch((err: unknown) => {
        const message = err instanceof Error ? err.message : String(err);
        toastStore.getState().push(message, { severity: 'error', ttlMs: 12000 });
      })
      .finally(() => {
        setResetPending(false);
      });
  };

  const toggleSection = (group: CrowGroup): void => {
    setCollapsedSections((prev) => {
      const next = new Set(prev);
      if (next.has(group)) next.delete(group);
      else next.add(group);
      return next;
    });
  };

  usePanelListKeys({
    active: focused,
    itemCount: flatRows.length,
    cursor,
    setCursor,
    onActivate: () => {
      const row = flatRows[cursor];
      if (row !== undefined) openCrow(row.agentId);
    },
    onAction: (key) => {
      const row = flatRows[cursor];
      if (row === undefined) return false;
      if (key === 'f') {
        void toggleFavorite(row.agentId);
        return true;
      }
      if (key === 'm' || key === 'x') {
        murderConfirmStore.getState().arm({ agentId: row.agentId, name: row.name });
        return true;
      }
      return false;
    },
  });

  return (
    <>
      <Panel
        title="crows"
        count={view.isEmpty ? null : rowCount}
        flush
        active={focused}
        data-panel-id="crows"
        onHeaderClick={() => panelFocusStore.getState().focus('crows')}
        actions={
          <IconButton label="Spawn rogue" onClick={openSpawn}>
            <Icon name="plus" size={14} />
          </IconButton>
        }
      >
        <SliceHint state={view} empty="No agents." />
        {view.sections.map((section) => {
          const collapsed = collapsedSections.has(section.group);
          return (
            <div key={section.group} className="roster-section">
              <button
                type="button"
                className={cx(
                  'roster-section__label',
                  collapsed && 'roster-section__label--collapsed',
                )}
                aria-expanded={!collapsed}
                onClick={() => toggleSection(section.group)}
              >
                <Icon
                  name="chevron-down"
                  size={12}
                  className={cx(
                    'roster-section__chevron',
                    collapsed && 'roster-section__chevron--collapsed',
                  )}
                />
                {section.label}
                <span className="roster-section__count">{section.rows.length}</span>
              </button>
              {collapsed
                ? null
                : section.rows.map((row) => {
                    const ticketId = ticketIdFor(row.agentId);
                    const flatIndex = flatRows.findIndex((r) => r.agentId === row.agentId);
                    const cursorHere = focused && flatIndex === cursor;
                    return (
                      <ListRow
                        key={row.agentId}
                        selected={cursorHere || (!focused && row.agentId === activeAgentId)}
                        starred={row.favorited}
                        onPinToggle={() => void toggleFavorite(row.agentId)}
                        onClick={() => {
                          panelFocusStore.getState().focus('crows');
                          if (flatIndex >= 0) setCursor(flatIndex);
                          openCrow(row.agentId);
                        }}
                        title={
                          <span className="roster-name">
                            <Avatar size="sm" name={row.name} />
                            {row.name}
                          </span>
                        }
                        meta={
                          <span className="roster-meta">
                            <Tag>{row.harness}</Tag>
                            <span>{row.model}</span>
                          </span>
                        }
                        trailing={
                          <span className="roster-trail">
                            <StatusDot
                              status={HEALTH_TO_DOT[row.health]}
                              pulse
                              label={row.status}
                            />
                            <IconButton
                              size="sm"
                              label={`Murder ${row.name}`}
                              onClick={(e) => {
                                e.stopPropagation();
                                murderConfirmStore.getState().arm({
                                  agentId: row.agentId,
                                  name: row.name,
                                });
                              }}
                            >
                              <Icon name="x" size={14} />
                            </IconButton>
                            {ticketId !== null ? (
                              <IconButton
                                size="sm"
                                label={`Reset ${row.name}`}
                                onClick={(e) => {
                                  e.stopPropagation();
                                  setResetTarget({ ticketId, name: row.name });
                                }}
                              >
                                <Icon name="back" size={14} />
                              </IconButton>
                            ) : null}
                          </span>
                        }
                      />
                    );
                  })}
            </div>
          );
        })}
      </Panel>

      {resetTarget !== null ? (
        <Dialog
          open
          title="Reset crow"
          onClose={() => {
            if (!resetPending) setResetTarget(null);
          }}
          footer={
            <>
              <Button
                variant="ghost"
                disabled={resetPending}
                onClick={() => setResetTarget(null)}
              >
                Cancel
              </Button>
              <Button variant="danger" disabled={resetPending} onClick={confirmReset}>
                {resetPending ? 'Resetting…' : 'Reset'}
              </Button>
            </>
          }
        >
          <p>
            Reset <strong>{resetTarget.name}</strong>? The crow is killed and its ticket re-queued
            as ready.
          </p>
        </Dialog>
      ) : null}
    </>
  );
}
