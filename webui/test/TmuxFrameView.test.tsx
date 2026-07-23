/**
 * TmuxFrameView: attaches to a terminal stream and renders replacement ANSI frames as HTML.
 * We emit a frame with an SGR color code through the FakeApplicationClient and assert the converter produced
 * colored markup (a <span style> with a color) and that the empty state shows before any frame.
 */

import { FakeApplicationClient } from '@core/application/FakeApplicationClient.js';
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

  it('renders an ANSI terminal replacement frame as colored HTML', () => {
    const bus = new FakeApplicationClient();
    renderWithStore(<TmuxFrameView agentId="a1" />, { bus });
    // Red foreground "hi" then reset.
    act(() => {
      bus.emitTerminal('a1', '[31mhi[0m');
    });
    const pre = document.querySelector('.mds-tmux__frame');
    expect(pre).not.toBeNull();
    expect(pre?.innerHTML).toContain('hi');
    // ansi-to-html emits a styled span for the color code.
    expect(pre?.innerHTML.toLowerCase()).toContain('style');
  });

  it('ignores frames for a different agent (filter scope)', () => {
    const bus = new FakeApplicationClient();
    renderWithStore(<TmuxFrameView agentId="a1" />, { bus });
    act(() => {
      bus.emitTerminal('b2', 'other');
    });
    // Still waiting — the terminal attachment is scoped to a1.
    expect(screen.getByText(/Waiting for the agent/)).toBeTruthy();
  });
});
