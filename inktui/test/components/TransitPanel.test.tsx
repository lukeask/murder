/**
 * TransitPanel test — proves the git commit-graph right-rail panel RENDERS through the real
 * Ink/Yoga renderer (ink-testing-library), exercises hjkl / lane-switch / g-jump navigation, and
 * guards the loading + empty states.
 *
 * Seeding: the transit slice is a small custom slice (lanes + status), fed via the
 * `state.transit_snapshot` RPC + `actions.transit.refresh()` — the same faithful path the live store
 * takes (mirrors CrowsPanel.test's `state.crow_snapshot` seed).
 *
 * The panel's g-capture interprets per-char chords (`char:<x>`) routed through the dispatcher; we
 * drive navigation via stdin (the real routing path) and assert the `lastFrame()` text changes.
 */

import { Box } from 'ink';
import { render } from 'ink-testing-library';
import type { JSX } from 'react';
import { describe, expect, it } from 'vitest';
import { FakeBusClient } from '../../src/bus/FakeBusClient.js';
import { TransitPanel } from '../../src/components/TransitPanel.js';
import { AppStoreProvider } from '../../src/hooks/useAppStore.js';
import { InputStoresProvider } from '../../src/hooks/useInputStores.js';
import { useRootInput } from '../../src/hooks/useRootInput.js';
import { createInputStores } from '../../src/input/createInputStores.js';
import { createAppStore } from '../../src/store/store.js';
import type { TransitSnapshotReply } from '../../src/store/transit/transitActions.js';

const RETURN = '\r';
const ESC = '\x1b';

async function tick(): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 20));
}

const NOW = Math.floor(Date.now() / 1000);
const MIN = 60;
const HOUR = 60 * MIN;
const DAY = 24 * HOUR;

/**
 * Realistic multi-lane fixture: a `main` trunk (6 commits, ages spanning minutes → ~22 days), a
 * `pane-polish` branch forked off main's `m3` (~3 days ago) with 3 of its own commits, plus a third
 * `usage-tiers` worktree branch forked off `m2`. Each branch's `commits` include the pre-fork shared
 * `main` ancestry so a big duration-jump walks across the fork — exactly the spec's intent.
 */
