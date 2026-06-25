/**
 * `themeStore` recolor test — the whole point of the runtime store is that a `setTheme` on the
 * global singleton re-renders subscribed components with the new palette's colors. We render a tiny
 * component that paints `theme.error`, assert the dark-scheme red SGR, flip the store to the light
 * scheme, and assert the frame now carries the light-scheme red — proving the recolor propagates
 * through `useTheme()` without remounting.
 *
 * FORCE_COLOR / chalk level 3 is set before Ink renders so `lastFrame()` carries truecolor SGRs.
 */

import chalkModule from 'chalk';

// biome-ignore lint/suspicious/noExplicitAny: chalk's default-vs-namespace interop in ESM tests.
const chalk: { level: number } = (chalkModule as any).default ?? (chalkModule as any);
chalk.level = 3;

import { Text } from 'ink';
import { render } from 'ink-testing-library';
import type React from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { getPalette } from '../../src/theme/palettes.js';
import { setTheme, useTheme } from '../../src/theme/themeStore.js';

/** Truecolor foreground SGR for a `#rrggbb` hex. */
function fgSgr(hex: string): string {
  const r = Number.parseInt(hex.slice(1, 3), 16);
  const g = Number.parseInt(hex.slice(3, 5), 16);
  const b = Number.parseInt(hex.slice(5, 7), 16);
  return `\x1b[38;2;${r};${g};${b}m`;
}

function Probe(): React.JSX.Element {
  const theme = useTheme();
  return <Text color={theme.error}>boom</Text>;
}

async function tick(): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 20));
}

describe('themeStore — runtime recolor', () => {
  // Restore the default scheme so this test doesn't leak into the FORCE_COLOR-gated dark assertions.
  afterEach(() => setTheme('everforest-dark'));

  it('repaints a subscribed component when the scheme changes', async () => {
    const { lastFrame } = render(<Probe />);
    expect(lastFrame()).toContain(fgSgr(getPalette('everforest-dark')!.red));

    setTheme('everforest-light');
    await tick();

    expect(lastFrame()).toContain(fgSgr(getPalette('everforest-light')!.red));
    expect(lastFrame()).not.toContain(fgSgr(getPalette('everforest-dark')!.red));
  });
});
