import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const source = await readFile(new URL('../web/js/attention.js', import.meta.url), 'utf8');
const rules = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

test('user price alert interrupts during quiet hours in balanced mode', () => {
  const now = new Date('2026-08-22T23:00:00+08:00');
  const item = rules.makeAttentionItem({ kind: 'price', priority: 'high', delivery: 'immediate' }, now.getTime());
  const decision = rules.attentionDecision(item, { mode: 'balanced', quietStart: '22:00', quietEnd: '08:00' }, now);
  assert.equal(decision.interrupt, true);
  assert.equal(decision.reason, 'user_price_alert');
});

test('pause until tomorrow suppresses even price alerts', () => {
  const now = new Date('2026-08-22T12:00:00+08:00');
  const item = rules.makeAttentionItem({ kind: 'price', priority: 'high', delivery: 'immediate' }, now.getTime());
  assert.equal(rules.attentionDecision(item, { pausedUntil: now.getTime() + 1000 }, now).interrupt, false);
});

test('system events go to digest instead of individual interruption', () => {
  const now = new Date('2026-08-22T10:00:00+08:00');
  const item = rules.makeAttentionItem({ kind: 'move', priority: 'medium', delivery: 'digest' }, now.getTime());
  assert.deepEqual(rules.attentionDecision(item, { quietEnabled: false }, now), { interrupt: false, reason: 'digest' });
});

test('high-only and center-only modes preserve user control', () => {
  const now = new Date('2026-08-22T10:00:00+08:00');
  const medium = rules.makeAttentionItem({ kind: 'phase', priority: 'medium', delivery: 'immediate' }, now.getTime());
  const high = rules.makeAttentionItem({ kind: 'risk', priority: 'high', delivery: 'immediate' }, now.getTime());
  assert.equal(rules.attentionDecision(medium, { mode: 'high_only', quietEnabled: false }, now).interrupt, false);
  assert.equal(rules.attentionDecision(high, { mode: 'high_only', quietEnabled: false }, now).interrupt, true);
  assert.equal(rules.attentionDecision(high, { mode: 'center_only', quietEnabled: false }, now).interrupt, false);
});

test('overnight quiet range crosses midnight correctly', () => {
  assert.equal(rules.isQuietTime({ quietStart: '22:30', quietEnd: '08:00' }, new Date('2026-08-22T23:00:00+08:00')), true);
  assert.equal(rules.isQuietTime({ quietStart: '22:30', quietEnd: '08:00' }, new Date('2026-08-22T12:00:00+08:00')), false);
});

test('digest explains grouped system updates', () => {
  assert.equal(rules.digestMessage([{ kind: 'phase' }, { kind: 'move' }, { kind: 'move' }]), '市场摘要：1 项阶段变化、2 项盘中异动');
});

test('explicit learned controls reduce noise but keep events in the center', () => {
  const now = new Date('2026-08-22T10:00:00+08:00');
  const phase = rules.makeAttentionItem({ kind: 'phase', priority: 'high', delivery: 'immediate' }, now.getTime());
  const digest = rules.attentionDecision(phase, {
    quietEnabled: false, kindControls: { phase: { delivery: 'digest', reason: 'too_frequent' } },
  }, now);
  const centerOnly = rules.attentionDecision(phase, {
    quietEnabled: false, kindControls: { phase: { delivery: 'center_only', reason: 'irrelevant' } },
  }, now);
  assert.deepEqual(digest, { interrupt: false, reason: 'digest' });
  assert.deepEqual(centerOnly, { interrupt: false, reason: 'learned_center_only' });
});

test('price conditions ignore kind learning controls because the user created them', () => {
  const now = new Date('2026-08-22T10:00:00+08:00');
  const price = rules.makeAttentionItem({ kind: 'price', priority: 'high', delivery: 'immediate' }, now.getTime());
  const decision = rules.attentionDecision(price, {
    quietEnabled: false, kindControls: { price: { delivery: 'center_only', reason: 'irrelevant' } },
  }, now);
  assert.equal(decision.interrupt, true);
  assert.equal(decision.reason, 'user_price_alert');
});

test('expired market events never interrupt', () => {
  const now = new Date('2026-08-22T10:00:00+08:00');
  const item = rules.makeAttentionItem({
    kind: 'move', priority: 'high', delivery: 'immediate', expiresAt: now.getTime() - 1,
  }, now.getTime() - 60_000);
  assert.deepEqual(rules.attentionDecision(item, { quietEnabled: false }, now), { interrupt: false, reason: 'expired' });
});