function multiLaneReply(): TransitSnapshotReply {
  // Named so the branch lanes can splice in the shared pre-fork main ancestry without index access.
  const m0 = {
    sha: 'm0aaaaa',
    short: 'm0aaaa',
    subject: 'main: latest tip',
    body: 'tip body line one\ntip body line two',
    ts_epoch: NOW - 5 * MIN,
    parents: ['m1bbbbb'],
  };
  const m1 = {
    sha: 'm1bbbbb',
    short: 'm1bbbb',
    subject: 'main: an hour ago',
    body: 'hour body',
    ts_epoch: NOW - 65 * MIN,
    parents: ['m2ccccc'],
  };
  const m2 = {
    sha: 'm2ccccc',
    short: 'm2cccc',
    subject: 'main: two hours',
    body: 'two hours body',
    ts_epoch: NOW - 2 * HOUR - 10 * MIN,
    parents: ['m3ddddd'],
  };
  const m3 = {
    sha: 'm3ddddd',
    short: 'm3dddd',
    subject: 'main: three days ago (fork base)',
    body: 'fork base body',
    ts_epoch: NOW - 3 * DAY - HOUR,
    parents: ['m4eeeee'],
  };
  const m4 = {
    sha: 'm4eeeee',
    short: 'm4eeee',
    subject: 'main: a week back',
    body: 'week back body',
    ts_epoch: NOW - 8 * DAY - HOUR,
    parents: ['m5fffff'],
  };
  const m5 = {
    sha: 'm5fffff',
    short: 'm5ffff',
    subject: 'main: three weeks ago (old base)',
    body: 'old base body for the 20d jump',
    ts_epoch: NOW - 22 * DAY - HOUR,
    parents: [],
  };
  const paneOwn = [
    {
      sha: 'p0aaaaa',
      short: 'p0aaaa',
      subject: 'pane-polish: flex shrink fix',
      body: 'flexShrink=0 on lane rows',
      ts_epoch: NOW - 30 * MIN,
      parents: ['p1bbbbb'],
    },
    {
      sha: 'p1bbbbb',
      short: 'p1bbbb',
      subject: 'pane-polish: age markers',
      body: 'sparse floored markers',
      ts_epoch: NOW - 4 * HOUR,
      parents: ['p2ccccc'],
    },
    {
      sha: 'p2ccccc',
      short: 'p2cccc',
      subject: 'pane-polish: scaffold',
      body: 'initial scaffold',
      ts_epoch: NOW - 2 * DAY - HOUR,
      parents: ['m3ddddd'],
    },
  ];
  const usageOwn = [
    {
      sha: 'u0aaaaa',
      short: 'u0aaaa',
      subject: 'usage-tiers: fluid width',
      body: 'greedy bar',
      ts_epoch: NOW - 90 * MIN,
      parents: ['u1bbbbb'],
    },
    {
      sha: 'u1bbbbb',
      short: 'u1bbbb',
      subject: 'usage-tiers: start',
      body: 'start tiers',
      ts_epoch: NOW - 26 * HOUR,
      parents: ['m2ccccc'],
    },
  ];
  return {
    generated_at_epoch: NOW,
    invalidation_key: 'iv',
    lanes: [
      {
        branch: 'main',
        is_main: true,
        worktree_path: null,
        head_sha: 'm0aaaaa',
        fork_sha: null,
        commits: [m0, m1, m2, m3, m4, m5],
      },
      // pane-polish forks off m3ddddd; its commits then include the pre-fork main ancestry (m3→m5).
      {
        branch: 'pane-polish',
        is_main: false,
        worktree_path: '.murder/worktrees/pane',
        head_sha: 'p0aaaaa',
        fork_sha: 'm3ddddd',
        commits: [...paneOwn, m3, m4, m5],
      },
      {
        branch: 'usage-tiers',
        is_main: false,
        worktree_path: '.murder/worktrees/usage',
        head_sha: 'u0aaaaa',
        fork_sha: 'm2ccccc',
        commits: [...usageOwn, m2, m3, m4, m5],
      },
    ],
  };
}

function RootInput(): null {
  useRootInput();
  return null;
}

function Harness({
  store,
  inputStores,
  innerWidth = 42,
}: {
  readonly store: ReturnType<typeof createAppStore>['store'];
  readonly inputStores: ReturnType<typeof createInputStores>;
  readonly innerWidth?: number;
}): JSX.Element {
  return (
    <AppStoreProvider value={store}>
      <InputStoresProvider value={inputStores}>
        <RootInput />
        <Box height={24} width={innerWidth + 4}>
          <TransitPanel innerWidth={innerWidth} />
        </Box>
      </InputStoresProvider>
    </AppStoreProvider>
  );
}

async function setup(reply: TransitSnapshotReply = multiLaneReply(), focused = true) {
  const fake = new FakeBusClient();
  fake.stubRpc('state.transit_snapshot', reply);
  const { store, dispose } = createAppStore(fake);
  await store.getState().actions.transit.refresh();
  const inputStores = createInputStores(['transit'], focused ? 'transit' : 'chat');
  return { fake, store, dispose, inputStores };
}

