import type { Bundle } from './bundle';
import type { TypeLine } from '../islands/typewriter';

const PROMPT_DELAY_MS = 400;
const CMD_DELAY_MS = 250;
const FAIL_DELAY_MS = 500;
const CLAIM_DELAY_MS = 500;
const BLOCK_DELAY_MS = 0;

function firstLine(text: string): string {
  const [head] = text.split('\n');
  return head ?? '';
}

/**
 * Turns a replay bundle into the hero terminal's typed transcript: the
 * task prompt (from `summary`), one `cmd` line per `post`/`post_fail` event
 * (with an `exit 1` `fail` line for the failing ones), the assistant's
 * claim, and the block reason's first line.
 */
export function heroScript(bundle: Bundle): TypeLine[] {
  const lines: TypeLine[] = [
    { text: bundle.summary, delayMs: PROMPT_DELAY_MS, cls: 'prompt' },
  ];

  for (const event of bundle.events) {
    if (event.kind !== 'post' && event.kind !== 'post_fail') continue;
    lines.push({ text: `$ ${event.command}`, delayMs: CMD_DELAY_MS, cls: 'cmd' });
    if (event.kind === 'post_fail') {
      lines.push({ text: 'exit 1', delayMs: FAIL_DELAY_MS, cls: 'fail' });
    }
  }

  lines.push({ text: bundle.final_message, delayMs: CLAIM_DELAY_MS, cls: 'claim' });
  lines.push({ text: firstLine(bundle.decision.reason), delayMs: BLOCK_DELAY_MS, cls: 'block' });

  return lines;
}
