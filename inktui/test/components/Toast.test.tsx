/**
 * `<Toast>` component test — the F9 rack renders the live toasts bottom-right, dim for `info` and
 * coloured for `error`, capped newest-on-top.
 *
 * The component reads the {@link toastStore} singleton, so each test clears it first (the singleton's
 * `clear()` cancels timers too — no cross-test leak). Asserting on the painted frame is sound because
 * the component is a pure function of the live toast set (rule 1).
 */

import chalkModule from 'chalk';

// Force colour on so Ink emits SGR codes in the non-TTY test renderer — otherwise `lastFrame()` is
// plain text and "info dim vs error red" can't be distinguished. Set before Ink renders (top of file).
// biome-ignore lint/suspicious/noExplicitAny: chalk's default-vs-namespace interop in ESM tests.
const chalk: { level: number } = (chalkModule as any).default ?? (chalkModule as any);
chalk.level = 3;

import { render } from 'ink-testing-library';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { Toast } from '../../src/components/Toast.js';
import { MAX_VISIBLE_TOASTS, toastStore } from '../../src/store/toast/toastStore.js';

// Error toasts paint in `theme.error` (everforest red #e67e80). At chalk level 3 (truecolor, set
// above) that emits this foreground SGR — the colour signal the assertions key on.
const ERROR_SGR = '\x1b[38;2;230;126;128m';

/** Let Ink flush a render. */
async function tick(): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 20));
}

beforeEach(() => {
  toastStore.getState().clear();
});
afterEach(() => {
  toastStore.getState().clear();
});

describe('<Toast>', () => {
  it('renders nothing when no toasts are live', async () => {
    const { lastFrame, unmount } = render(<Toast />);
    await tick();
    expect(lastFrame()).toBe('');
    unmount();
  });

  it('paints an info toast (dim, no colour code) bottom-right', async () => {
    toastStore.getState().push('→ crow-7', { ttlMs: 10_000 });
    const { lastFrame, unmount } = render(<Toast />);
    await tick();
    const frame = lastFrame() ?? '';
    expect(frame).toContain('→ crow-7');
    // info is dim — the dim SGR (2) is present, the error colour is not.
    expect(frame).toContain('\x1b[2m');
    expect(frame).not.toContain(ERROR_SGR);
    unmount();
  });

  it('paints an error toast in red', async () => {
    toastStore.getState().push('agent did not handle message', {
      severity: 'error',
      ttlMs: 10_000,
    });
    const { lastFrame, unmount } = render(<Toast />);
    await tick();
    const frame = lastFrame() ?? '';
    expect(frame).toContain('agent did not handle message');
    expect(frame).toContain(ERROR_SGR); // theme.error truecolor SGR
    unmount();
  });

  it('stacks newest-on-top and caps the visible count', async () => {
    // Push more than the cap; the newest should appear first, oldest beyond the cap dropped.
    for (let i = 1; i <= MAX_VISIBLE_TOASTS + 2; i++) {
      toastStore.getState().push(`m${i}`, { ttlMs: 10_000 });
    }
    const { lastFrame, unmount } = render(<Toast />);
    await tick();
    const frame = lastFrame() ?? '';
    const lines = frame.split('\n').filter((l) => l.trim().length > 0);
    expect(lines).toHaveLength(MAX_VISIBLE_TOASTS);
    // Newest (highest index) on top; the two oldest are dropped.
    const newest = MAX_VISIBLE_TOASTS + 2;
    expect(lines[0]).toContain(`m${newest}`);
    expect(frame).not.toContain('m1');
    unmount();
  });
});
