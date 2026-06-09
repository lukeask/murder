#!/usr/bin/env tsx
/**
 * Minimal Ink rendering probe — NONE of the murder app, just Ink itself. Renders a bordered box of
 * numbered lines and holds until ctrl+c. Use it to tell whether the "blank line every other line" /
 * "too tall" artifact is Ink-meets-this-terminal (probe reproduces it) or something in our app
 * (probe is clean but `murder` is not).
 *
 * Flags (combine freely):
 *   --alt      render on the alternate screen buffer (what the live app uses)
 *   --height   clamp the outer box to the terminal rows + overflow:hidden (the app's height clamp)
 *   --fill     make the content fill the whole screen height (the app is full-height; 15 lines isn't)
 *   --tick     re-render every 400ms with a changing counter (the app repaints constantly on live
 *              data — a STATIC frame can be clean while a repainting full-height frame is not)
 *
 * The combo that matches the live app most closely:
 *   node_modules/.bin/tsx tools/probe.tsx --alt --height --fill --tick
 *
 * Report: blank line between every content line? taller than the screen? does it only go wrong once
 * the --tick counter starts moving? The cyan header prints rows/cols/TERM/flags.
 */

import process from 'node:process';
import { Box, render, Text, useApp, useInput, useStdin, useStdout } from 'ink';
import React, { useEffect, useState } from 'react';

const argv = process.argv.slice(2);
const useAlt = argv.includes('--alt');
const clampHeight = argv.includes('--height');
const fill = argv.includes('--fill');
const tick = argv.includes('--tick');
// --wide: make the box span the FULL terminal width (like the app's panels), so its border lines hit
// the last column. Suspected trigger: a line exactly `cols` wide → terminal deferred-wrap → Ink's
// `\n` yields a phantom blank line. The content-width default box never hits the last column.
const wide = argv.includes('--wide');
// --overflow: render FAR more lines than fit, so a height-clamped overflow:hidden box must actually
// CLIP them. The app's lists do this (40+ entries in a ~60-row box); no other flag exercises the
// vertical-clip path — `--fill` renders exactly what fits. Use with `--height` (and `--alt`).
const overflow = argv.includes('--overflow');
// --noshrink: set flexShrink={0} on the overflowing lines so Yoga can't squeeze 72 lines into 20 rows
// (which drops/samples most of them — the "skipped line" bug). With it, lines keep height 1 and the
// clip shows a contiguous TOP slice. This is the candidate fix for the app's lists.
const noshrink = argv.includes('--noshrink');

function Probe(): React.JSX.Element {
  const { stdout } = useStdout();
  const { isRawModeSupported } = useStdin();
  const { exit } = useApp();
  const [counter, setCounter] = useState(0);
  useEffect(() => {
    if (!tick) {
      return;
    }
    const id = setInterval(() => setCounter((c) => c + 1), 400);
    return () => clearInterval(id);
  }, []);
  useInput(
    (input, key) => {
      if (key.escape || (key.ctrl && input === 'c')) {
        exit();
      }
    },
    { isActive: isRawModeSupported === true },
  );
  const rows = stdout.rows ?? 24;
  const cols = stdout.columns ?? 80;
  // --overflow forces real clipping (3× the screen); --fill fits exactly; else a small 15.
  const lineCount = overflow ? rows * 3 : fill ? Math.max(1, rows - 4) : 15;
  const lines = Array.from({ length: lineCount }, (_v, i) => i);
  return (
    <Box
      flexDirection="column"
      {...(clampHeight ? { height: rows, overflow: 'hidden' as const } : {})}
    >
      <Text color="cyan">
        {`probe rows=${rows} cols=${cols} TERM=${process.env['TERM'] ?? '?'} alt=${useAlt} clamp=${clampHeight} fill=${fill} overflow=${overflow} tick=${tick} wide=${wide} counter=${counter}`}
      </Text>
      <Box
        flexDirection="column"
        borderStyle="round"
        paddingX={1}
        flexGrow={clampHeight ? 1 : 0}
        {...(wide ? { width: cols } : {})}
      >
        {lines.map((i) => (
          <Box key={i} {...(noshrink ? { flexShrink: 0 } : {})}>
            <Text>{`line ${String(i).padStart(2, '0')} — quick brown fox [${counter}]`}</Text>
          </Box>
        ))}
      </Box>
      <Text dimColor>ctrl+c or esc to exit</Text>
    </Box>
  );
}

render(<Probe />, useAlt ? { alternateScreen: true } : {});
