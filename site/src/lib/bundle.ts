/**
 * TypeScript mirror of `site/scripts/replay_schema.py`. Key sets, enums, and
 * caps here must match that file exactly -- it is the one source of truth
 * for the replay bundle's closed shape. Do not add a field here without
 * adding it there first.
 */

export const CAPS = {
  command: 80,
  final_message: 600,
  reason: 2000,
  summary: 240,
  title: 60,
  statement: 300,
} as const;

export const TOP_KEYS = [
  'name',
  'title',
  'summary',
  'staged_claim',
  'mode',
  'recorded_at',
  'claude_code_version',
  'model_returned',
  'events',
  'final_message',
  'questions',
  'decision',
] as const;

export const EVENT_KEYS = [
  'seq',
  'kind',
  't_ms',
  'tool',
  'command',
  'status',
  'exit_code',
  'decision',
  'rule_id',
] as const;

export const EVENT_KINDS = [
  'prompt',
  'pre',
  'post',
  'post_fail',
  'stop',
  'verdict',
  'action',
] as const;

export const QUESTION_KEYS = ['key', 'type', 'statement', 'answer'] as const;
export const DECISION_KEYS = ['action', 'would_have', 'rule_id', 'threshold_used', 'reason'] as const;
export const ACTIONS = ['pass', 'flag', 'block', 'gate_unavailable'] as const;
export const TOOLS = ['Bash', 'Read', 'Write', 'Edit', 'NotebookEdit', 'WebFetch', 'Agent', 'mcp'] as const;
export const QUESTION_TYPES = ['noul', 'score', 'choice'] as const;
export const STATUS_VALUES = ['ok', 'error'] as const;
export const EVENT_DECISION_VALUES = ['deny', 'ask'] as const;

export type EventKind = (typeof EVENT_KINDS)[number];
export type Action = (typeof ACTIONS)[number];
export type Tool = (typeof TOOLS)[number];
export type QuestionType = (typeof QUESTION_TYPES)[number];
export type Status = (typeof STATUS_VALUES)[number];
export type EventDecision = (typeof EVENT_DECISION_VALUES)[number];

export interface BundleEvent {
  seq: number;
  kind: EventKind;
  t_ms: number;
  tool: Tool | null;
  command: string;
  status: Status | null;
  exit_code: number | null;
  decision: EventDecision | null;
  rule_id: string | null;
}

export interface Question {
  key: string;
  type: QuestionType;
  statement: string;
  answer: number | null;
}

export interface Decision {
  action: Action;
  would_have: Action | null;
  rule_id: string | null;
  threshold_used: number | null;
  reason: string;
}

export interface Bundle {
  name: string;
  title: string;
  summary: string;
  staged_claim: boolean;
  mode: string;
  recorded_at: string;
  claude_code_version: string;
  model_returned: string | null;
  events: BundleEvent[];
  final_message: string;
  questions: Question[];
  decision: Decision;
}

export class BundleShapeError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'BundleShapeError';
  }
}

function fail(path: string, message: string): never {
  throw new BundleShapeError(`${path}: ${message}`);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isInt(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value);
}

function isNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function checkKeys(path: string, obj: Record<string, unknown>, keys: readonly string[]): void {
  const actual = new Set(Object.keys(obj));
  const expected = new Set(keys);
  const diff = [...actual].filter((k) => !expected.has(k)).concat(
    [...expected].filter((k) => !actual.has(k)),
  );
  if (diff.length > 0) {
    fail(path, `keys ${JSON.stringify([...new Set(diff)].sort())}`);
  }
}

function checkString(path: string, value: unknown, cap?: number): string {
  if (typeof value !== 'string') {
    fail(path, `expected str, got ${typeof value}`);
  }
  if (cap !== undefined && value.length > cap) {
    fail(path, `exceeds ${cap} chars`);
  }
  return value;
}

function checkNullableString(path: string, value: unknown, cap?: number): string | null {
  if (value === null) return null;
  return checkString(path, value, cap);
}

function checkEnum<T extends string>(path: string, value: unknown, allowed: readonly T[]): T {
  const s = checkString(path, value);
  if (!allowed.includes(s as T)) {
    fail(path, `${JSON.stringify(s)} not in ${JSON.stringify(allowed)}`);
  }
  return s as T;
}

