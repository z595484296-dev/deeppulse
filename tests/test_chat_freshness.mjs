import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const source = await readFile(new URL('../web/js/chat-freshness.js', import.meta.url), 'utf8');
const rules = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

const current = { dataDate: '2026-08-21', temp: 52, phase: '发酵期', asOf: 2_000_000 };

test('legacy market answer is visibly historical when its snapshot is missing', () => {
  const result = rules.classifyMessageFreshness({ role: 'bot', sourceQuestion: '今天情绪怎么样' }, current, 2_000_000);
  assert.equal(result.status, 'unknown');
  assert.equal(result.stale, true);
});

test('answer stays current while its market snapshot and short time window match', () => {
  const message = { role: 'bot', sourceQuestion: '当前有什么风险', createdAt: 1_900_000, marketSnapshot: { ...current } };
  assert.equal(rules.classifyMessageFreshness(message, current, 2_000_000).status, 'current');
});

test('temperature or phase change makes the old answer stale', () => {
  const message = { role: 'bot', sourceQuestion: '今天情绪怎么样', createdAt: 1_900_000,
    marketSnapshot: { dataDate: '2026-08-21', temp: 43, phase: '修复期', asOf: 1_900_000 } };
  const result = rules.classifyMessageFreshness(message, current, 2_000_000);
  assert.equal(result.status, 'stale');
  assert.equal(result.reason, 'market_changed');
});

test('market-sensitive answer becomes historical after fifteen minutes', () => {
  const message = { role: 'bot', sourceQuestion: '工业富联现在价格', createdAt: 1_000_000, marketSnapshot: { ...current } };
  assert.equal(rules.classifyMessageFreshness(message, current, 2_000_000).reason, 'time_elapsed');
});

test('navigation acknowledgement is timeless', () => {
  const message = { role: 'bot', sourceQuestion: '打开策略', createdAt: 1 };
  assert.equal(rules.classifyMessageFreshness(message, current, 9_000_000).status, 'timeless');
});

test('missing temperature is not rewritten as zero degrees', () => {
  const snapshot = rules.marketSnapshotFromState({ emotion: { date: '2026-08-21', engine: { temp: null, phase: '发酵期' } } }, 123);
  assert.equal(snapshot.temp, null);
});

test('stale assistant text is excluded from the next model context while user intent remains', () => {
  const rows = [
    { role: 'user', html: '今天情绪怎么样' },
    { role: 'bot', html: '43度修复期', sourceQuestion: '今天情绪怎么样' },
    { role: 'user', html: '再核对一下风险' },
  ];
  const filtered = rules.historyForCurrentMarket(rows, current, 2_000_000);
  assert.deepEqual(filtered.map(row => row.html), ['今天情绪怎么样', '再核对一下风险']);
});
