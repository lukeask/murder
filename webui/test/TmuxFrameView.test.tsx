/**
 * TmuxFrameView: TerminalSurfaceStore ingestion + writer lease lifecycle.
 */

import { FakeApplicationClient } from '@murder/ui-core/application/FakeApplicationClient.js';
import { fireEvent, screen, cleanup } from '@testing-library/react';
import { act } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { TmuxFrameView } from '../src/components/stage/TmuxFrameView.js';
import { renderWithStore } from './helpers.js';

const SESSION = '0198b156-2dd3-70a9-bc79-fca001dc8801';

afterEach(cleanup);

async function flush(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

function emitReadyFrame(bus: FakeApplicationClient): void {
  bus.emitTerminal(SESSION, 'ready');
}

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
    act(() => {
      bus.emitTerminal(SESSION, '[31mhi[0m');
    });
    const pre = document.querySelector('.mds-tmux__frame');
    expect(pre).not.toBeNull();
    expect(pre?.textContent).toContain('hi');
    expect(pre?.innerHTML.toLowerCase()).toContain('style');
  });

  it('applies incremental chunks after a reset frame', () => {
    const bus = new FakeApplicationClient();
    renderWithStore(<TmuxFrameView sessionId={SESSION} />, { bus });
    act(() => {
      bus.emitTerminalUpdate(SESSION, {
        type: 'terminal.frame',
        subscription_id: 'fake-terminal',
        sequence: 1,
        session_id: SESSION,
        captured_at: new Date().toISOString(),
        columns: 20,
        rows: 4,
        encoding: 'utf-8',
        data: 'hello',
        reset: true,
      });
    });
    act(() => {
      bus.emitTerminalUpdate(SESSION, {
        type: 'terminal.chunk',
        sequence: 2,
        encoding: 'base64',
        data: Buffer.from(' world').toString('base64'),
      });
    });
    const pre = document.querySelector('.mds-tmux__frame');
    expect(pre?.textContent).toContain('hello world');
  });

  it('ignores frames for a different session (filter scope)', () => {
    const bus = new FakeApplicationClient();
    renderWithStore(<TmuxFrameView sessionId={SESSION} />, { bus });
    act(() => {
      bus.emitTerminal('0198b156-2dd3-70a9-bc79-fca001dc8802', 'other');
    });
    expect(screen.getByText(/Waiting for the agent/)).toBeTruthy();
  });

  it('acquires a writer lease on mount and releases on unmount', async () => {
    const bus = new FakeApplicationClient();
    const open = vi.spyOn(bus, 'openTerminalInput');
    const close = vi.spyOn(bus, 'closeTerminalInput');
    const { unmount } = renderWithStore(<TmuxFrameView sessionId={SESSION} />, { bus });
    await flush();
    expect(open).toHaveBeenCalledWith(SESSION);
    act(() => emitReadyFrame(bus));
    await flush();
    expect(document.querySelector('.mds-tmux')?.getAttribute('data-terminal-input')).toBe('true');
    unmount();
    await flush();
    expect(close).toHaveBeenCalled();
  });

  it('falls back to a muted read-only hint when the lease fails', async () => {
    const bus = new FakeApplicationClient();
    vi.spyOn(bus, 'openTerminalInput').mockRejectedValue(new Error('lease held elsewhere'));
    renderWithStore(<TmuxFrameView sessionId={SESSION} />, { bus });
    act(() => emitReadyFrame(bus));
    await flush();
    expect(screen.getByText(/read-only: lease held elsewhere/)).toBeTruthy();
    expect(document.querySelector('.mds-tmux')?.getAttribute('data-terminal-input')).toBeNull();
  });

  it('forwards keydown bytes through the lease writer', async () => {
    const bus = new FakeApplicationClient();
    renderWithStore(<TmuxFrameView sessionId={SESSION} />, { bus });
    await flush();
    act(() => emitReadyFrame(bus));
    await flush();
    const surface = document.querySelector('.mds-tmux');
    expect(surface).not.toBeNull();
    act(() => {
      (surface as HTMLElement).focus();
      fireEvent.keyDown(surface!, { key: 'a' });
    });
    await flush();
    expect(bus.terminalInputCalls.length).toBeGreaterThan(0);
    const payload = bus.terminalInputCalls[0]!;
    expect(Buffer.from(payload.data, 'base64').toString('utf8')).toBe('a');
  });
});
