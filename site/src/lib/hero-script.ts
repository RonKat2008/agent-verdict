import type { Bundle } from './bundle';
import type { TypeLine } from '../islands/typewriter';

const PROMPT_DELAY_MS = 400;
const CMD_DELAY_MS = 250;
const FAIL_DELAY_MS = 500;
const CLAIM_DELAY_MS = 500;
const BLOCK_LINE_DELAY_MS = 250;

/**
 * The hook's own block reason is multi-line ("...unresolved failures:",
 * one "step N (Bash, exit 1): ..." line per failing step, then "Fix it or
 * tell the user..."); typing only the first line reads as unfinished. Every
 * non-empty line of `reason` becomes its own typed `block` line, in order.
 * An empty (or whitespace-only) reason -- e.g. a `pass` decision -- yields
 * no lines at all.
 */
function reasonLines(reason: string): TypeLine[] {
  if (reason.trim() === '') return [];
  const parts = reason.split('\n');
  return parts.map((text, i) => ({
    text,
    delayMs: i === parts.length - 1 ? 0 : BLOCK_LINE_DELAY_MS,
    cls: 'block' as const,
  }));
}

/**
 * Turns a replay bundle into the hero terminal's typed transcript: the
 * task prompt (from `summary`), one `cmd` line per `post`/`post_fail` event
 * (with an `exit 1` `fail` line for the failing ones), the assistant's
 * claim, and the block reason typed one line at a time.
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
  lines.push(...reasonLines(bundle.decision.reason));

  return lines;
}
