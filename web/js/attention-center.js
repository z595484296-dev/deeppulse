/* 深脉 DeepPulse — 统一提醒中心与注意力调度 */

import { attentionDecision, digestMessage, makeAttentionItem, nextMorning } from './attention.js?v=1.16.0';
import { api } from './api.js?v=1.16.0';
import {
  attentionLearningContext, bus, feedbackAttentionItem, loadAttentionInbox, loadAttentionPreferences, markAttentionRead,
  pushAttentionItem, resetAttentionLearning, saveAttentionPreferences,
} from './store.js?v=1.16.0';
import { esc, toast } from './util.js?v=1.16.0';

let navigate = () => {};
let digestTimer = null;
let initialized = false;
let knownIds = new Set();
let deliverySnapshot = { channels: {}, recent: [] };
const $ = selector => document.querySelector(selector);
const KIND_LABELS = { phase: '情绪阶段', move: '盘中异动', price: '价格条件', routine: '主动日程', event: '事件影响', hypothesis_review: '假设复盘', system: '系统更新' };
const FEEDBACK_LABELS = { helpful: '有用', done: '已完成', too_frequent: '少一点', irrelevant: '不相关' };

function isExpired(item, now = Date.now()) {
  return Number(item?.expiresAt) > 0 && now >= Number(item.expiresAt);
}

function timeLabel(timestamp) {
  const date = new Date(Number(timestamp) || Date.now());
  return date.toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false });
}

function deliveryTrace(itemId) {
  const labels = { desktop: 'Windows', epaper: '墨水屏' };
  const statusLabels = { queued: '等待重试', claimed: '发送中', delivered: '已送达', failed: '失败', dismissed: '已忽略' };
  const latest = new Map();
  (deliverySnapshot.recent || []).forEach(row => {
    if (row?.itemId === itemId && !latest.has(row.channel)) latest.set(row.channel, row);
  });
  if (!latest.size) return '';
  return `<div class="attention-delivery-trace" aria-label="终端送达记录">${[...latest.values()].map(row => {
    const when = row.deliveredAt || row.acknowledgedAt || row.claimedAt;
    const failed = row.status === 'failed';
    return `<div class="attention-delivery-chip ${esc(row.status || '')}">
      <span>${esc(labels[row.channel] || row.channel)} · ${esc(statusLabels[row.status] || row.status || '未知')}${when ? ` · ${timeLabel(when)}` : ''}${Number(row.attempts) > 1 ? ` · 第 ${Number(row.attempts)} 次` : ''}</span>
      ${failed ? `<button class="attention-retry" data-delivery-retry="${esc(row.channel)}" title="重新排队投递">重试</button>` : ''}
      ${failed && row.error ? `<small>${esc(row.error)}</small>` : ''}
    </div>`;
  }).join('')}</div>`;
}

function render() {
  if (!initialized) return;
  const items = loadAttentionInbox().slice().reverse();
  const unread = items.filter(item => !item.readAt && !isExpired(item)).length;
  const badge = $('#attention-badge');
  badge.textContent = unread > 99 ? '99+' : String(unread);
  badge.hidden = unread === 0;
  $('#attention-count').textContent = unread ? `${unread} 条未读` : '已全部读完';
  const list = $('#attention-list');
  list.innerHTML = items.length ? items.map(item => `
    <article class="attention-item ${item.readAt || isExpired(item) ? '' : 'unread'} ${isExpired(item) ? 'expired' : ''}" data-id="${esc(item.id)}">
      <div class="attention-item-head"><b>${esc(item.title)}</b><time>${isExpired(item) ? '已过期 · ' : ''}${timeLabel(item.createdAt)}</time></div>
      <p>${esc(item.detail)}</p>
      <small>为什么提醒我：${esc(item.reason)}</small>
      ${deliveryTrace(item.id)}
      <div class="attention-item-actions">
        ${item.page ? `<button class="btn sm" data-attention-page="${esc(item.page)}">查看</button>` : ''}
        ${item.readAt || isExpired(item) ? '' : '<button class="btn sm ghost" data-attention-read>已读</button>'}
      </div>
      <div class="attention-item-feedback" aria-label="告诉深脉这条提醒是否有用">
        ${['helpful', 'done', ...(item.kind === 'price' ? [] : ['too_frequent', 'irrelevant'])].map(signal => `
          <button class="attention-feedback-btn ${item.feedback === signal ? 'selected' : ''}" data-attention-feedback="${signal}" aria-pressed="${item.feedback === signal}">${FEEDBACK_LABELS[signal]}</button>
        `).join('')}
      </div>
    </article>`).join('') : '<div class="attention-empty"><b>现在很安静</b><span>价格到达、阶段变化和重要异动会统一出现在这里。</span></div>';
  renderLearning();
}

