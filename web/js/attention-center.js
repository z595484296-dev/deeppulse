/* 深脉 DeepPulse — 统一提醒中心与注意力调度 */

import { attentionDecision, digestMessage, makeAttentionItem, nextMorning } from './attention.js?v=1.8.0';
import {
  bus, loadAttentionInbox, loadAttentionPreferences, markAttentionRead,
  pushAttentionItem, saveAttentionPreferences,
} from './store.js?v=1.8.0';
import { esc, toast } from './util.js?v=1.8.0';

let navigate = () => {};
let digestTimer = null;
let initialized = false;
let knownIds = new Set();
const $ = selector => document.querySelector(selector);

function timeLabel(timestamp) {
  const date = new Date(Number(timestamp) || Date.now());
  return date.toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false });
}

function render() {
  if (!initialized) return;
  const items = loadAttentionInbox().slice().reverse();
  const unread = items.filter(item => !item.readAt).length;
  const badge = $('#attention-badge');
  badge.textContent = unread > 99 ? '99+' : String(unread);
  badge.hidden = unread === 0;
  $('#attention-count').textContent = unread ? `${unread} 条未读` : '已全部读完';
  const list = $('#attention-list');
  list.innerHTML = items.length ? items.map(item => `
    <article class="attention-item ${item.readAt ? '' : 'unread'}" data-id="${esc(item.id)}">
      <div class="attention-item-head"><b>${esc(item.title)}</b><time>${timeLabel(item.createdAt)}</time></div>
      <p>${esc(item.detail)}</p>
      <small>为什么提醒我：${esc(item.reason)}</small>
      <div class="attention-item-actions">
        ${item.page ? `<button class="btn sm" data-attention-page="${esc(item.page)}">查看</button>` : ''}
        ${item.readAt ? '' : '<button class="btn sm ghost" data-attention-read>标为已读</button>'}
      </div>
    </article>`).join('') : '<div class="attention-empty"><b>现在很安静</b><span>价格到达、阶段变化和重要异动会统一出现在这里。</span></div>';
}

function renderPreferences() {
  const prefs = loadAttentionPreferences();
  $('#attention-mode').value = prefs.mode;
  $('#attention-quiet').checked = prefs.quietEnabled;
  $('#attention-quiet-start').value = prefs.quietStart;
  $('#attention-quiet-end').value = prefs.quietEnd;
  $('#attention-pause').textContent = prefs.pausedUntil && Date.now() < prefs.pausedUntil ? '恢复提醒' : '暂停到明早';
}

function collectPreferences() {
  const previous = loadAttentionPreferences();
  return saveAttentionPreferences({
    ...previous,
    mode: $('#attention-mode').value,
    quietEnabled: $('#attention-quiet').checked,
    quietStart: $('#attention-quiet-start').value,
    quietEnd: $('#attention-quiet-end').value,
  });
}

function scheduleDigest(preferences) {
  if (digestTimer) return;
  digestTimer = setTimeout(() => {
    digestTimer = null;
    const items = loadAttentionInbox().filter(item => !item.readAt && item.delivery === 'digest');
    if (!items.length) return;
    const decision = attentionDecision({ priority: 'medium', delivery: 'immediate', kind: 'digest' }, loadAttentionPreferences());
    if (decision.interrupt) toast(digestMessage(items), 'ok', 7000);
  }, preferences.systemDigestMinutes * 60 * 1000);
}

function deliverItem(item) {
  const prefs = loadAttentionPreferences();
  const decision = attentionDecision(item, prefs);
  if (decision.interrupt) toast(`${item.title}：${item.detail}`, item.priority === 'high' ? 'err' : 'ok', 9000);
  else if (decision.reason === 'digest') scheduleDigest(prefs);
  return decision;
}

export function publishAttention(input) {
  const item = makeAttentionItem(input);
  pushAttentionItem(item);
  return { item, decision: attentionDecision(item, loadAttentionPreferences()) };
}

export function attentionContext() {
  const inbox = loadAttentionInbox();
  return {
    unread: inbox.filter(item => !item.readAt).length,
    preferences: loadAttentionPreferences(),
    recent: inbox.slice(-8).reverse().map(item => ({
      kind: item.kind, priority: item.priority, title: item.title, detail: item.detail,
      reason: item.reason, createdAt: item.createdAt, read: !!item.readAt,
    })),
  };
}

export function initAttentionCenter(options = {}) {
  if (initialized) return;
  initialized = true;
  knownIds = new Set(loadAttentionInbox().map(item => item && item.id).filter(Boolean));
  navigate = typeof options.navigate === 'function' ? options.navigate : navigate;
  const panel = $('#attention-panel');
  const toggle = open => {
    panel.classList.toggle('open', open);
    panel.setAttribute('aria-hidden', String(!open));
    $('#btn-attention').setAttribute('aria-expanded', String(open));
    if (open) render();
  };
  $('#btn-attention').addEventListener('click', () => toggle(!panel.classList.contains('open')));
  $('#attention-close').addEventListener('click', () => toggle(false));
  $('#attention-read-all').addEventListener('click', () => markAttentionRead());
  $('#attention-list').addEventListener('click', event => {
    const card = event.target.closest('.attention-item');
    if (!card) return;
    if (event.target.closest('[data-attention-read]')) markAttentionRead(card.dataset.id);
    const page = event.target.closest('[data-attention-page]')?.dataset.attentionPage;
    if (page) { markAttentionRead(card.dataset.id); navigate(page); toggle(false); }
  });
  ['attention-mode', 'attention-quiet', 'attention-quiet-start', 'attention-quiet-end']
    .forEach(id => $('#' + id).addEventListener('change', collectPreferences));
  $('#attention-pause').addEventListener('click', () => {
    const prefs = loadAttentionPreferences();
    saveAttentionPreferences({ ...prefs, pausedUntil: prefs.pausedUntil && Date.now() < prefs.pausedUntil ? null : nextMorning() });
  });
  bus.addEventListener('attention', event => {
    const items = Array.isArray(event.detail) ? event.detail : loadAttentionInbox();
    const fresh = items.filter(item => item && item.id && !knownIds.has(item.id));
    items.forEach(item => { if (item && item.id) knownIds.add(item.id); });
    fresh.forEach(deliverItem);
    render();
  });
  bus.addEventListener('attention-preferences', renderPreferences);
  document.addEventListener('click', event => {
    if (panel.classList.contains('open') && !panel.contains(event.target) && !event.target.closest('#btn-attention')) toggle(false);
  });
  renderPreferences();
  render();
}
