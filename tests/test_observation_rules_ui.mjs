import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const overview = await readFile(new URL('../web/js/pages/overview.js', import.meta.url), 'utf8');
const app = await readFile(new URL('../web/js/app.js', import.meta.url), 'utf8');
const triage = await readFile(new URL('../attention_triage.py', import.meta.url), 'utf8');
const css = await readFile(new URL('../web/css/app.css', import.meta.url), 'utf8');

test('observation rules live inside the existing service center with a confirm preview', () => {
  assert.match(overview, /id="ov-observation-rules"/);
  assert.ok(overview.indexOf('id="ov-observation-rules"') < overview.indexOf('id="ov-event-radar"'));
  assert.match(overview, /data-observation-preview/);
  assert.match(overview, /data-observation-confirm/);
  assert.match(overview, /确认前不创建、不检查/);
  assert.match(css, /\.observation-composer\[hidden\]\s*\{\s*display:\s*none/);
});

test('observation trigger uses a precise typed target and acknowledgment', () => {
  assert.match(triage, /'observation_rule'/);
  assert.match(app, /target\.entityType === 'observation_rule'/);
  assert.match(overview, /observation-rule-open/);
  assert.match(overview, /acknowledge\?\.\(Boolean\(card\)\)/);
});

test('data shortage is not presented as an unmet rule', () => {
  assert.match(overview, /数据不足，暂时无法判断/);
});
