/**
 * crowHealthSelectors tests — the classifier is a pure function (no React, store, or clock-by-default),
 * so every branch of the ported Textual precedence is asserted directly. Mirrors the branch coverage
 * of the legacy Textual crow_health module's test surface.
 *
 * Precedence under test (first match wins):
 *   RED    — open escalation, or severity ≥ 2, or a red status (escalating/blocked/failed/dead)
 *   YELLOW — stuck-but-alive flag
 *   GREEN  — running / idle
 *   NEUTRAL— done, or anything with no positive read
 */

import {
  classifyCrowHealth,
  HEALTH_EDGE_COLOR,
  isStuck,
  STUCK_AFTER_MS,
} from '../../src/selectors/crowHealthSelectors.js';

describe('classifyCrowHealth — precedence', () => {
  it('GREEN for running / idle (healthy live crow)', () => {
    expect(classifyCrowHealth({ status: 'running' })).toBe('green');
    expect(classifyCrowHealth({ status: 'idle' })).toBe('green');
  });

  it('RED for red statuses (escalating / blocked / failed / dead)', () => {
    expect(classifyCrowHealth({ status: 'escalating' })).toBe('red');
    expect(classifyCrowHealth({ status: 'blocked' })).toBe('red');
    expect(classifyCrowHealth({ status: 'failed' })).toBe('red');
    expect(classifyCrowHealth({ status: 'dead' })).toBe('red');
  });

  it('RED when an open escalation is linked, overriding a green status', () => {
    expect(classifyCrowHealth({ status: 'running', openEscalations: 1 })).toBe('red');
  });

  it('RED when max severity ≥ 2, even with zero open rows (defensive)', () => {
    expect(classifyCrowHealth({ status: 'idle', maxSeverity: 2 })).toBe('red');
    // severity 1 is informational — does NOT force red on its own.
    expect(classifyCrowHealth({ status: 'idle', maxSeverity: 1 })).toBe('green');
  });

  it('escalation/severity RED wins over the stuck flag', () => {
    expect(classifyCrowHealth({ status: 'running', openEscalations: 1, stuck: true })).toBe('red');
  });

  it('YELLOW when stuck-but-alive (and no escalation / red status)', () => {
    expect(classifyCrowHealth({ status: 'running', stuck: true })).toBe('yellow');
    expect(classifyCrowHealth({ status: 'idle', stuck: true })).toBe('yellow');
  });

  it('red status wins over the stuck flag (precedence: status-RED before YELLOW)', () => {
    expect(classifyCrowHealth({ status: 'failed', stuck: true })).toBe('red');
  });

  it('NEUTRAL for done, unknown, empty, or null status', () => {
    expect(classifyCrowHealth({ status: 'done' })).toBe('neutral');
    expect(classifyCrowHealth({ status: 'mystery' })).toBe('neutral');
    expect(classifyCrowHealth({ status: '' })).toBe('neutral');
    expect(classifyCrowHealth({ status: null })).toBe('neutral');
  });

  it('normalizes status case before matching', () => {
    expect(classifyCrowHealth({ status: 'RUNNING' })).toBe('green');
    expect(classifyCrowHealth({ status: 'Failed' })).toBe('red');
  });
});

describe('isStuck — 60s heartbeat rule', () => {
  const now = 1_000_000_000_000;

  it('true when a running/idle crow has not been seen for > 60s', () => {
    expect(isStuck({ status: 'running', lastSeenMs: now - STUCK_AFTER_MS - 1, nowMs: now })).toBe(
      true,
    );
    expect(isStuck({ status: 'idle', lastSeenMs: now - 120_000, nowMs: now })).toBe(true);
  });

  it('false at exactly 60s (strictly greater-than, mirroring Python)', () => {
    expect(isStuck({ status: 'running', lastSeenMs: now - STUCK_AFTER_MS, nowMs: now })).toBe(
      false,
    );
  });

  it('false when the crow is not running/idle (a dead crow is not stuck-but-alive)', () => {
    expect(isStuck({ status: 'failed', lastSeenMs: now - 999_999, nowMs: now })).toBe(false);
    expect(isStuck({ status: 'done', lastSeenMs: now - 999_999, nowMs: now })).toBe(false);
  });

  it('false when last-seen is unknown (no positive read)', () => {
    expect(isStuck({ status: 'running', lastSeenMs: null, nowMs: now })).toBe(false);
  });

  it('composes with classify: a stuck live crow classifies YELLOW', () => {
    const stuck = isStuck({ status: 'running', lastSeenMs: now - 90_000, nowMs: now });
    expect(classifyCrowHealth({ status: 'running', stuck })).toBe('yellow');
  });
});

describe('HEALTH_EDGE_COLOR — Ink colour-name map', () => {
  it('maps each health state to a literal Ink colour (neutral → gray)', () => {
    expect(HEALTH_EDGE_COLOR.red).toBe('red');
    expect(HEALTH_EDGE_COLOR.yellow).toBe('yellow');
    expect(HEALTH_EDGE_COLOR.green).toBe('green');
    expect(HEALTH_EDGE_COLOR.neutral).toBe('gray');
  });
});
