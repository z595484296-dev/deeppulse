import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const source = await readFile(new URL('../web/js/proactive.js', import.meta.url), 'utf8');
const { buildProactiveBrief } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const FRESH = { asOf: '2026-08-21T15:10:00+08:00', now: new Date('2026-08-21T15:11:00+08:00') };

function healthyEmotion(overrides = {}) {
  return {
    date: '2026-08-21',
    history: [{ date: '2026-08-21', temp: 47, phase: '发酵期' }],
    engine: {
      temp: 47, phase: '发酵期', phase_desc: '赚钱效应扩散，验证主线持续性。',
      coverage: 100, confidence: 99, actionable: true, degraded: false,
      dynamics: { delta1: -8.4, direction: '降温' },
      raw: { up: 2363, down: 2819 },
      ...overrides,
    },
  };
}

test('degraded data gates directional conclusions and prioritizes repair', () => {
  const brief = buildProactiveBrief({
    ...FRESH,
    emotion: healthyEmotion({ coverage: 42, confidence: 51, actionable: false, degraded: true }),
    marketState: 'open',
  });
  assert.equal(brief.degraded, true);
  assert.equal(brief.status, '数据受限');
  assert.equal(brief.actions[0].id, 'repair-data');
  assert.equal(brief.actions[0].page, 'datasrc');
  assert.equal(brief.actions.length, 1);
  assert.match(brief.headline, /先修复数据/);
});

test('informational no-risk fallback does not create a risk task', () => {
  const brief = buildProactiveBrief({
    ...FRESH,
    emotion: healthyEmotion({
      risks: ['当前未触发显著风险规则，仍需关注盘中变化'],
      flags: [{ type: 'info', text: '数据已完成更新' }],
    }),
    marketState: 'open',
  });
  assert.ok(!brief.actions.some(item => item.id === 'verify-risk'));
  assert.ok(!brief.headline.includes('结构风险'));
});

test('risk, close review and watchlist form a maximum-three action loop', () => {
  const emotion = healthyEmotion({
    risks: ['高位负反馈：昨日连板指数 -3.09%'],
    divergences: ['涨停数量偏强，但昨日涨停溢价为负'],
  });
  const brief = buildProactiveBrief({
    ...FRESH,
    emotion,
    marketState: 'closed',
    watchlist: [{ code: '601138', name: '工业富联' }],
    journal: [],
  });
  assert.equal(brief.actions.length, 3);
  assert.deepEqual(brief.actions.map(item => item.id), ['verify-risk', 'close-review', 'review-watch']);
  assert.match(brief.headline, /结构风险/);
  assert.match(brief.prompt, /反证条件/);
});

test('pending alerts outrank generic watchlist review', () => {
  const brief = buildProactiveBrief({
    ...FRESH,
    emotion: healthyEmotion(),
    marketState: 'open',
    watchlist: [{ code: '601138', name: '工业富联' }],
    alerts: [{ id: 'a1', code: '601138', triggered: false }],
  });
  assert.ok(brief.actions.some(item => item.id === 'pending-alerts'));
  assert.ok(!brief.actions.some(item => item.id === 'review-watch'));
});

test('read identity stays stable across period labels when facts and tasks do not change', () => {
  const input = { ...FRESH, emotion: healthyEmotion(), watchlist: [{ code: '601138' }] };
  const openBrief = buildProactiveBrief({ ...input, marketState: 'open' });
  const breakBrief = buildProactiveBrief({ ...input, marketState: 'break' });
  assert.equal(openBrief.id, breakBrief.id);
  assert.notEqual(openBrief.period, breakBrief.period);
});

test('risk content changes produce a new read identity', () => {
  const first = buildProactiveBrief({ ...FRESH, emotion: healthyEmotion({ risks: ['炸板潮待核'] }), marketState: 'closed' });
  const second = buildProactiveBrief({ ...FRESH, emotion: healthyEmotion({ risks: ['跌停扩散待核'] }), marketState: 'closed' });
  assert.notEqual(first.id, second.id);
  assert.notEqual(first.contentHash, second.contentHash);
});

test('ordinary market value refresh changes content without stealing attention again', () => {
  const first = buildProactiveBrief({ ...FRESH, emotion: healthyEmotion(), marketState: 'closed' });
  const second = buildProactiveBrief({
    ...FRESH,
    emotion: healthyEmotion({ temp: 47.2, raw: { up: 2370, down: 2812 } }),
    indices: [{ code: '000001', name: '上证指数', pct: 0.52 }],
    marketState: 'closed',
  });
  assert.equal(first.id, second.id);
  assert.notEqual(first.contentHash, second.contentHash);
});

test('stale payload and old trade date are gated during trading', () => {
  const stale = buildProactiveBrief({
    emotion: healthyEmotion(), marketState: 'open',
    asOf: '2026-08-21T14:00:00+08:00', now: new Date('2026-08-21T15:00:00+08:00'),
  });
  assert.equal(stale.degraded, true);
  assert.equal(stale.stale, true);
  assert.equal(stale.actions[0].id, 'repair-data');
});

test('weekend keeps the latest trading-day close snapshot valid and labels it clearly', () => {
  const brief = buildProactiveBrief({
    emotion: healthyEmotion(), marketState: 'closed',
    asOf: '2026-08-21T15:10:00+08:00', now: new Date('2026-08-22T10:00:00+08:00'),
  });
  assert.equal(brief.stale, false);
  assert.equal(brief.degraded, false);
  assert.ok(brief.evidence.some(item => item.includes('上一交易日快照')));
});

test('brief remains useful before the first market payload', () => {
  const brief = buildProactiveBrief({ marketState: 'pre' });
  assert.equal(brief.degraded, true);
  assert.ok(brief.actions.length >= 1);
  assert.equal(brief.facts.length, 0);
  assert.ok(!brief.headline.includes('0°'));
  assert.ok(brief.evidence.some(item => item.includes('数据日待确认')));
});