function renderLearning() {
  const learning = attentionLearningContext();
  const controls = learning.controls || [];
  $('#attention-learning-summary').textContent = learning.feedbackCount
    ? `已根据 ${learning.feedbackCount} 次明确反馈调整；当前 ${learning.activeControls} 类提醒已降噪。`
    : '还没有学习记录。你的明确反馈才会改变提醒方式。';
  $('#attention-learning-controls').innerHTML = controls.length ? controls.map(control => `
    <div class="attention-learning-row">
      <span><b>${esc(KIND_LABELS[control.kind] || control.kind)}</b><small>${control.delivery === 'center_only' ? '仅收入中心' : '合并为摘要'}</small></span>
      <button class="btn sm ghost" data-learning-reset="${esc(control.kind)}">恢复</button>
    </div>`).join('') : '<span class="attention-learning-empty">没有生效中的分类调整</span>';
  $('#attention-learning-reset').disabled = controls.length === 0;
  $('#attention-learning-clear').disabled = learning.feedbackCount === 0;
}

function renderPreferences() {
  const prefs = loadAttentionPreferences();
  $('#attention-mode').value = prefs.mode;
  $('#attention-quiet').checked = prefs.quietEnabled;
  $('#attention-quiet-start').value = prefs.quietStart;
  $('#attention-quiet-end').value = prefs.quietEnd;
  $('#attention-desktop-system').checked = prefs.desktopSystemEnabled;
  $('#attention-epaper-delivery').checked = prefs.epaperDeliveryEnabled;
  $('#attention-pause').textContent = prefs.pausedUntil && Date.now() < prefs.pausedUntil ? '恢复提醒' : '暂停到明早';
}

async function renderDeliveryStatus() {
  const target = $('#attention-delivery-status');
  try {
    const status = await api.deliveryStatus();
    deliverySnapshot = status || { channels: {}, recent: [] };
    const desktop = status.channels?.desktop || {};
    const epaper = status.channels?.epaper || {};
    target.textContent = `Windows：${desktop.enabled ? `已开启 · 已送达 ${desktop.delivered || 0}` : '关闭'}；墨水屏：${epaper.enabled ? `已开启 · 已显示 ${epaper.delivered || 0}` : '关闭'}。每条提醒在每个已选终端最多一次。`;
    if (initialized) render();
  } catch {
    target.textContent = '投递状态暂时不可用；设置仍会保存在本机。';
  }
}

function collectPreferences() {
  const previous = loadAttentionPreferences();
  const now = Date.now();
  const desktopEnabled = $('#attention-desktop-system').checked;
  const epaperEnabled = $('#attention-epaper-delivery').checked;
  return saveAttentionPreferences({
    ...previous,
    mode: $('#attention-mode').value,
    quietEnabled: $('#attention-quiet').checked,
    quietStart: $('#attention-quiet-start').value,
    quietEnd: $('#attention-quiet-end').value,
    desktopSystemEnabled: desktopEnabled,
    desktopSystemEnabledAt: desktopEnabled
      ? (previous.desktopSystemEnabled ? previous.desktopSystemEnabledAt : now) : null,
    epaperDeliveryEnabled: epaperEnabled,
    epaperDeliveryEnabledAt: epaperEnabled
      ? (previous.epaperDeliveryEnabled ? previous.epaperDeliveryEnabledAt : now) : null,
  });
}

