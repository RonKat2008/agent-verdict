/**
 * Dependency-free typing effect for the hero terminal. Appends one
 * `<div class="line {cls}">` per script line, typing characters at a fixed
 * cadence with a blinking block cursor that follows the current line. When
 * `reducedMotion` is on (explicitly, or via
 * `prefers-reduced-motion: reduce`), the whole transcript is written at once
 * and `start()` resolves without waiting on any timer.
 */

export type TypeLineClass = 'prompt' | 'cmd' | 'out' | 'fail' | 'claim' | 'block';

export interface TypeLine {
  text: string;
  delayMs: number;
  cls?: TypeLineClass;
}

export interface TypewriterOptions {
  cps?: number;
  reducedMotion?: boolean;
}

const DEFAULT_CPS = 32;

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}

function prefersReducedMotion(): boolean {
  if (typeof matchMedia !== 'function') return false;
  try {
    return matchMedia('(prefers-reduced-motion: reduce)').matches;
  } catch {
    return false;
  }
}

function lineClassName(cls: TypeLineClass | undefined): string {
  return cls ? `line ${cls}` : 'line';
}

export class Typewriter {
  private readonly el: HTMLElement;
  private readonly lines: TypeLine[];
  private readonly cps: number;
  private readonly reducedMotion: boolean;
  private readonly cursor: HTMLSpanElement;

  constructor(el: HTMLElement, lines: TypeLine[], opts: TypewriterOptions = {}) {
    this.el = el;
    this.lines = lines;
    this.cps = opts.cps ?? DEFAULT_CPS;
    this.reducedMotion = opts.reducedMotion ?? prefersReducedMotion();
    this.cursor = document.createElement('span');
    this.cursor.className = 'cursor';
    this.cursor.setAttribute('aria-hidden', 'true');
  }

  async start(): Promise<void> {
    this.el.textContent = '';
    if (this.reducedMotion) {
      this.renderFinal();
      return;
    }
    await this.typeAll();
  }

  private async typeAll(): Promise<void> {
    const msPerChar = 1000 / this.cps;
    for (const line of this.lines) {
      const div = document.createElement('div');
      div.className = lineClassName(line.cls);
      this.el.appendChild(div);
      div.appendChild(this.cursor);
      for (const ch of line.text) {
        this.cursor.insertAdjacentText('beforebegin', ch);
        // eslint-disable-next-line no-await-in-loop
        await wait(msPerChar);
      }
      if (line.delayMs > 0) {
        // eslint-disable-next-line no-await-in-loop
        await wait(line.delayMs);
      }
    }
    this.cursor.classList.add('done');
  }

  private renderFinal(): void {
    for (const line of this.lines) {
      const div = document.createElement('div');
      div.className = lineClassName(line.cls);
      div.textContent = line.text;
      this.el.appendChild(div);
    }
    this.cursor.classList.add('done');
    this.el.appendChild(this.cursor);
  }
}
