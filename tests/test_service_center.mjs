import test from 'node:test';
import assert from 'node:assert/strict';

import { buildServiceCenterStatus } from '../web/js/service-center.js';

test('service center is explicitly idle when no persistent service is authorized', () => {
  const status = buildServiceCenterStatus({}, {});
  assert.equal(status.state, 'idle');
  assert.equal(status.enabledCount, 0);
  assert.match(status.summary, /尚未开启/);
  assert.match(status.next, /不会默认访问外部来源/);
});

test('service center combines routine slots and event radar without hiding scope', () => {
  const status = buildServiceCenterStatus({
    config: { enabled: true, tasks: { pre_market: true, close_review: true } },
    runtime: { state: 'waiting' },
    next_service: { at: '2026-08-24T08:45:00+08:00', label: '盘前准备' },
  }, {
    config: { enabled: true }, state: 'ok',
  });
  assert.equal(status.state, 'active');
  assert.equal(status.enabledCount, 3);
  assert.deepEqual(status.enabledItems, ['盘前准备', '收盘复盘', '事件影响雷达']);
  assert.match(status.next, /盘前准备/);
});

test('paused routine is visible in the compact strip', () => {
  const status = buildServiceCenterStatus({
    config: { enabled: true, tasks: { intraday: true } },
    runtime: { state: 'paused' },
  }, {});
  assert.equal(status.state, 'paused');
  assert.equal(status.stateLabel, '已暂停');
  assert.match(status.alert, /暂停/);
});

test('authorized degraded event source becomes a visible warning', () => {
  const status = buildServiceCenterStatus({}, {
    config: { enabled: true }, state: 'degraded',
  });
  assert.equal(status.state, 'warning');
  assert.match(status.alert, /需要检查/);
});

test('disabled event radar does not surface an old degraded runtime state', () => {
  const status = buildServiceCenterStatus({}, {
    config: { enabled: false }, state: 'degraded',
  });
  assert.equal(status.state, 'idle');
  assert.equal(status.alert, '');
});

test('active observation rules are included without adding another service center', () => {
  const status = buildServiceCenterStatus({}, {}, { activeCount: 2 });
  assert.equal(status.state, 'active');
  assert.equal(status.enabledCount, 1);
  assert.deepEqual(status.enabledItems, ['2 条观察规则']);
});
