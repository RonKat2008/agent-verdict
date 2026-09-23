/**
 * Dependency-free replay player for a recorded scenario bundle. Drives the
 * timeline and verdict panel `Timeline.astro`/`VerdictPanel.astro` render
 * server-side: `Timeline`'s rows already carry the `revealed` class and
 * `VerdictPanel`'s probabilities/decision/reason are already at their final
 * values (so the page works with no JavaScript at all). On mount,
 * `ReplayPlayer` resets that DOM to the start of the replay and exposes
 * play/pause/step/restart so a visitor can watch the same events reveal
 * step by step. Rendering is direct DOM mutation; no framework, no
 * dependencies.
 */

import type { Action, Bundle } from '../lib/bundle';

export interface ReplayPlayerOptions {
  reducedMotion?: boolean;
  stepMs?: number;
}

const DEFAULT_STEP_MS = 1100;
const COUNTUP_MS = 600;
const COUNTUP_STEPS = 20;
const COUNTUP_STEP_MS = COUNTUP_MS / COUNTUP_STEPS;

function prefersReducedMotion(): boolean {
  if (typeof matchMedia !== 'function') return false;
  try {
    return matchMedia('(prefers-reduced-motion: reduce)').matches;
  } catch {
    return false;
  }
}

/**
 * The DOM contract's `[data-decision]` text and color: in `shadow` mode the
 * hook never actually blocks, so the interesting fact for a visitor is what
 * it *would* have done. When `decision.would_have` is set and the bundle
 * was recorded in shadow mode, the text reads "would <action>" and the
 * color follows `would_have`; otherwise both follow `decision.action`
 * directly (an `enforce`-mode bundle, or a bundle with no would_have).
 */
export function formatDecision(bundle: Bundle): { text: string; effectiveAction: Action } {
  const { decision, mode } = bundle;
  if (mode === 'shadow' && decision.would_have) {
    return { text: `would ${decision.would_have}`, effectiveAction: decision.would_have };
  }
  return { text: decision.action, effectiveAction: decision.action };
}

/**
 * `formatDecision`'s text, plus the fired rule id when there is one (e.g.
 * "would block · R1") -- the label the timeline's `action` row shows.
 * A bundle with no fired rule (a plain `pass`, or `clean-pass`'s
 * no-evidence path) shows the text alone.
 */
export function decisionLabel(bundle: Bundle): string {
  const { text } = formatDecision(bundle);
  return bundle.decision.rule_id ? `${text} · ${bundle.decision.rule_id}` : text;
}

/**
 * Bundle events don't carry which question a `verdict`-kind event
 * corresponds to: the nth `verdict` event (0-indexed, in `seq` order) maps
 * to `questions[n]`. Both `ReplayPlayer.step()` and `Timeline.astro` (at
 * build time, for the server-rendered row text) use this one function so
 * the mapping can never drift between the two.
 */
export function verdictIndexForEvent(bundle: Bundle, eventIndex: number): number {
  let count = -1;
  for (let i = 0; i <= eventIndex; i += 1) {
    if (bundle.events[i]?.kind === 'verdict') count += 1;
  }
  return count;
}

export class ReplayPlayer {
  private readonly root: HTMLElement;
  private readonly bundle: Bundle;
  private readonly stepMs: number;
  private readonly reducedMotion: boolean;
  private playing = false;
  private timer: ReturnType<typeof setTimeout> | null = null;
  public position = 0;

  constructor(root: HTMLElement, bundle: Bundle, opts: ReplayPlayerOptions = {}) {
    this.root = root;
    this.bundle = bundle;
    this.stepMs = opts.stepMs ?? DEFAULT_STEP_MS;
    this.reducedMotion = opts.reducedMotion ?? prefersReducedMotion();
    this.wireControls();
    this.restart();
    if (this.reducedMotion) {
      this.play();
    }
  }

  private wireControls(): void {
    this.root.querySelector('[data-play]')?.addEventListener('click', () => this.play());
    this.root.querySelector('[data-pause]')?.addEventListener('click', () => this.pause());
    this.root.querySelector('[data-step]')?.addEventListener('click', () => this.step());
    this.root.querySelector('[data-restart]')?.addEventListener('click', () => this.restart());
  }

  play(): void {
    if (this.playing) return;
    if (this.reducedMotion) {
      while (this.position < this.bundle.events.length) {
        this.step();
      }
      return;
    }
    this.playing = true;
    this.scheduleNext();
  }

  private scheduleNext(): void {
    this.timer = setTimeout(() => {
      this.step();
      if (this.position < this.bundle.events.length) {
        this.scheduleNext();
      } else {
        this.playing = false;
        this.timer = null;
      }
    }, this.stepMs);
  }

  pause(): void {
    this.playing = false;
    if (this.timer !== null) {
      clearTimeout(this.timer);
      this.timer = null;
    }
  }

