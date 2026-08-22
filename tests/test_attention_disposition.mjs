import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const center = await readFile(new URL('../web/js/attention-center.js', import.meta.url), 'utf8');
const app = await readFile(new URL('../web/js/app.js', import.meta.url), 'utf8');
const strategy = await readFile(new URL('../web/js/pages/strategy.js', import.meta.url), 'utf8');
const bridge = await readFile(new URL('../web/js/bridge.js', import.meta.url), 'utf8');

test('opening an attention item acknowledges only after the exact target is found', () => {
  const navigateAt = center.indexOf('Promise.resolve(navigate(group.target, group))');
  const openAt = center.indexOf("mutateAttentionTriage(card.dataset.id, 'open'");
  assert.ok(navigateAt > 0);
  assert.ok(openAt > navigateAt);
  assert.match(center, /if \(!found\) throw new Error\('目标已变化或暂时无法定位，事项仍保持待处理'\)/);
});

test('quality feedback is separated from disposition', () => {
  assert.doesNotMatch(center, /FEEDBACK_LABELS[\s\S]{0,220}done\s*:/);
  assert.match(center, /data-attention-disposition="resolve"/);
  assert.match(center, /data-task-state=/);
});

test('desktop links require a typed target and current fingerprint', () => {
  assert.match(bridge, /attention-target-open/);
  assert.match(bridge, /\^\[a-f0-9\]\{24,64\}\$/i);
  assert.match(app, /navigateAttentionTarget\(target/);
  assert.match(app, /'desktop-deeplink', target\.fingerprint/);
});

test('workflow target expands evidence and acknowledges the concrete card', () => {
  assert.match(strategy, /research-workflow-open/);
  assert.match(strategy, /revealWorkflowAttentionTarget/);
  assert.match(strategy, /acknowledge\?\.\(Boolean\(target\)\)/);
  assert.match(strategy, /workflow-attention-context/);
});
