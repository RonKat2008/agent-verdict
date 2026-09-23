import { describe, expect, it } from 'vitest';

import { BundleShapeError, parseBundle } from '../../src/lib/bundle';
import fixture from '../../src/data/fixtures/unreported-failure.json';

describe('parseBundle', () => {
  it('accepts the fixture bundle', () => {
    const bundle = parseBundle(fixture);
    expect(bundle.name).toBe('unreported-failure');
    expect(bundle.decision.rule_id).toBe('R1');
    expect(bundle.events.length).toBeGreaterThan(0);
  });

  it('rejects an unknown top-level key', () => {
    const bad = { ...fixture, extra: 1 };
    expect(() => parseBundle(bad)).toThrow(BundleShapeError);
  });

  it('rejects a bad event kind', () => {
    const bad = JSON.parse(JSON.stringify(fixture));
    bad.events[0].kind = 'nonsense';
    expect(() => parseBundle(bad)).toThrow(BundleShapeError);
  });

  it('rejects a missing decision', () => {
    const bad = JSON.parse(JSON.stringify(fixture)) as Record<string, unknown>;
    delete bad.decision;
    expect(() => parseBundle(bad)).toThrow(BundleShapeError);
  });
});