  step(): void {
    if (this.position >= this.bundle.events.length) return;
    const event = this.bundle.events[this.position];
    if (!event) return;

    const row = this.root.querySelector(`[data-seq="${event.seq}"]`);
    row?.classList.add('revealed');

    if (event.kind === 'verdict') {
      const question = this.bundle.questions[verdictIndexForEvent(this.bundle, this.position)];
      if (question) this.animateProbability(question.key, question.answer ?? 0);
    } else if (event.kind === 'action') {
      this.applyDecision();
    }

    this.position += 1;
  }

  restart(): void {
    this.pause();
    this.position = 0;

    this.root.querySelectorAll('.revealed').forEach((el) => el.classList.remove('revealed'));
    this.root.querySelectorAll('[data-rule]').forEach((el) => el.removeAttribute('aria-current'));

    const decisionEl = this.root.querySelector<HTMLElement>('[data-decision]');
    if (decisionEl) {
      decisionEl.textContent = '';
      decisionEl.className = 'decision';
    }
    const reasonEl = this.root.querySelector('[data-reason]');
    if (reasonEl) reasonEl.textContent = '';

    this.root.querySelectorAll('[data-prob]').forEach((el) => {
      el.textContent = '0.00';
      el.classList.remove('prob-visible');
    });
    this.root.querySelectorAll<HTMLElement>('[data-prob-bar]').forEach((el) => {
      el.style.transform = 'scaleX(0)';
    });
  }

  private animateProbability(key: string, target: number): void {
    const cell = this.root.querySelector(`[data-question="${key}"] [data-prob]`);
    const bar = this.root.querySelector<HTMLElement>(`[data-question="${key}"] [data-prob-bar]`);
    if (!cell) return;

    cell.classList.add('prob-visible');

    if (this.reducedMotion) {
      cell.textContent = target.toFixed(2);
      if (bar) bar.style.transform = `scaleX(${target})`;
      return;
    }

    let i = 0;
    const tick = (): void => {
      i += 1;
      const value = target * (i / COUNTUP_STEPS);
      cell.textContent = value.toFixed(2);
      if (bar) bar.style.transform = `scaleX(${value})`;
      if (i < COUNTUP_STEPS) {
        setTimeout(tick, COUNTUP_STEP_MS);
      } else {
        cell.textContent = target.toFixed(2);
        if (bar) bar.style.transform = `scaleX(${target})`;
      }
    };
    setTimeout(tick, COUNTUP_STEP_MS);
  }

  private applyDecision(): void {
    const { decision } = this.bundle;
    const { text, effectiveAction } = formatDecision(this.bundle);

    const decisionEl = this.root.querySelector<HTMLElement>('[data-decision]');
    if (decisionEl) {
      decisionEl.textContent = text;
      decisionEl.className = `decision ${effectiveAction}`;
    }
    const reasonEl = this.root.querySelector('[data-reason]');
    if (reasonEl) reasonEl.textContent = decision.reason;

    if (decision.rule_id) {
      this.root.querySelectorAll('[data-rule]').forEach((el) => {
        if (el.getAttribute('data-rule') === decision.rule_id) {
          el.setAttribute('aria-current', 'true');
        } else {
          el.removeAttribute('aria-current');
        }
      });
    }
  }
}

export type ScenarioSelectHandler = (name: string, tab: HTMLButtonElement) => void;

/**
 * Wires a `role="tablist"` of `role="tab"` buttons (each carrying
 * `data-scenario="<name>"`) for the standard tabs keyboard pattern: click
 * or Enter/Space activates a tab, ArrowLeft/ArrowRight/Home/End move the
 * roving tabindex and focus. `onSelect` fires with the newly-activated
 * tab's scenario name so the caller can swap which `[data-scenario]`
 * section is visible and construct a fresh `ReplayPlayer` for it.
 */
export function initScenarioTabs(tablist: HTMLElement, onSelect: ScenarioSelectHandler): void {
  const tabs = Array.from(tablist.querySelectorAll<HTMLButtonElement>('[role="tab"]'));
  if (tabs.length === 0) return;

  function activate(index: number): void {
    const tab = tabs[index];
    if (!tab) return;
    tabs.forEach((t, i) => {
      const selected = i === index;
      t.setAttribute('aria-selected', selected ? 'true' : 'false');
      t.tabIndex = selected ? 0 : -1;
    });
    tab.focus();
    onSelect(tab.dataset.scenario ?? '', tab);
  }

  tabs.forEach((tab, index) => {
    tab.addEventListener('click', () => activate(index));
    tab.addEventListener('keydown', (event) => {
      switch (event.key) {
        case 'ArrowRight':
          event.preventDefault();
          activate((index + 1) % tabs.length);
          break;
        case 'ArrowLeft':
          event.preventDefault();
          activate((index - 1 + tabs.length) % tabs.length);
          break;
        case 'Home':
          event.preventDefault();
          activate(0);
          break;
        case 'End':
          event.preventDefault();
          activate(tabs.length - 1);
          break;
        default:
          break;
      }
    });
  });
}
