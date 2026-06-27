import { EventEmitter } from 'node:events';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import React from 'react';
import type { PaneFixture, PaneFixtureDataId, PaneFixtureId } from './types.js';

// biome-ignore lint/suspicious/noControlCharactersInRegex: ESC is the byte ANSI SGR uses.
const ANSI_SGR_RE = /\x1b\[[0-9;]*m/g;
// biome-ignore lint/suspicious/noControlCharactersInRegex: width checks must ignore all CSI escapes.
const ANSI_CSI_RE = /\x1b\[[0-?]*[ -/]*[@-~]/g;

export type FixtureFocusState = 'focused' | 'blurred';

export interface FixtureSize {
  readonly id: string;
  readonly width: number;
  readonly height: number;
}

export interface RenderInkFixtureRequest<Data = unknown> {
  readonly fixture: PaneFixture<Data>;
  readonly dataId: PaneFixtureDataId;
  readonly width: number;
  readonly height: number;
  readonly focused: boolean;
}

export interface RenderedInkFixture {
  readonly fixtureId: PaneFixtureId;
  readonly dataId: PaneFixtureDataId;
  readonly width: number;
  readonly height: number;
  readonly focused: boolean;
  readonly ansi: string;
}

export interface WrittenInkFixture extends RenderedInkFixture {
  readonly path: string;
}

export interface RenderAllFixtureOptions {
  readonly fixtures: readonly PaneFixture[];
  readonly outputDir: string;
  readonly stripAnsi?: boolean;
}

export interface RenderOneFixtureOptions {
  readonly fixtures: readonly PaneFixture[];
  readonly fixtureId: string;
  readonly dataId: string;
  readonly width: number;
  readonly height: number;
  readonly focused: boolean;
  readonly outputDir: string;
  readonly sizeId?: string;
  readonly stripAnsi?: boolean;
}

export function stripAnsiSgr(text: string): string {
  return text.replace(ANSI_SGR_RE, '');
}

function stripAnsiCsi(text: string): string {
  return text.replace(ANSI_CSI_RE, '');
}

function displayWidth(text: string): number {
  return Array.from(stripAnsiCsi(text)).length;
}

function focusLabel(focused: boolean): FixtureFocusState {
  return focused ? 'focused' : 'blurred';
}

function sanitizeId(id: string): string {
  return id.replace(/[^a-zA-Z0-9._-]+/g, '-');
}

export function fixtureSnapshotFilename({
  fixtureId,
  dataId,
  width,
  height,
  focused,
  sizeId = 'custom',
  suffix = '.txt',
}: {
  readonly fixtureId: string;
  readonly dataId: string;
  readonly width: number;
  readonly height: number;
  readonly focused: boolean;
  readonly sizeId?: string;
  readonly suffix?: string;
}): string {
  return (
    [
      sanitizeId(fixtureId),
      sanitizeId(dataId),
      focusLabel(focused),
      `${sanitizeId(sizeId)}-${width}x${height}`,
    ].join('__') + suffix
  );
}

export function assertSnapshotWithinDimensions(
  snapshot: string,
  width: number,
  height: number,
): void {
  const lines = snapshot.length === 0 ? [] : snapshot.split('\n');
  if (lines.length > height) {
    throw new Error(`fixture rendered ${lines.length} rows, exceeding requested height ${height}`);
  }
  for (const [index, line] of lines.entries()) {
    const actual = displayWidth(line);
    if (actual > width) {
      throw new Error(
        `fixture rendered row ${index + 1} at ${actual} columns, exceeding requested width ${width}`,
      );
    }
  }
}

export function normalizeSnapshotToDimensions(
  snapshot: string,
  width: number,
  height: number,
): string {
  assertSnapshotWithinDimensions(snapshot, width, height);
  const lines = snapshot.length === 0 ? [] : snapshot.split('\n');
  const padded = lines.map((line) => line + ' '.repeat(width - displayWidth(line)));
  while (padded.length < height) {
    padded.push(' '.repeat(width));
  }
  return padded.join('\n');
}

/**
 * ink-testing-library hardcodes a 100-column stdout, so bordered panes wider than that render their
 * body (incl. the scrollbar track) at ~101 cols and get space-padded to the requested width — the
 * top/bottom borders stretch but the right `│`/`┛` column floats mid-pane. Match stdout to the
 * fixture allocation instead (same pattern as TopBar.test's wide inkRender stub).
 */
function createFixtureStdout(
  width: number,
  height: number,
): NodeJS.WriteStream & {
  lastFrame: () => string | undefined;
} {
  const stub = new EventEmitter() as NodeJS.WriteStream & { lastFrame: () => string | undefined };
  let last: string | undefined;
  Object.assign(stub, {
    columns: width,
    rows: height,
    isTTY: false,
    write: (frame: string) => {
      last = frame;
      return true;
    },
    lastFrame: () => last,
  });
  return stub;
}

export async function renderInkFixture<Data = unknown>({
  fixture,
  dataId,
  width,
  height,
  focused,
}: RenderInkFixtureRequest<Data>): Promise<RenderedInkFixture> {
  if (width <= 0 || height <= 0) {
    throw new Error(`fixture dimensions must be positive, got ${width}x${height}`);
  }
  const data = fixture.data[dataId];
  if (data === undefined) {
    throw new Error(`unknown data id '${dataId}' for fixture '${fixture.id}'`);
  }

  // biome-ignore lint/complexity/useLiteralKeys: noPropertyAccessFromIndexSignature requires bracket access.
  process.env['FORCE_COLOR'] = process.env['FORCE_COLOR'] ?? '3';
  const { render: inkRender } = await import('ink');
  const { Box } = await import('ink');
  const tree = React.createElement(
    Box,
    { width, height },
    fixture.render({ data, width, height, focused }),
  );
  const stdout = createFixtureStdout(width, height);
  const instance = inkRender(tree, {
    stdout,
    stderr: stdout,
    stdin: new EventEmitter() as unknown as NodeJS.ReadStream,
    debug: true,
    exitOnCtrlC: false,
    patchConsole: false,
  });
  await new Promise((resolve) => setTimeout(resolve, 20));
  const frame = stdout.lastFrame() ?? '';
  instance.unmount();
  const ansi = normalizeSnapshotToDimensions(frame, width, height);
  return { fixtureId: fixture.id, dataId, width, height, focused, ansi };
}

export async function writeRenderedFixture({
  rendered,
  outputDir,
  sizeId,
  stripAnsi = false,
}: {
  readonly rendered: RenderedInkFixture;
  readonly outputDir: string;
  readonly sizeId?: string;
  readonly stripAnsi?: boolean;
}): Promise<WrittenInkFixture> {
  await mkdir(outputDir, { recursive: true });
  const filename = fixtureSnapshotFilename({
    ...rendered,
    ...(sizeId === undefined ? {} : { sizeId }),
  });
  const filePath = path.join(outputDir, filename);
  await writeFile(filePath, rendered.ansi, 'utf8');
  if (stripAnsi) {
    const plainPath = path.join(
      outputDir,
      fixtureSnapshotFilename({
        ...rendered,
        ...(sizeId === undefined ? {} : { sizeId }),
        suffix: '.plain.txt',
      }),
    );
    await writeFile(plainPath, stripAnsiSgr(rendered.ansi), 'utf8');
  }
  return { ...rendered, path: filePath };
}

function fixtureById(fixtures: readonly PaneFixture[], id: string): PaneFixture {
  const fixture = fixtures.find((candidate) => candidate.id === id);
  if (fixture === undefined) {
    throw new Error(`unknown fixture '${id}'`);
  }
  return fixture;
}

export async function renderOneRegisteredFixture({
  fixtures,
  fixtureId,
  dataId,
  width,
  height,
  focused,
  outputDir,
  sizeId,
  stripAnsi = false,
}: RenderOneFixtureOptions): Promise<WrittenInkFixture> {
  const fixture = fixtureById(fixtures, fixtureId);
  const rendered = await renderInkFixture({ fixture, dataId, width, height, focused });
  return writeRenderedFixture({
    rendered,
    outputDir,
    ...(sizeId === undefined ? {} : { sizeId }),
    stripAnsi,
  });
}

export async function renderAllRegisteredFixtures({
  fixtures,
  outputDir,
  stripAnsi = false,
}: RenderAllFixtureOptions): Promise<WrittenInkFixture[]> {
  const written: WrittenInkFixture[] = [];
  for (const fixture of fixtures) {
    const focusStates = fixture.focusStates ?? [true, false];
    for (const dataId of Object.keys(fixture.data)) {
      for (const size of fixture.sizes) {
        for (const focused of focusStates) {
          const rendered = await renderInkFixture({
            fixture,
            dataId,
            width: size.width,
            height: size.height,
            focused,
          });
          written.push(
            await writeRenderedFixture({
              rendered,
              outputDir,
              sizeId: size.id,
              stripAnsi,
            }),
          );
        }
      }
    }
  }
  const manifest = {
    generatedAt: new Date(0).toISOString(),
    count: written.length,
    snapshots: written.map((item) => ({
      fixtureId: item.fixtureId,
      dataId: item.dataId,
      focused: item.focused,
      width: item.width,
      height: item.height,
      file: path.basename(item.path),
    })),
  };
  await mkdir(outputDir, { recursive: true });
  await writeFile(path.join(outputDir, 'manifest.json'), `${JSON.stringify(manifest, null, 2)}\n`);
  return written;
}
