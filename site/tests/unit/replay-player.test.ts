import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { Bundle } from '../../src/lib/bundle';
import { parseBundle } from '../../src/lib/bundle';
import {
  ReplayPlayer,
  autoplayState,
  decisionLabel,
  formatDecision,
  initScenarioTabs,
  verdictIndexForEvent,
} from '../../src/islands/replay-player';
import fixture from '../../src/data/fixtures/unreported-failure.json';

/** jsdom has no `IntersectionObserver`; this records enough of the real API
 * for `ReplayPlayer.setupAutoplayObserver` and lets a test fire a fake
 * intersection entry on demand. */
class MockIntersectionObserver {
  static instances: MockIntersectionObserver[] = [];
  readonly callback: IntersectionObserverCallback;
  observedElement: Element | null = null;
  disconnected = false;

  constructor(callback: IntersectionObserverCallback) {
    this.callback = callback;
    MockIntersectionObserver.instances.push(this);
  }

  observe(el: Element): void {
    this.observedElement = el;
  }

  unobserve(): void {}

  disconnect(): void {
    this.disconnected = true;
  }

  trigger(isIntersecting: boolean): void {
    const entry = { isIntersecting, target: this.observedElement } as IntersectionObserverEntry;
    this.callback([entry], this as unknown as IntersectionObserver);
  }
}

/**
 * Builds the same DOM shape `Timeline.astro` and `VerdictPanel.astro`
 * render, at the server-rendered, no-JS state -- every reveal-gated element
 * carries `reveal ssr` (not `revealed`) and its final content, exactly as
 * `ReplayPlayer` finds it before its first `restart()` strips `ssr` and
 * resets everything. Selectors match the DOM contract in task-5-brief.md
 * exactly.
 */
function buildRoot(bundle: Bundle): HTMLElement {
  const root = document.createElement('div');

  const timeline = document.createElement('div');
  timeline.setAttribute('data-timeline', '');
  bundle.events.forEach((event, index) => {
    const row = document.createElement('div');
    row.className = 'row reveal ssr';
    row.dataset.seq = String(event.seq);

    // Mirrors Timeline.astro's server-rendered command cell exactly (same
    // shared helpers), since that text is static -- ReplayPlayer never
    // writes it.
    const command = document.createElement('span');
    command.className = 'cell command mono';
    if (event.kind === 'verdict') {
      const question = bundle.questions[verdictIndexForEvent(bundle, index)];
      if (question) {
        command.innerHTML = `<span class="ev-key">${question.key}</span> <span class="ev-value">${(question.answer ?? 0).toFixed(2)}</span>`;
      }
    } else if (event.kind === 'action') {
      const { effectiveAction } = formatDecision(bundle);
      command.innerHTML = `<span class="ev-decision ${effectiveAction}">${decisionLabel(bundle)}</span>`;
    } else if (event.kind === 'stop') {
      command.innerHTML = '<span class="ev-stop">final message</span>';
    } else {
      command.textContent = event.command;
    }
    row.appendChild(command);
    timeline.appendChild(row);
  });
  root.appendChild(timeline);

  const panel = document.createElement('div');
  panel.setAttribute('data-verdict-panel', '');
  for (const q of bundle.questions) {
    const wrap = document.createElement('div');
    wrap.dataset.question = q.key;
    const probRow = document.createElement('div');
    probRow.className = 'prob-row reveal ssr';
    const prob = document.createElement('span');
    prob.setAttribute('data-prob', '');
    prob.textContent = (q.answer ?? 0).toFixed(2);
    const bar = document.createElement('span');
    bar.setAttribute('data-prob-bar', '');
    probRow.append(prob, bar);
    wrap.appendChild(probRow);
    panel.appendChild(wrap);
  }
  for (const ruleId of ['R1', 'R2', 'R3', 'R4']) {
    const row = document.createElement('div');
    row.setAttribute('data-rule', ruleId);
    if (bundle.decision.rule_id === ruleId) row.setAttribute('aria-current', 'true');
    panel.appendChild(row);
  }
  const decision = document.createElement('p');
  decision.className = 'decision reveal ssr';
  decision.setAttribute('data-decision', '');
  decision.setAttribute('aria-live', 'polite');
  decision.setAttribute('aria-atomic', 'true');
  const reason = document.createElement('pre');
  reason.className = 'reason reveal ssr';
  reason.setAttribute('data-reason', '');
  panel.append(decision, reason);
  root.appendChild(panel);

  const controls = document.createElement('div');
  for (const attr of ['data-play', 'data-pause', 'data-step', 'data-restart']) {
    const btn = document.createElement('button');
    btn.setAttribute(attr, '');
    btn.setAttribute('type', 'button');
    controls.appendChild(btn);
  }
  root.appendChild(controls);

  document.body.appendChild(root);
  return root;
}