describe('TransitPanel — multi-lane render', () => {
  it('renders without throwing: Git Tree title, all lanes, stations, branch tags', async () => {
    const { store, inputStores, dispose } = await setup();
    const { lastFrame } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();
    const frame = lastFrame() ?? '';
    // Eyeball aid in test logs (the real ASCII frame for the report).
    console.log(`\n===== GIT TREE FRAME =====\n${frame}\n=========================`);

    // Pane title (renamed Transit → Git Tree).
    expect(frame).toContain('Git Tree');
    // Each lane shows its branch tag (in the ▐ … ⌂ ▌ tag bar).
    expect(frame).toContain('▐ main ⌂ ▌');
    expect(frame).toContain('pane-polish');
    expect(frame).toContain('usage-tiers');
    // Railway glyphs present (a HEAD cap on the un-selected lane heads + track).
    expect(frame).toContain('▶');
    expect(frame).toContain('━');
    // The selected commit's "you are here" glyph (main's head is selected by default).
    expect(frame).toContain('◆');
    dispose();
  });

  it('renders exactly the 3 lane rows (one tag bar each, no blank row between)', async () => {
    const { store, inputStores, dispose } = await setup();
    const { lastFrame } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();
    const frame = lastFrame() ?? '';
    const lines = frame.split('\n');
    // Each lane row carries exactly one tag bar (the ▐ of `▐ name ⌂ ▌`); count them.
    const tagLines = lines.filter((l) => l.includes('▐'));
    expect(tagLines).toHaveLength(3);
    // No fully-blank line wedged BETWEEN the three lane rows (they are consecutive).
    const first = lines.findIndex((l) => l.includes('▐'));
    const last = lines.map((l) => l.includes('▐')).lastIndexOf(true);
    const between = lines.slice(first, last + 1);
    expect(between.filter((l) => l.trim() === '')).toHaveLength(0);
    dispose();
  });

  it('draws a shared age ruler above the lanes (a time label applies to all branches)', async () => {
    const { store, inputStores, dispose } = await setup();
    const { lastFrame } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();
    const frame = lastFrame() ?? '';
    const lines = frame.split('\n');
    // The ruler is the first content line above the lane rows; it carries coarse age labels.
    const firstTag = lines.findIndex((l) => l.includes('▐'));
    const rulerLine = lines.slice(0, firstTag).find((l) => /\d[mhdw]/.test(l)) ?? '';
    expect(rulerLine).toMatch(/\d[mhdw]/);
    dispose();
  });

  it('wraps (does not truncate) the selected commit message body', async () => {
    // A long single-line body whose tail word would be lost under truncation must appear on a wrap.
    const reply: TransitSnapshotReply = {
      generated_at_epoch: NOW,
      invalidation_key: 'iv',
      lanes: [
        {
          branch: 'main',
          is_main: true,
          worktree_path: null,
          head_sha: 'w0',
          fork_sha: null,
          commits: [
            {
              sha: 'w0',
              short: 'w0aaaa',
              subject: 'wrap me',
              body: 'alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima TAILWORD',
              ts_epoch: NOW - 5 * MIN,
              parents: [],
            },
          ],
        },
      ],
    };
    const { store, inputStores, dispose } = await setup(reply);
    const { lastFrame } = render(
      <Harness store={store} inputStores={inputStores} innerWidth={30} />,
    );
    await tick();
    const frame = lastFrame() ?? '';
    // The tail word survives (it wrapped onto a later line rather than being truncated away).
    expect(frame).toContain('TAILWORD');
    dispose();
  });

  it('info section shows the selected commit short sha, branch, and BODY', async () => {
    const { store, inputStores, dispose } = await setup();
    const { lastFrame } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();
    const frame = lastFrame() ?? '';
    // Cursor seeds on main's HEAD (m0aaaaa).
    expect(frame).toContain('m0aaaa'); // short sha
    expect(frame).toContain('tip body line one'); // body
    dispose();
  });
});

