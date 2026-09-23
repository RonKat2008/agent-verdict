import { describe, expect, it } from 'vitest';

import type { Bundle } from '../../src/lib/bundle';
import { parseBundle } from '../../src/lib/bundle';
import { heroScript } from '../../src/lib/hero-script';
import fixture from '../../src/data/fixtures/unreported-failure.json';

describe('heroScript', () => {
  it('types every line of a multi-line block reason as its own block line', () => {
    const bundle = parseBundle(fixture);
    expect(bundle.decision.reason.split('\n')).toHaveLength(3);

    const blockLines = heroScript(bundle).filter((line) => line.cls === 'block');

    expect(blockLines).toHaveLength(3);
    expect(blockLines[0]?.text).toBe(
      "Rule R1 (unreported failure): the final message presents the user's task as complete or successful, but these steps still show unresolved failures:",
    );
    expect(blockLines[1]?.text).toBe('step 1 (Bash, exit 1): npm test');
    expect(blockLines[2]?.text).toBe(
      'Fix it or tell the user it is still failing. Run `verdict show` for details.',
    );
  });

  it('emits no block lines for a pass decision with an empty reason', () => {
    const bundle = parseBundle(fixture);
    const passBundle: Bundle = {
      ...bundle,
      decision: {
        action: 'pass',
        would_have: null,
        rule_id: null,
        threshold_used: null,
        reason: '',
      },
    };

    const blockLines = heroScript(passBundle).filter((line) => line.cls === 'block');

    expect(blockLines).toHaveLength(0);
  });
});
