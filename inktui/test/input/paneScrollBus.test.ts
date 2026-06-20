/**
 * paneScrollBus tests — the focus-id-keyed wheel→scroll command channel. Covers per-id fan-out,
 * isolation between ids, unsubscribe, and the emit-with-no-subscriber no-op.
 */

import { describe, expect, it } from 'vitest';
import { createPaneScrollBus } from '../../src/input/paneScrollBus.js';

describe('paneScrollBus', () => {
  it('delivers a scroll to the listener registered at the same focus id', () => {
    const bus = createPaneScrollBus();
    const seen: Array<[string, number]> = [];
    bus.subscribe('stage:chat:a', (dir, amt) => seen.push([dir, amt]));
    bus.emit('stage:chat:a', 'up', 3);
    bus.emit('stage:chat:a', 'down', 1);
    expect(seen).toEqual([
      ['up', 3],
      ['down', 1],
    ]);
  });

  it('does not deliver across focus ids', () => {
    const bus = createPaneScrollBus();
    const seen: string[] = [];
    bus.subscribe('stage:chat:a', (dir) => seen.push(dir));
    bus.emit('stage:doc:readme', 'up', 3);
    expect(seen).toEqual([]);
  });

  it('fans out to multiple listeners on one id', () => {
    const bus = createPaneScrollBus();
    let a = 0;
    let b = 0;
    bus.subscribe('stage:chat:x', () => a++);
    bus.subscribe('stage:chat:x', () => b++);
    bus.emit('stage:chat:x', 'down', 1);
    expect([a, b]).toEqual([1, 1]);
  });

  it('stops delivering after unsubscribe', () => {
    const bus = createPaneScrollBus();
    let count = 0;
    const off = bus.subscribe('stage:chat:a', () => count++);
    bus.emit('stage:chat:a', 'up', 1);
    off();
    bus.emit('stage:chat:a', 'up', 1);
    expect(count).toBe(1);
  });

  it('emit with no subscriber is a safe no-op', () => {
    const bus = createPaneScrollBus();
    expect(() => bus.emit('stage:chat:gone', 'up', 3)).not.toThrow();
  });
});
