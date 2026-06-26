import { mkdtemp, readdir, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

// biome-ignore lint/suspicious/noControlCharactersInRegex: tests assert ANSI SGR bytes are preserved.
const ANSI_SGR_RE = /\x1b\[[0-9;]*m/g;
// biome-ignore lint/suspicious/noControlCharactersInRegex: tests strip ANSI for display width checks.
const ANSI_CSI_RE = /\x1b\[[0-?]*[ -/]*[@-~]/g;

let tempDir: string;

function stripAnsi(text: string): string {
  return text.replace(ANSI_CSI_RE, '');
}

function lineWidths(text: string): number[] {
  return text.split('\n').map((line) => Array.from(stripAnsi(line)).length);
}

beforeEach(async () => {
  // biome-ignore lint/complexity/useLiteralKeys: noPropertyAccessFromIndexSignature requires bracket access.
  process.env['FORCE_COLOR'] = '3';
  tempDir = await mkdtemp(path.join(tmpdir(), 'inktui-fixtures-'));
});

afterEach(async () => {
  await rm(tempDir, { recursive: true, force: true });
});

describe('renderInkFixture tooling', () => {
  it('renders a component at the exact requested dimensions', async () => {
    const [{ paneFixtures }, { renderInkFixture }] = await Promise.all([
      import('../../fixtures/registry.js'),
      import('../../fixtures/renderInkFixture.js'),
    ]);
    const fixture = paneFixtures.find((item) => item.id === 'pane');
    if (fixture === undefined) {
      throw new Error('missing pane fixture');
    }
    const rendered = await renderInkFixture({
      fixture,
      dataId: 'basic',
      width: 30,
      height: 8,
      focused: true,
    });
    expect(rendered.ansi.split('\n')).toHaveLength(8);
    expect(lineWidths(rendered.ansi)).toEqual(Array.from({ length: 8 }, () => 30));
  });

  it('preserves ANSI SGR escapes in .txt output', async () => {
    const [{ paneFixtures }, { renderOneRegisteredFixture }] = await Promise.all([
      import('../../fixtures/registry.js'),
      import('../../fixtures/renderInkFixture.js'),
    ]);
    const written = await renderOneRegisteredFixture({
      fixtures: paneFixtures,
      fixtureId: 'pane',
      dataId: 'basic',
      width: 30,
      height: 8,
      focused: true,
      outputDir: tempDir,
    });
    const text = await readFile(written.path, 'utf8');
    expect(text).toMatch(ANSI_SGR_RE);
  });

  it('rejects output exceeding requested width or height', async () => {
    const { assertSnapshotWithinDimensions } = await import('../../fixtures/renderInkFixture.js');
    expect(() => assertSnapshotWithinDimensions('123456', 5, 1)).toThrow(
      /exceeding requested width/,
    );
    expect(() => assertSnapshotWithinDimensions('ok\nok', 5, 1)).toThrow(
      /exceeding requested height/,
    );
  });

  it('can regenerate all registered fixtures', async () => {
    const [{ paneFixtures }, { renderAllRegisteredFixtures }] = await Promise.all([
      import('../../fixtures/registry.js'),
      import('../../fixtures/renderInkFixture.js'),
    ]);
    const written = await renderAllRegisteredFixtures({
      fixtures: paneFixtures,
      outputDir: tempDir,
    });
    expect(written.length).toBeGreaterThan(0);
    const files = await readdir(tempDir);
    expect(files).toContain('manifest.json');
    expect(files.filter((file) => file.endsWith('.txt')).length).toBe(written.length);
  }, 20_000);

  it('can render one selected fixture only', async () => {
    const [{ paneFixtures }, { renderOneRegisteredFixture }] = await Promise.all([
      import('../../fixtures/registry.js'),
      import('../../fixtures/renderInkFixture.js'),
    ]);
    const written = await renderOneRegisteredFixture({
      fixtures: paneFixtures,
      fixtureId: 'text-input',
      dataId: 'filled',
      width: 30,
      height: 3,
      focused: false,
      outputDir: tempDir,
    });
    expect(path.basename(written.path)).toContain('text-input__filled__blurred__custom-30x3.txt');
    const files = await readdir(tempDir);
    expect(files.filter((file) => file.endsWith('.txt'))).toEqual([path.basename(written.path)]);
  });
});
