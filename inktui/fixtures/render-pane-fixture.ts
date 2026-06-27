import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { renderAllRegisteredFixtures, renderOneRegisteredFixture } from './renderInkFixture.js';

// biome-ignore lint/complexity/useLiteralKeys: noPropertyAccessFromIndexSignature requires bracket access.
process.env['FORCE_COLOR'] = process.env['FORCE_COLOR'] ?? '3';

interface CliArgs {
  readonly all: boolean;
  readonly fixture?: string;
  readonly data?: string;
  readonly width?: number;
  readonly height?: number;
  readonly focused?: boolean;
  readonly outputDir: string;
  readonly stripAnsi: boolean;
}

function usage(): string {
  return [
    'render-pane-fixture --all [--output-dir fixtures/output] [--strip-ansi]',
    'render-pane-fixture --fixture <id> --data <id> --width <cols> --height <rows> [--focus focused|blurred] [--output-dir fixtures/output]',
  ].join('\n');
}

function readFlagValue(args: readonly string[], index: number, flag: string): string {
  const value = args[index + 1];
  if (value === undefined || value.startsWith('--')) {
    throw new Error(`missing value for ${flag}`);
  }
  return value;
}

function parseArgs(argv: readonly string[]): CliArgs {
  let all = false;
  let fixture: string | undefined;
  let data: string | undefined;
  let width: number | undefined;
  let height: number | undefined;
  let focused: boolean | undefined;
  let outputDir = 'fixtures/output';
  let stripAnsi = false;

  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    switch (arg) {
      case '--all':
        all = true;
        break;
      case '--fixture':
        fixture = readFlagValue(argv, index, arg);
        index += 1;
        break;
      case '--data':
        data = readFlagValue(argv, index, arg);
        index += 1;
        break;
      case '--width':
        width = Number.parseInt(readFlagValue(argv, index, arg), 10);
        index += 1;
        break;
      case '--height':
        height = Number.parseInt(readFlagValue(argv, index, arg), 10);
        index += 1;
        break;
      case '--focus': {
        const value = readFlagValue(argv, index, arg);
        if (value !== 'focused' && value !== 'blurred') {
          throw new Error("--focus must be 'focused' or 'blurred'");
        }
        focused = value === 'focused';
        index += 1;
        break;
      }
      case '--output-dir':
        outputDir = readFlagValue(argv, index, arg);
        index += 1;
        break;
      case '--strip-ansi':
        stripAnsi = true;
        break;
      case '--help':
      case '-h':
        throw new Error(usage());
      default:
        throw new Error(`unknown argument '${arg}'\n${usage()}`);
    }
  }

  return {
    all,
    ...(fixture === undefined ? {} : { fixture }),
    ...(data === undefined ? {} : { data }),
    ...(width === undefined ? {} : { width }),
    ...(height === undefined ? {} : { height }),
    ...(focused === undefined ? {} : { focused }),
    outputDir,
    stripAnsi,
  };
}

async function main(): Promise<void> {
  const args = parseArgs(process.argv.slice(2));
  const { paneFixtures } = await import('./registry.js');
  const outputDir = path.resolve(
    path.dirname(fileURLToPath(import.meta.url)),
    '..',
    args.outputDir,
  );
  if (args.all) {
    const written = await renderAllRegisteredFixtures({
      fixtures: paneFixtures,
      outputDir,
      stripAnsi: args.stripAnsi,
    });
    process.stdout.write(`wrote ${written.length} fixture snapshots to ${outputDir}\n`);
    return;
  }
  if (
    args.fixture === undefined ||
    args.data === undefined ||
    args.width === undefined ||
    args.height === undefined
  ) {
    throw new Error(
      `single fixture render requires --fixture, --data, --width, and --height\n${usage()}`,
    );
  }
  const written = await renderOneRegisteredFixture({
    fixtures: paneFixtures,
    fixtureId: args.fixture,
    dataId: args.data,
    width: args.width,
    height: args.height,
    focused: args.focused ?? true,
    outputDir,
    stripAnsi: args.stripAnsi,
  });
  process.stdout.write(`wrote ${written.path}\n`);
}

main().catch((error: unknown) => {
  const message = error instanceof Error ? error.message : String(error);
  process.stderr.write(`${message}\n`);
  process.exitCode = 1;
});
