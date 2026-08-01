/**
 * Stick-to-bottom + web goto-line helpers used by ChatTranscript / DocViewer.
 */

import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { act, useCallback, useRef } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useStickToBottom } from '../src/components/stage/useStickToBottom.js';
import { useWebGotoLine } from '../src/components/stage/useWebGotoLine.js';
import { GotoLineOverlay } from '../src/components/stage/GotoLineOverlay.js';
import { ChatTranscript } from '../src/components/stage/ChatTranscript.js';
import { makeStore, renderWithStore, seedSlice } from './helpers.js';

afterEach(cleanup);

const AID = 'crowzero';

function StickProbe({
  contentKey,
  nearPx = 48,
}: {
  readonly contentKey: number;
  readonly nearPx?: number;
}): React.JSX.Element {
  const ref = useRef<HTMLDivElement>(null);
  const { onScroll } = useStickToBottom(ref, contentKey, nearPx);
  return (
    <div
      ref={ref}
      data-testid="scroller"
      onScroll={onScroll}
      style={{ height: 40, overflow: 'auto' }}
    >
      <div style={{ height: 200 }} data-testid="tall" />
      <div data-testid="end">end-{contentKey}</div>
    </div>
  );
}

const gotoJump = vi.fn();

function GotoProbe({ enabled = true }: { readonly enabled?: boolean }): React.JSX.Element {
  const jump = useCallback((line: number) => {
    gotoJump(line);
  }, []);
  const goto = useWebGotoLine(jump, enabled);
  return <GotoLineOverlay pending={goto.pending} />;
}

describe('useStickToBottom', () => {
  it('scrolls to bottom when content grows and user was near bottom', () => {
    const { rerender } = render(<StickProbe contentKey={1} />);
    const scroller = screen.getByTestId('scroller');
    Object.defineProperty(scroller, 'scrollHeight', { configurable: true, get: () => 200 });
    Object.defineProperty(scroller, 'clientHeight', { configurable: true, get: () => 40 });
    Object.defineProperty(scroller, 'scrollTop', {
      configurable: true,
      get: () => (scroller as HTMLElement & { _top?: number })._top ?? 0,
      set: (v: number) => {
        (scroller as HTMLElement & { _top?: number })._top = v;
      },
    });
    // Near bottom.
    (scroller as HTMLElement & { _top?: number })._top = 150;
    act(() => {
      fireEvent.scroll(scroller);
    });
    rerender(<StickProbe contentKey={2} />);
    expect((scroller as HTMLElement & { _top?: number })._top).toBe(200);
  });

  it('does not force scroll when user has scrolled up', () => {
    const { rerender } = render(<StickProbe contentKey={1} />);
    const scroller = screen.getByTestId('scroller');
    Object.defineProperty(scroller, 'scrollHeight', { configurable: true, get: () => 200 });
    Object.defineProperty(scroller, 'clientHeight', { configurable: true, get: () => 40 });
    Object.defineProperty(scroller, 'scrollTop', {
      configurable: true,
      get: () => (scroller as HTMLElement & { _top?: number })._top ?? 0,
      set: (v: number) => {
        (scroller as HTMLElement & { _top?: number })._top = v;
      },
    });
    (scroller as HTMLElement & { _top?: number })._top = 10;
    act(() => {
      fireEvent.scroll(scroller);
    });
    rerender(<StickProbe contentKey={2} />);
    expect((scroller as HTMLElement & { _top?: number })._top).toBe(10);
  });
});

describe('useWebGotoLine', () => {
  beforeEach(() => {
    gotoJump.mockClear();
  });

  it('captures g then digits and jumps live', () => {
    render(<GotoProbe />);
    act(() => {
      fireEvent.keyDown(window, { key: 'g' });
    });
    expect(document.querySelector('.mds-goto')).toBeTruthy();
    act(() => {
      fireEvent.keyDown(window, { key: '3' });
    });
    expect(gotoJump).toHaveBeenCalledWith(3);
    expect(document.querySelector('.mds-goto__digits')?.textContent).toBe('3');
  });
});

describe('ChatTranscript follow + goto', () => {
  it('exposes turn line markers for goto', () => {
    const { store } = makeStore();
    seedSlice(store, 'conversations', {
      transcripts: {
        [AID]: [
          { id: '1', type: 'user', raw: { text: 'one' } },
          { id: '2', type: 'assistant', raw: { text: 'two' } },
        ],
      },
      pendingByAgent: {},
      meta: {},
      activePaneAgentId: AID,
      paneOverrides: new Map<string, boolean>(),
      paneReapAges: new Map<string, number>(),
      clearedFloors: {},
      paneViewModes: {},
      chunkSummaries: {},
    } as never);
    renderWithStore(<ChatTranscript agentId={AID} />, { store });
    expect(document.querySelector('[data-turn-line="1"]')).toBeTruthy();
    expect(document.querySelector('[data-turn-line="2"]')).toBeTruthy();
  });
});
