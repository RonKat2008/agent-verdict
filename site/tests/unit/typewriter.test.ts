import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { Typewriter, type TypeLine } from '../../src/islands/typewriter';

describe('Typewriter', () => {
  let el: HTMLDivElement;

  beforeEach(() => {
    el = document.createElement('div');
    document.body.appendChild(el);
  });

  afterEach(() => {
    el.remove();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('types every character in order', async () => {
    vi.useFakeTimers();
    const lines: TypeLine[] = [
      { text: 'ok', delayMs: 0, cls: 'cmd' },
      { text: 'go', delayMs: 0, cls: 'prompt' },
    ];
    const tw = new Typewriter(el, lines, { cps: 10, reducedMotion: false });
    const done = tw.start();
    const firstLine = () => el.querySelectorAll('.line')[0] as HTMLElement;
    const secondLine = () => el.querySelectorAll('.line')[1] as HTMLElement | undefined;

    // The first character of the first line is typed synchronously, before
    // the first per-character timer is even scheduled.
    expect(firstLine().textContent).toBe('o');

    await vi.advanceTimersByTimeAsync(100);
    expect(firstLine().textContent).toBe('ok');

    // second line has not started typing yet
    expect(secondLine()).toBeUndefined();

    await vi.advanceTimersByTimeAsync(100);
    expect(secondLine()?.textContent).toBe('g');
    await vi.advanceTimersByTimeAsync(100);
    expect(secondLine()?.textContent).toBe('go');
    await vi.advanceTimersByTimeAsync(100);

    await done;
  });

  it('reducedMotion renders the full transcript synchronously and resolves', async () => {
    const lines: TypeLine[] = [
      { text: 'hello', delayMs: 500, cls: 'prompt' },
      { text: 'world', delayMs: 500, cls: 'cmd' },
    ];
    const tw = new Typewriter(el, lines, { reducedMotion: true });

    await tw.start();

    const rendered = Array.from(el.querySelectorAll('.line')).map((n) => n.textContent);
    expect(rendered).toEqual(['hello', 'world']);
  });

  it('the cursor element exists while typing and gets class done after', async () => {
    vi.useFakeTimers();
    const lines: TypeLine[] = [{ text: 'x', delayMs: 0, cls: 'cmd' }];
    const tw = new Typewriter(el, lines, { cps: 10, reducedMotion: false });
    const done = tw.start();

    await vi.advanceTimersByTimeAsync(0);
    const cursorWhileTyping = el.querySelector('.cursor');
    expect(cursorWhileTyping).not.toBeNull();
    expect(cursorWhileTyping?.classList.contains('done')).toBe(false);

    await vi.advanceTimersByTimeAsync(100);
    await done;

    const cursorAfter = el.querySelector('.cursor');
    expect(cursorAfter).not.toBeNull();
    expect(cursorAfter?.classList.contains('done')).toBe(true);
  });
});
