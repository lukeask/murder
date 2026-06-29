import { paneFixtures } from './registry.js';
import { renderInkFixture } from './renderInkFixture.js';

// biome-ignore lint/complexity/useLiteralKeys: noPropertyAccessFromIndexSignature requires bracket access.
process.env['FORCE_COLOR'] = process.env['FORCE_COLOR'] ?? '3';

const PANE_ALIASES = new Map<string, string>([
  ['plans', 'plans-panel'],
  ['notes', 'notes-panel'],
  ['reports', 'reports-panel'],
  ['tickets', 'tickets-panel'],
  ['crows', 'crows-panel'],
  ['roster', 'roster-panel'],
  ['history', 'history-panel'],
  ['usage', 'usage-panel'],
  ['tree', 'tree-panel'],
  ['doc', 'doc-pane'],
  ['stage-doc', 'doc-pane'],
  ['transcript', 'transcript-pane'],
  ['stage-transcript', 'transcript-pane'],
  ['chat-input', 'chat-input'],
  ['text-input', 'text-input'],
  ['pane-border', 'pane-border'],
  ['paneBorder', 'pane-border'],
  ['top-bar', 'top-bar'],
  ['bottom-bar', 'bottom-bar'],
  ['pane', 'pane'],
  ['ledger', 'ledger'],
  ['resource-row', 'resource-row'],
]);

function usage(): string {
  const fixtureLines = paneFixtures
    .map((fixture) => `  ${fixture.id}: ${Object.keys(fixture.data).join(', ')}`)
    .join('\n');
  return [
    'usage: render-pane-fixture <pane_type> <fixture_data> <lh_allocation> <cw_allocation>',
    '',
    'fixture_data is a registered data id for the pane type.',
    '',
    'available fixtures:',
    fixtureLines,
  ].join('\n');
}

function parsePositiveInt(value: string, label: string): number {
  const parsed = Number.parseInt(value, 10);
  if (!Number.isInteger(parsed) || parsed <= 0 || String(parsed) !== value) {
    throw new Error(`${label} must be a positive integer, got '${value}'`);
  }
  return parsed;
}

function resolvePaneType(input: string): string {
  return PANE_ALIASES.get(input) ?? input;
}

async function main(): Promise<void> {
  const [paneTypeArg, fixtureData, lhArg, cwArg, ...extra] = process.argv.slice(2);
  if (
    paneTypeArg === undefined ||
    fixtureData === undefined ||
    lhArg === undefined ||
    cwArg === undefined ||
    extra.length > 0 ||
    paneTypeArg === '--help' ||
    paneTypeArg === '-h'
  ) {
    throw new Error(usage());
  }

  const fixtureId = resolvePaneType(paneTypeArg);
  const fixture = paneFixtures.find((candidate) => candidate.id === fixtureId);
  if (fixture === undefined) {
    throw new Error(`unknown pane type '${paneTypeArg}'\n\n${usage()}`);
  }

  const height = parsePositiveInt(lhArg, 'lh_allocation');
  const width = parsePositiveInt(cwArg, 'cw_allocation');
  const rendered = await renderInkFixture({
    fixture,
    dataId: fixtureData,
    width,
    height,
    focused: true,
  });
  process.stdout.write(`${rendered.ansi}\n`);
}

main().catch((error: unknown) => {
  const message = error instanceof Error ? error.message : String(error);
  process.stderr.write(`${message}\n`);
  process.exitCode = 1;
});