describe('ReplayPlayer', () => {
  let bundle: Bundle;
  let root: HTMLElement;

  beforeEach(() => {
    bundle = parseBundle(fixture);
    root = buildRoot(bundle);
  });

  afterEach(() => {
    root.remove();
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    autoplayState.done = false;
    MockIntersectionObserver.instances = [];
  });

  it('mounting on SSR markup strips ssr and leaves rows unrevealed', () => {
    vi.useFakeTimers();
    expect(root.querySelectorAll('.ssr').length).toBeGreaterThan(0);

    const player = new ReplayPlayer(root, bundle, { reducedMotion: false });

    expect(player.position).toBe(0);
    expect(root.querySelectorAll('.ssr')).toHaveLength(0);
    expect(root.querySelectorAll('.revealed')).toHaveLength(0);
  });

  it('step() reveals rows in seq order and increments position', () => {
    vi.useFakeTimers();
    const player = new ReplayPlayer(root, bundle, { reducedMotion: false });

    player.step();
    expect(player.position).toBe(1);
    expect(root.querySelector('[data-seq="1"]')?.classList.contains('revealed')).toBe(true);
    expect(root.querySelector('[data-seq="2"]')?.classList.contains('revealed')).toBe(false);

    player.step();
    expect(player.position).toBe(2);
    expect(root.querySelector('[data-seq="2"]')?.classList.contains('revealed')).toBe(true);
  });

  it('after stepping past the last event, probabilities, rule, decision, and reason are final', async () => {
    vi.useFakeTimers();
    const player = new ReplayPlayer(root, bundle, { reducedMotion: false });

    for (let i = 0; i < bundle.events.length; i += 1) {
      player.step();
    }
    await vi.advanceTimersByTimeAsync(1000);

    for (const q of bundle.questions) {
      const cell = root.querySelector(`[data-question="${q.key}"] [data-prob]`);
      expect(cell?.textContent).toBe((q.answer ?? 0).toFixed(2));
    }

    const firedRow = root.querySelector(`[data-rule="${bundle.decision.rule_id}"]`);
    expect(firedRow?.getAttribute('aria-current')).toBe('true');
    for (const ruleId of ['R1', 'R2', 'R3', 'R4']) {
      if (ruleId === bundle.decision.rule_id) continue;
      expect(root.querySelector(`[data-rule="${ruleId}"]`)?.getAttribute('aria-current')).toBeNull();
    }

    const decisionText = root.querySelector('[data-decision]')?.textContent;
    expect(decisionText).toBe('would block');
    expect(root.querySelector('[data-reason]')?.textContent).toBe(bundle.decision.reason);

    // The timeline's own verdict/action rows (static text, server-rendered
    // by Timeline.astro -- ReplayPlayer never writes these) still read
    // correctly once every row is revealed.
    const verdictSeq = bundle.events.find((e) => e.kind === 'verdict')?.seq;
    const verdictRow = root.querySelector(`[data-seq="${verdictSeq}"] .cell.command`);
    expect(verdictRow?.textContent?.trim()).toBe('claims_done 0.92');

    const actionSeq = bundle.events.find((e) => e.kind === 'action')?.seq;
    const actionRow = root.querySelector(`[data-seq="${actionSeq}"] .cell.command`);
    expect(actionRow?.textContent?.trim()).toBe('would block · R1');
  });

  it('restart() removes every revealed class and resets position to 0', () => {
    vi.useFakeTimers();
    const player = new ReplayPlayer(root, bundle, { reducedMotion: false });
    player.step();
    player.step();
    expect(player.position).toBe(2);

    player.restart();

    expect(player.position).toBe(0);
    expect(root.querySelectorAll('.revealed')).toHaveLength(0);
  });

  it('play() with reducedMotion reveals everything synchronously', () => {
    const player = new ReplayPlayer(root, bundle, { reducedMotion: true });
    player.play();

    expect(player.position).toBe(bundle.events.length);
    expect(root.querySelectorAll('.row.revealed')).toHaveLength(bundle.events.length);
    expect(root.querySelector('[data-decision]')?.textContent).toBe('would block');
    for (const q of bundle.questions) {
      const cell = root.querySelector(`[data-question="${q.key}"] [data-prob]`);
      expect(cell?.textContent).toBe((q.answer ?? 0).toFixed(2));
    }
  });

  it('play() advances one step per stepMs under fake timers', async () => {
    vi.useFakeTimers();
    const player = new ReplayPlayer(root, bundle, { reducedMotion: false, stepMs: 100 });

    player.play();
    expect(player.position).toBe(0);

    await vi.advanceTimersByTimeAsync(100);
    expect(player.position).toBe(1);

    await vi.advanceTimersByTimeAsync(100);
    expect(player.position).toBe(2);

    player.pause();
    const posAfterPause = player.position;
    await vi.advanceTimersByTimeAsync(500);
    expect(player.position).toBe(posAfterPause);
  });

  it('clicking the control buttons drives the same methods', () => {
    vi.useFakeTimers();
    const player = new ReplayPlayer(root, bundle, { reducedMotion: false });
    root.querySelector<HTMLButtonElement>('[data-step]')?.click();
    expect(player.position).toBe(1);
    root.querySelector<HTMLButtonElement>('[data-restart]')?.click();
    expect(player.position).toBe(0);
  });

  it('autoplays once the band scrolls into view (IntersectionObserver)', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('IntersectionObserver', MockIntersectionObserver);
    const player = new ReplayPlayer(root, bundle, { reducedMotion: false, stepMs: 100 });
    expect(player.position).toBe(0);

    const observer = MockIntersectionObserver.instances.at(-1);
    expect(observer?.observedElement).toBe(root);

    observer?.trigger(true);
    await vi.advanceTimersByTimeAsync(100);

    expect(player.position).toBe(1);
    expect(observer?.disconnected).toBe(true);
  });

  it('does not autoplay a second time on this page load', () => {
    vi.useFakeTimers();
    vi.stubGlobal('IntersectionObserver', MockIntersectionObserver);
    autoplayState.done = true;

    new ReplayPlayer(root, bundle, { reducedMotion: false });

    expect(MockIntersectionObserver.instances).toHaveLength(0);
  });
});