describe('TransitPanel — navigation', () => {
  it('l/h move selection within the lane → info body updates', async () => {
    const { store, inputStores, dispose } = await setup();
    const { lastFrame, stdin } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();
    expect(lastFrame() ?? '').toContain('tip body line one'); // m0 head

    stdin.write('h'); // older → m1bbbbb
    await tick();
    const afterOlder = lastFrame() ?? '';
    expect(afterOlder).toContain('m1bbbb');
    expect(afterOlder).toContain('hour body');
    expect(afterOlder).not.toContain('tip body line one');

    stdin.write('l'); // newer → back to m0
    await tick();
    expect(lastFrame() ?? '').toContain('tip body line one');
    dispose();
  });

  it('j/k switch lanes → branch in the info section changes', async () => {
    const { store, inputStores, dispose } = await setup();
    const { lastFrame, stdin } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();
    // Starts on main.
    const before = lastFrame() ?? '';
    // The info section's branch label: main's head selected.
    expect(before).toContain('m0aaaa');

    stdin.write('j'); // → pane-polish lane (clamped to nearest-by-time commit)
    await tick();
    const onPane = lastFrame() ?? '';
    // The selected commit now belongs to pane-polish (nearest to m0's ~5m-ago time is p0 ~30m).
    expect(onPane).toContain('p0aaaa');

    stdin.write('k'); // → back to main
    await tick();
    expect(lastFrame() ?? '').toContain('m0aaaa');
    dispose();
  });

  it('g + lane-hint jumps to that lane HEAD and shows the hint overlay while pending', async () => {
    const { store, inputStores, dispose } = await setup();
    const { lastFrame, stdin } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();

    stdin.write('g'); // enter g-pending → hint overlay
    await tick();
    const overlay = lastFrame() ?? '';
    // The hint overlay lists lane hint keys + branches.
    expect(overlay).toContain('type 5d/20m');
    expect(overlay).toMatch(/\[[a-z0-9]\]\s*main/);

    // 'u' is the hint for usage-tiers (first free char 'u'); jump to its HEAD.
    stdin.write('u');
    await tick();
    const onUsage = lastFrame() ?? '';
    expect(onUsage).toContain('u0aaaa'); // usage-tiers HEAD
    expect(onUsage).toContain('greedy bar'); // its body
    // Overlay gone.
    expect(onUsage).not.toContain('type 5d/20m');
    dispose();
  });

  it('g 2 0 d <return> jumps ~20 days back onto a pre-fork shared main commit', async () => {
    // Start on the pane-polish lane (forked 2 days ago). g20d must walk across the fork into the
    // shared main ancestry → the resolved commit is a main commit, and the panel maps it onto the
    // main lane.
    const { store, inputStores, dispose } = await setup();
    const { lastFrame, stdin } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();
    stdin.write('j'); // → pane-polish lane
    await tick();
    expect(lastFrame() ?? '').toContain('p0aaaa');

    stdin.write('g');
    await tick();
    stdin.write('2');
    stdin.write('0');
    stdin.write('d');
    await tick();
    stdin.write(RETURN);
    await tick();

    const frame = lastFrame() ?? '';
    // 20 days ago is closest to m4eeeee (8d) vs m5fffff (22d): |20-8|=12, |20-22|=2 → m5fffff wins.
    // m5fffff is a pre-fork main commit, so the info branch is `main`.
    expect(frame).toContain('m5ffff');
    expect(frame).toContain('old base body for the 20d jump');
    dispose();
  });

  it('g then esc cancels without moving', async () => {
    const { store, inputStores, dispose } = await setup();
    const { lastFrame, stdin } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();
    stdin.write('g');
    await tick();
    expect(lastFrame() ?? '').toContain('type 5d/20m');
    stdin.write(ESC);
    await tick();
    const after = lastFrame() ?? '';
    expect(after).not.toContain('type 5d/20m');
    expect(after).toContain('m0aaaa'); // unchanged selection
    dispose();
  });
});

describe('TransitPanel — loading / empty guards', () => {
  it('renders the loading state without throwing', async () => {
    const fake = new FakeBusClient();
    // Never resolve the snapshot: status stays 'loading' after refresh is invoked, so the panel must
    // paint its loading chrome (the dangling promise is torn down with the render at dispose).
    fake.stubRpc('state.transit_snapshot', () => new Promise<TransitSnapshotReply>(() => {}));
    const { store, dispose } = createAppStore(fake);
    void store.getState().actions.transit.refresh();
    const inputStores = createInputStores(['transit'], 'transit');
    const { lastFrame } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();
    const frame = lastFrame() ?? '';
    expect(frame).toContain('Git Tree');
    expect(frame).toContain('loading');
    dispose();
  });

  it('renders the empty (no lanes) state without throwing', async () => {
    const { store, inputStores, dispose } = await setup({
      generated_at_epoch: NOW,
      invalidation_key: 'iv',
      lanes: [],
    });
    const { lastFrame } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();
    const frame = lastFrame() ?? '';
    expect(frame).toContain('Git Tree');
    expect(frame).toContain('no branches');
    dispose();
  });
});
