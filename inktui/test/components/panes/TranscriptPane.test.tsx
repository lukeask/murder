import { describe, expect, it } from 'vitest';
import { SCROLL_THUMB } from '../../../src/components/glyphs.js';
import { TranscriptPane, tmuxFrameRows } from '../../../src/components/panes/TranscriptPane.js';
import type { ChatTurn } from '../../../src/selectors/conversationsSelectors.js';
import { renderInkFixture, stripAnsiSgr } from '../../fixtures/pane_rendering/renderInkFixture.js';
import type { PaneFixture } from '../../fixtures/pane_rendering/types.js';

const turn: ChatTurn = {
  speaker: 'assistant',
  text: Array.from({ length: 10 }, (_, index) => `chat-line-${index + 1}`).join('\n'),
  blockId: 'assistant-1',
};

const fixture: PaneFixture<readonly ChatTurn[]> = {
  id: 'transcript-pane-scroll',
  description: 'TranscriptPane bottom-anchored scroll fixture',
  sizes: [{ id: 'preferred', width: 42, height: 8 }],
  data: { long: [turn] },
  render: ({ data, width, height, focused }) => (
    <TranscriptPane
      width={width}
      height={height}
      focused={focused}
      title="collab"
      footerLeft="claude ◇ opus"
      footerRight="main"
      turns={data}
      viewMode="verbose"
      scrollUp={2}
      gotoLine={null}
    />
  ),
};

describe('TranscriptPane', () => {
  it('renders a bottom-anchored transcript window with a scrollbar thumb', async () => {
    const rendered = await renderInkFixture({
      fixture,
      dataId: 'long',
      width: 42,
      height: 8,
      focused: true,
    });
    const frame = stripAnsiSgr(rendered.ansi);

    expect(frame).toContain('chat-line-3');
    expect(frame).toContain('chat-line-8');
    expect(frame).not.toContain('chat-line-2');
    expect(frame).not.toContain('chat-line-9');
    expect(frame).toContain(SCROLL_THUMB);
  });

  it('sanitizes and clamps tmux frames to a rectangular cell surface', () => {
    const rows = tmuxFrameRows('a\r\u001B[10Csecret\tbroad\nsecond', 8, 4);
    expect(rows).toEqual(['a', 'secret  ', 'second']);
    expect(rows.every((row) => [...row].length <= 8)).toBe(true);
  });

  it('does not leak tmux CSI into the rendered frame', async () => {
    const tmuxFixture: PaneFixture<readonly ChatTurn[]> = {
      id: 'transcript-pane-tmux',
      description: 'TranscriptPane tmux sanitization fixture',
      sizes: [{ id: 'preferred', width: 42, height: 8 }],
      data: { empty: [] },
      render: ({ width, height, focused }) => (
        <TranscriptPane
          width={width}
          height={height}
          focused={focused}
          title="collab"
          footerLeft="claude ◇ opus"
          footerRight="main"
          turns={[]}
          viewMode="tmux"
          scrollUp={0}
          gotoLine={null}
          tmuxFrame={'left\r\u001B[2Aescape\nnext'}
        />
      ),
    };
    const rendered = await renderInkFixture({
      fixture: tmuxFixture,
      dataId: 'empty',
      width: 42,
      height: 8,
      focused: true,
    });
    const frame = stripAnsiSgr(rendered.ansi);
    expect(frame).toContain('left');
    expect(frame).toContain('escape');
    expect(frame).toContain('next');
    expect(frame).not.toContain('\u001B');
    expect(frame).not.toContain('\r');
  });
});
