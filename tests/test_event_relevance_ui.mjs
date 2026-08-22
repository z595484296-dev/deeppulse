import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const center = await readFile(new URL('../web/js/attention-center.js', import.meta.url), 'utf8');
const api = await readFile(new URL('../web/js/api.js', import.meta.url), 'utf8');
const css = await readFile(new URL('../web/css/app.css', import.meta.url), 'utf8');

test('negative event feedback opens a scoped preview instead of mutating immediately', () => {
  assert.match(center, /previewEventRelevance/);
  assert.match(center, /确认精准降噪/);
  assert.match(center, /仅调整/);
  assert.match(center, /只记录这一次/);
  assert.match(center, /不影响其他股票、其他主题、价格提醒和原始证据/);
  assert.ok(center.indexOf("group?.kind === 'event'") < center.indexOf('api.mutateAttentionTriage(card.dataset.id, \'feedback\', signal'));
});

test('confirmed relevance control has a persistent receipt and restore action', () => {
  assert.match(center, /精准降噪已生效/);
  assert.match(center, /data-relevance-restore/);
  assert.match(center, /mutateEventRelevance\(restoreControl, 'restore'\)/);
  assert.match(api, /\/api\/event-relevance\/preview/);
  assert.match(api, /\/api\/event-relevance\/confirm/);
  assert.match(api, /\/api\/event-relevance\/action/);
});

test('mobile confirmation controls remain touch friendly', () => {
  assert.match(css, /\.attention-noise-draft/);
  assert.match(css, /\.attention-noise-actions \.btn[^}]*min-height:\s*44px/s);
});