function scheduleDigest(preferences) {
  if (digestTimer) return;
  digestTimer = setTimeout(() => {
    digestTimer = null;
    const currentPreferences = loadAttentionPreferences();
    const items = loadAttentionInbox().filter(item => !item.readAt && !isExpired(item)
      && attentionDecision(item, currentPreferences).reason === 'digest');
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
    unread: inbox.filter(item => !item.readAt && !isExpired(item)).length,
    preferences: loadAttentionPreferences(),
    learning: attentionLearningContext(),
    recent: inbox.slice(-8).reverse().map(item => ({
      kind: item.kind, priority: item.priority, title: item.title, detail: item.detail,
      reason: item.reason, createdAt: item.createdAt, expiresAt: item.expiresAt,
      read: !!item.readAt, done: !!item.doneAt, expired: isExpired(item), feedback: item.feedback || null,
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
    if (open) { render(); renderDeliveryStatus(); }
  };
  $('#btn-attention').addEventListener('click', () => toggle(!panel.classList.contains('open')));
  $('#attention-close').addEventListener('click', () => toggle(false));
  $('#attention-read-all').addEventListener('click', () => markAttentionRead());
  $('#attention-list').addEventListener('click', event => {
    const card = event.target.closest('.attention-item');
    if (!card) return;
    const retryChannel = event.target.closest('[data-delivery-retry]')?.dataset.deliveryRetry;
    if (retryChannel) {
      const button = event.target.closest('[data-delivery-retry]');
      button.disabled = true;
      api.retryDelivery(retryChannel, card.dataset.id)
        .then(() => {
          toast(`${retryChannel === 'desktop' ? 'Windows' : '墨水屏'}提醒已重新排队`, 'ok');
          return renderDeliveryStatus();
        })
        .catch(error => { button.disabled = false; toast(`重试失败：${error.message}`, 'err'); });
      return;
    }
    if (event.target.closest('[data-attention-read]')) markAttentionRead(card.dataset.id);
    const signal = event.target.closest('[data-attention-feedback]')?.dataset.attentionFeedback;
    if (signal) {
      feedbackAttentionItem(card.dataset.id, signal).then(() => {
        const message = signal === 'too_frequent' ? '同类提醒以后合并为摘要，可随时恢复'
          : signal === 'irrelevant' ? '同类提醒以后只收入中心，可随时恢复'
            : signal === 'done' ? '已记为完成' : '已记住这类提醒对你有用';
        toast(message, 'ok', 4500);
      }).catch(error => toast(`反馈未保存：${error.message}`, 'err'));
    }
    const page = event.target.closest('[data-attention-page]')?.dataset.attentionPage;
    if (page) { markAttentionRead(card.dataset.id); navigate(page); toggle(false); }
  });
  ['attention-mode', 'attention-quiet', 'attention-quiet-start', 'attention-quiet-end',
    'attention-desktop-system', 'attention-epaper-delivery']
    .forEach(id => $('#' + id).addEventListener('change', () => {
      collectPreferences();
      window.setTimeout(renderDeliveryStatus, 350);
    }));
  $('#attention-pause').addEventListener('click', () => {
    const prefs = loadAttentionPreferences();
    saveAttentionPreferences({ ...prefs, pausedUntil: prefs.pausedUntil && Date.now() < prefs.pausedUntil ? null : nextMorning() });
  });
  $('#attention-learning-controls').addEventListener('click', event => {
    const kind = event.target.closest('[data-learning-reset]')?.dataset.learningReset;
    if (!kind) return;
    resetAttentionLearning(kind).then(() => toast(`${KIND_LABELS[kind] || kind}已恢复默认提醒方式`, 'ok'))
      .catch(error => toast(`恢复失败：${error.message}`, 'err'));
  });
  $('#attention-learning-reset').addEventListener('click', () => {
    resetAttentionLearning().then(() => toast('全部分类调整已恢复，反馈记录仍保留', 'ok'))
      .catch(error => toast(`恢复失败：${error.message}`, 'err'));
  });
  $('#attention-learning-clear').addEventListener('click', () => {
    if (!window.confirm('清除全部学习记录并恢复默认提醒方式？此操作不可撤销。')) return;
    resetAttentionLearning(null, true).then(() => toast('学习记录已清除', 'ok'))
      .catch(error => toast(`清除失败：${error.message}`, 'err'));
  });
  bus.addEventListener('attention', event => {
    const items = Array.isArray(event.detail) ? event.detail : loadAttentionInbox();
    const fresh = items.filter(item => item && item.id && !knownIds.has(item.id));
    items.forEach(item => { if (item && item.id) knownIds.add(item.id); });
    fresh.forEach(deliverItem);
    render();
  });
  bus.addEventListener('attention-preferences', () => { renderPreferences(); renderDeliveryStatus(); });
  bus.addEventListener('attention-learning', renderLearning);
  document.addEventListener('attention-open', event => {
    const id = String(event.detail?.id || '');
    if (!id) return;
    toggle(true);
    window.setTimeout(() => {
      const card = [...document.querySelectorAll('.attention-item')]
        .find(row => row.dataset.id === id);
      if (!card) { toast('这条提醒已不在最近记录中', 'err'); return; }
      card.scrollIntoView({ behavior: 'smooth', block: 'center' });
      card.classList.add('attention-target');
      window.setTimeout(() => card.classList.remove('attention-target'), 2600);
    }, 120);
  });
  document.addEventListener('click', event => {
    if (panel.classList.contains('open') && !panel.contains(event.target) && !event.target.closest('#btn-attention')) toggle(false);
  });
  renderPreferences();
  renderDeliveryStatus();
  render();
}