describe('formatDecision', () => {
  it('renders "would <action>" for a shadow-mode bundle with a would_have', () => {
    const bundle = parseBundle(fixture);
    expect(formatDecision(bundle)).toEqual({ text: 'would block', effectiveAction: 'block' });
  });

  it('renders the plain action when mode is not shadow', () => {
    const bundle = parseBundle(fixture);
    const enforceBundle: Bundle = { ...bundle, mode: 'enforce' };
    expect(formatDecision(enforceBundle)).toEqual({ text: 'pass', effectiveAction: 'pass' });
  });

  it('renders the plain action when would_have is null', () => {
    const bundle = parseBundle(fixture);
    const passBundle: Bundle = {
      ...bundle,
      decision: { ...bundle.decision, action: 'pass', would_have: null },
    };
    expect(formatDecision(passBundle)).toEqual({ text: 'pass', effectiveAction: 'pass' });
  });

  it('renders gate_unavailable as "no verdict (provider unavailable)"', () => {
    const bundle = parseBundle(fixture);
    const gateBundle: Bundle = {
      ...bundle,
      decision: { ...bundle.decision, action: 'gate_unavailable', would_have: null, rule_id: null },
    };
    expect(formatDecision(gateBundle)).toEqual({
      text: 'no verdict (provider unavailable)',
      effectiveAction: 'gate_unavailable',
    });
  });
});

describe('initScenarioTabs', () => {
  function buildTabs(names: string[]): { tablist: HTMLElement; tabs: HTMLButtonElement[] } {
    const tablist = document.createElement('div');
    tablist.setAttribute('role', 'tablist');
    const tabs = names.map((name, i) => {
      const tab = document.createElement('button');
      tab.setAttribute('role', 'tab');
      tab.dataset.scenario = name;
      tab.setAttribute('aria-selected', i === 0 ? 'true' : 'false');
      tab.tabIndex = i === 0 ? 0 : -1;
      tablist.appendChild(tab);
      return tab;
    });
    document.body.appendChild(tablist);
    return { tablist, tabs };
  }

  afterEach(() => {
    document.body.innerHTML = '';
  });

  it('ArrowRight moves aria-selected and focus to the next tab', () => {
    const { tablist, tabs } = buildTabs(['a', 'b', 'c']);
    const onSelect = vi.fn();
    initScenarioTabs(tablist, onSelect);

    tabs[0]?.focus();
    tabs[0]?.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));

    expect(tabs[0]?.getAttribute('aria-selected')).toBe('false');
    expect(tabs[1]?.getAttribute('aria-selected')).toBe('true');
    expect(document.activeElement).toBe(tabs[1]);
    expect(onSelect).toHaveBeenCalledWith('b', tabs[1]);
  });

  it('ArrowRight wraps from the last tab to the first', () => {
    const { tablist, tabs } = buildTabs(['a', 'b']);
    tabs[1]?.setAttribute('aria-selected', 'true');
    tabs[0]?.setAttribute('aria-selected', 'false');
    initScenarioTabs(tablist, vi.fn());

    tabs[1]?.focus();
    tabs[1]?.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));

    expect(tabs[0]?.getAttribute('aria-selected')).toBe('true');
    expect(document.activeElement).toBe(tabs[0]);
  });
});
