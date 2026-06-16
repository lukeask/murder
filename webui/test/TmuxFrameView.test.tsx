/**
 * TmuxFrameView: subscribes to the bus's `tmux.frame` stream and renders the ANSI snapshot as HTML.
 * We emit a frame with an SGR color code through the FakeBusClient and assert the converter produced
 * colored markup (a <span style> with a color) and that the empty state shows before any frame.
 */

import { FakeBusClient } from '@core/bus/FakeBusClient.js';
import { screen, cleanup } from '@testing-library/react';
import { act } from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { TmuxFrameView } from '../src/components/stage/TmuxFrameView.js';
import { renderWithStore } from './helpers.js';

afterEach(cleanup);

describe('TmuxFrameView', () => {
  it('shows a waiting placeholder before any frame arrives', () => {
    renderWithStore(<TmuxFrameView agentId="a1" />);
    expect(screen.getByText(/Waiting for the agent/)).toBeTruthy();
  });

  it('renders an ANSI frame as colored HTML on a tmux.frame event', () => {
    const bus = new FakeBusClient();
    renderWithStore(<TmuxFrameView agentId="a1" />, { bus });
    // Red foreground "hi" then reset.
    act(() => {
      bus.emit({ type: 'tmux.frame', frame: '[31mhi[0m', agent_id: 'a1' } as never);
    });
    const pre = document.querySelector('.mds-tmux__frame');
    expect(pre).not.toBeNull();
    expect(pre?.innerHTML).toContain('hi');
    // ansi-to-html emits a styled span for the color code.
    expect(pre?.innerHTML.toLowerCase()).toContain('style');
  });

  it('ignores frames for a different agent (filter scope)', () => {
    const bus = new FakeBusClient();
    renderWithStore(<TmuxFrameView agentId="a1" />, { bus });
    act(() => {
      bus.emit({ type: 'tmux.frame', frame: 'other', agent_id: 'b2' } as never);
    });
    // Still waiting — the subscription filter scoped to a1 didn't match b2.
    expect(screen.getByText(/Waiting for the agent/)).toBeTruthy();
  });
});
