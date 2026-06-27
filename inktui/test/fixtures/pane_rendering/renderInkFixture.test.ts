import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { describe, expect, it } from 'vitest';

const execFileAsync = promisify(execFile);
const DEFAULT_TEST_LH = 20;
const DEFAULT_TEST_CW = 55;

// biome-ignore lint/suspicious/noControlCharactersInRegex: tests assert ANSI CSI bytes are preserved.
const ANSI_CSI_RE = /\x1b\[[0-?]*[ -/]*[@-~]/g;
// biome-ignore lint/suspicious/noControlCharactersInRegex: tests assert ANSI SGR bytes are preserved.
const ANSI_SGR_RE = /\x1b\[[0-9;]*m/g;

function stripAnsi(text: string): string {
  return text.replace(ANSI_CSI_RE, '');
}

function lineWidths(text: string): number[] {
  return text.split('\n').map((line) => Array.from(stripAnsi(line)).length);
}

describe('pane rendering fixtures', () => {
  it('renders one fixture at the exact requested allocation', async () => {
    process.env['FORCE_COLOR'] = '3';
    const [{ paneFixtures }, { renderInkFixture }] = await Promise.all([
      import('./registry.js'),
      import('./renderInkFixture.js'),
    ]);
    const fixture = paneFixtures.find((item) => item.id === 'plans-panel');
    if (fixture === undefined) {
      throw new Error('missing plans-panel fixture');
    }
    const rendered = await renderInkFixture({
      fixture,
      dataId: 'mixed',
      width: DEFAULT_TEST_CW,
      height: DEFAULT_TEST_LH,
      focused: true,
    });

    expect(rendered.ansi.split('\n')).toHaveLength(DEFAULT_TEST_LH);
    expect(lineWidths(rendered.ansi)).toEqual(
      Array.from({ length: DEFAULT_TEST_LH }, () => DEFAULT_TEST_CW),
    );
    expect(rendered.ansi).toMatch(ANSI_SGR_RE);
  });

  it('prints one selected fixture through the four positional CLI args', async () => {
    const env: NodeJS.ProcessEnv = { ...process.env, FORCE_COLOR: '3' };
    delete env['NO_COLOR'];
    const { stdout } = await execFileAsync(
      'python',
      [
        '-m',
        'tools.testing.render_pane_fixture',
        'plans',
        'mixed',
        String(DEFAULT_TEST_LH),
        String(DEFAULT_TEST_CW),
      ],
      {
        cwd: '..',
        env,
      },
    );
    const frame = stdout.endsWith('\n') ? stdout.slice(0, -1) : stdout;
    expect(frame).toContain('Plans');
    expect(frame.split('\n')).toHaveLength(DEFAULT_TEST_LH);
    expect(lineWidths(frame)).toEqual(
      Array.from({ length: DEFAULT_TEST_LH }, () => DEFAULT_TEST_CW),
    );
  });
});
