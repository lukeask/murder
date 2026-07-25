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

const SESSION = '0198b156-2dd3-70a9-bc79-fca001dc8801';

afterEach(cleanup);

describe('TmuxFrameView', () => {
  it('shows a waiting placeholder before any frame arrives', () => {
    renderWithStore(<TmuxFrameView sessionId={SESSION} />);
    expect(screen.getByText(/Waiting for the agent/)).toBeTruthy();
  });

  it('shows an empty state when sessionId is missing', () => {
    renderWithStore(<TmuxFrameView sessionId={null} />);
    expect(screen.getByText(/No terminal session/)).toBeTruthy();
  });

  it('renders an ANSI terminal replacement frame as colored HTML', () => {
    const bus = new FakeApplicationClient();
    renderWithStore(<TmuxFrameView sessionId={SESSION} />, { bus });
    // Red foreground "hi" then reset.
    act(() => {
      bus.emitTerminal(SESSION, '[31mhi[0m');
    });
    const pre = document.querySelector('.mds-tmux__frame');
    expect(pre).not.toBeNull();
    expect(pre?.innerHTML).toContain('hi');
    // ansi-to-html emits a styled span for the color code.
    expect(pre?.innerHTML.toLowerCase()).toContain('style');
  });

  it('ignores frames for a different session (filter scope)', () => {
    const bus = new FakeApplicationClient();
    renderWithStore(<TmuxFrameView sessionId={SESSION} />, { bus });
    act(() => {
      bus.emitTerminal('0198b156-2dd3-70a9-bc79-fca001dc8802', 'other');
    });
    // Still waiting — the terminal attachment is scoped to SESSION.
    expect(screen.getByText(/Waiting for the agent/)).toBeTruthy();
  });
});
