import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const overview = await readFile(new URL('../web/js/pages/overview.js', import.meta.url), 'utf8');
const api = await readFile(new URL('../web/js/api.js', import.meta.url), 'utf8');
const css = await readFile(new URL('../web/css/app.css', import.meta.url), 'utf8');

test('inactive routine follows trial before persistent authorization', () => {
  assert.match(overview, /用当前数据试一次/);
  assert.match(overview, /试运行结果 · 不会发送/);
  assert.match(overview, /持续开启，仅进入中心/);
  assert.match(overview, /if \(input\.checked\) \{[\s\S]*?input\.checked = false;[\s\S]*?await runRoutineTrial/);
});

test('trial confirmation uses a server-owned expiring preview', () => {
  assert.match(api, /\/api\/routine\/trial'/);
  assert.match(api, /\/api\/routine\/trial\/confirm/);
  assert.match(overview, /routineTrial\.trialId, routineTrial\.profileRevision/);
  assert.match(overview, /authorizationReceipt/);
});

test('trial discloses evidence, delivery and autonomous boundary', () => {
  assert.match(overview, /数据时点/);
  assert.match(overview, /使用依据/);
  assert.match(overview, /持续开启后/);
  assert.match(overview, /运行方式/);
  assert.match(css, /\.routine-trial-facts/);
  assert.match(css, /\.routine-trial-actions \.btn[^}]*min-height:\s*44px/s);
});