function checkNullableEnum<T extends string>(
  path: string,
  value: unknown,
  allowed: readonly T[],
): T | null {
  if (value === null) return null;
  return checkEnum(path, value, allowed);
}

function checkBool(path: string, value: unknown): boolean {
  if (typeof value !== 'boolean') {
    fail(path, `expected bool, got ${typeof value}`);
  }
  return value;
}

function checkInt(path: string, value: unknown): number {
  if (!isInt(value)) {
    fail(path, `expected int, got ${typeof value}`);
  }
  return value;
}

function checkNullableInt(path: string, value: unknown): number | null {
  if (value === null) return null;
  return checkInt(path, value);
}

function checkNullableNumber(path: string, value: unknown): number | null {
  if (value === null) return null;
  if (!isNumber(value)) {
    fail(path, `expected number, got ${typeof value}`);
  }
  return value;
}

function checkEvent(value: unknown, i: number): BundleEvent {
  const path = `events[${i}]`;
  if (!isRecord(value)) fail(path, `expected object, got ${typeof value}`);
  checkKeys(path, value, EVENT_KEYS);
  const seq = checkInt(`${path}.seq`, value.seq);
  if (seq !== i + 1) fail(path, `seq ${seq} out of order`);
  return {
    seq,
    kind: checkEnum(`${path}.kind`, value.kind, EVENT_KINDS),
    t_ms: checkInt(`${path}.t_ms`, value.t_ms),
    tool: checkNullableEnum(`${path}.tool`, value.tool, TOOLS),
    command: checkString(`${path}.command`, value.command, CAPS.command),
    status: checkNullableEnum(`${path}.status`, value.status, STATUS_VALUES),
    exit_code: checkNullableInt(`${path}.exit_code`, value.exit_code),
    decision: checkNullableEnum(`${path}.decision`, value.decision, EVENT_DECISION_VALUES),
    rule_id: checkNullableString(`${path}.rule_id`, value.rule_id),
  };
}

function checkQuestion(value: unknown, i: number): Question {
  const path = `questions[${i}]`;
  if (!isRecord(value)) fail(path, `expected object, got ${typeof value}`);
  checkKeys(path, value, QUESTION_KEYS);
  return {
    key: checkString(`${path}.key`, value.key),
    type: checkEnum(`${path}.type`, value.type, QUESTION_TYPES),
    statement: checkString(`${path}.statement`, value.statement, CAPS.statement),
    answer: checkNullableNumber(`${path}.answer`, value.answer),
  };
}

function checkDecision(value: unknown): Decision {
  const path = 'decision';
  if (!isRecord(value)) fail(path, `expected object, got ${typeof value}`);
  checkKeys(path, value, DECISION_KEYS);
  return {
    action: checkEnum(`${path}.action`, value.action, ACTIONS),
    would_have: checkNullableEnum(`${path}.would_have`, value.would_have, ACTIONS),
    rule_id: checkNullableString(`${path}.rule_id`, value.rule_id),
    threshold_used: checkNullableNumber(`${path}.threshold_used`, value.threshold_used),
    reason: checkString(`${path}.reason`, value.reason, CAPS.reason),
  };
}

export function parseBundle(json: unknown): Bundle {
  const path = '$';
  if (!isRecord(json)) {
    fail(path, `expected object, got ${typeof json}`);
  }
  checkKeys(path, json, TOP_KEYS);

  const events = json.events;
  if (!Array.isArray(events)) fail('events', `expected array, got ${typeof events}`);
  const questions = json.questions;
  if (!Array.isArray(questions)) fail('questions', `expected array, got ${typeof questions}`);

  return {
    name: checkString('name', json.name),
    title: checkString('title', json.title, CAPS.title),
    summary: checkString('summary', json.summary, CAPS.summary),
    staged_claim: checkBool('staged_claim', json.staged_claim),
    mode: checkString('mode', json.mode),
    recorded_at: checkString('recorded_at', json.recorded_at),
    claude_code_version: checkString('claude_code_version', json.claude_code_version),
    model_returned: checkNullableString('model_returned', json.model_returned),
    events: events.map((e, i) => checkEvent(e, i)),
    final_message: checkString('final_message', json.final_message, CAPS.final_message),
    questions: questions.map((q, i) => checkQuestion(q, i)),
    decision: checkDecision(json.decision),
  };
}
