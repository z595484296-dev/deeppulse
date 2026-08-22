/* 深脉 DeepPulse — 状态存储：会话状态 + 本机统一档案（各运行端共享） */

import { api } from './api.js?v=1.39.0';
import { normalizeAttentionPreferences } from './attention.js?v=1.39.0';

export const state = {
  emotion: null,      // /api/emotion 数据
  indices: null,
  news: null,
  health: null,
  lastUpdate: null,
  degraded: false,
  sparks: null,       // 指数迷你K线
  monitor: null,
  routine: null,
  eventImpact: null,
  hypotheses: null,
  cockpit: null,
  researchMemory: null,
  researchWorkflows: null,
  researchSuggestions: null,
  akshareResearch: null,
};

export const bus = new EventTarget();

export function emit(type, detail) {
  bus.dispatchEvent(new CustomEvent(type, { detail }));
}

const PROFILE_KEYS = {
  watchlist: 'dp_watchlist_v1',
  alerts: 'dp_alerts_v1',
  journal: 'dp_journal_v1',
  chat_history: 'dp_chat_v1',
  brief_receipts: 'dp_brief_receipts_v1',
  attention_inbox: 'dp_attention_inbox_v1',
  attention_feedback: 'dp_attention_feedback_v1',
  attention_preferences: 'dp_attention_preferences_v1',
  background_monitor: 'dp_background_monitor_v1',
  market_routine: 'dp_market_routine_v1',
  event_service: 'dp_event_service_v1',
  research_hypotheses: 'dp_research_hypotheses_v1',
  hypothesis_receipts: 'dp_hypothesis_receipts_v1',
};
const PROFILE_OBJECT_KEYS = new Set(['attention_preferences', 'background_monitor', 'market_routine', 'event_service']);
const profileTimers = new Map();

function persistProfile(key, value) {
  clearTimeout(profileTimers.get(key));
  profileTimers.set(key, setTimeout(() => {
    api.saveProfile({ [key]: value }).then(() => {
      emit('profile-sync', { ok: true, key });
    }).catch(error => {
      emit('profile-sync', { ok: false, key, error: error.message });
    });
  }, 250));
}

/**
 * 以本机后端档案为权威，在首次升级时把当前来源的 localStorage 迁入后端。
 * 显式空数组也具有含义，避免另一个来源用旧数据把用户已删除的内容复活。
 */
export async function syncProfile() {
  const profile = await api.profile();
  const remote = (profile && profile.data) || {};
  const migration = {};
  Object.entries(PROFILE_KEYS).forEach(([key, storageKey]) => {
    if (Object.prototype.hasOwnProperty.call(remote, key)) {
      localStorage.setItem(storageKey, JSON.stringify(remote[key]));
    } else {
      try {
        const localValue = JSON.parse(localStorage.getItem(storageKey) || (PROFILE_OBJECT_KEYS.has(key) ? '{}' : '[]'));
        // 空的新来源不能抢先覆盖旧来源中尚待迁移的真实数据。
        if ((Array.isArray(localValue) && localValue.length)
          || (PROFILE_OBJECT_KEYS.has(key) && localValue && Object.keys(localValue).length)) migration[key] = localValue;
      } catch { /* 等待其他来源迁移或首次真实写入 */ }
    }
  });
  if (Object.keys(migration).length) await api.saveProfile(migration);
  emit('watch', loadWatch());
  emit('alerts', loadAlerts());
  emit('journal', loadJournal());
  emit('brief-receipts', loadBriefReceipts());
  emit('attention', loadAttentionInbox());
  emit('attention-preferences', loadAttentionPreferences());
  emit('attention-learning', attentionLearningContext());
  emit('background-monitor', loadBackgroundMonitor());
  emit('market-routine', loadMarketRoutine());
  emit('event-service', loadEventService());
  emit('research-hypotheses', loadResearchHypotheses());
  document.dispatchEvent(new CustomEvent('profile-synced'));
  return profile;
}

/* ---------------- 自选股（localStorage） ---------------- */
const WATCH_KEY = 'dp_watchlist_v1';

export function loadWatch() {
  try {
    const list = JSON.parse(localStorage.getItem(WATCH_KEY)) || [];
    return list.map(w => ({ group: '默认', ...w }));
  } catch { return []; }
}

export function saveWatch(list) {
  localStorage.setItem(WATCH_KEY, JSON.stringify(list));
  persistProfile('watchlist', list);
  emit('watch', list);
}

/** Apply the authoritative list returned by an atomic server action without writing it back again. */
export function applyServerWatchlist(list) {
  const clean = Array.isArray(list) ? list : [];
  localStorage.setItem(WATCH_KEY, JSON.stringify(clean));
  emit('watch', clean);
}

export function watchGroups(list) {
  const groups = [];
  (list || []).forEach(w => { const g = w.group || '默认'; if (!groups.includes(g)) groups.push(g); });
  return groups;
}

export function setWatchGroup(code, group) {
  const list = loadWatch();
  const it = list.find(w => w.code === code);
  if (it) { it.group = group || '默认'; saveWatch(list); }
}

export function batchRemoveWatch(codes) {
  const set = new Set(codes);
  saveWatch(loadWatch().filter(w => !set.has(w.code)));
}

export function batchMoveWatch(codes, group) {
  const set = new Set(codes);
  const list = loadWatch();
  list.forEach(w => { if (set.has(w.code)) w.group = group || '默认'; });
  saveWatch(list);
}

export function addWatch(item) {
  const list = loadWatch();
  if (!list.some(w => w.code === item.code)) {
    list.push({ code: item.code, name: item.name, note: '', added: Date.now() });
    saveWatch(list);
    return true;
  }
  return false;
}

export function removeWatch(code) {
  saveWatch(loadWatch().filter(w => w.code !== code));
}

export function setWatchNote(code, note) {
  const list = loadWatch();
  const it = list.find(w => w.code === code);
  if (it) { it.note = note; saveWatch(list); }
}

/* ---------------- 价格提醒（localStorage） ---------------- */
const ALERTS_KEY = 'dp_alerts_v1';

export function loadAlerts() {
  try { return JSON.parse(localStorage.getItem(ALERTS_KEY)) || []; }
  catch { return []; }
}

export function saveAlerts(list) {
  localStorage.setItem(ALERTS_KEY, JSON.stringify(list));
  persistProfile('alerts', list);
  emit('alerts', list);
}

export function addAlert({ code, name, dir, price }) {
  const list = loadAlerts();
  list.push({ id: Date.now().toString(36) + Math.random().toString(36).slice(2, 7), code, name, dir, price, triggered: false, ts: Date.now() });
  saveAlerts(list);
}

export function removeAlert(id) {
  saveAlerts(loadAlerts().filter(a => a.id !== id));
}

export function markTriggered(id) {
  const list = loadAlerts();
  const it = list.find(a => a.id === id);
  if (it && !it.triggered) {
    it.triggered = true;
    it.triggered_at = Date.now();
    saveAlerts(list);
  }
}

/* ---------------- 情绪日记（localStorage） ---------------- */
const JOURNAL_KEY = 'dp_journal_v1';

export function loadJournal() {
  try { return JSON.parse(localStorage.getItem(JOURNAL_KEY)) || []; }
  catch { return []; }
}

export function saveJournalEntry(date, text) {
  const list = loadJournal();
  const i = list.findIndex(e => e.date === date);
  if (i >= 0) list[i].text = text;
  else list.push({ date, text, ts: Date.now() });
  list.sort((a, b) => (a.date < b.date ? 1 : -1));
  localStorage.setItem(JOURNAL_KEY, JSON.stringify(list));
  persistProfile('journal', list);
  emit('journal', list);
  return list;
}

export function deleteJournalEntry(date) {
  saveJournalRaw(loadJournal().filter(e => e.date !== date));
}

function saveJournalRaw(list) {
  localStorage.setItem(JOURNAL_KEY, JSON.stringify(list));
  persistProfile('journal', list);
  emit('journal', list);
}

export function persistChatHistory(messages) {
  persistProfile('chat_history', (messages || []).slice(-60));
}

/* ---------------- 主动简报处理记录（跨运行端共享） ---------------- */
const BRIEF_RECEIPTS_KEY = 'dp_brief_receipts_v1';

export function loadBriefReceipts() {
  try { return JSON.parse(localStorage.getItem(BRIEF_RECEIPTS_KEY)) || []; }
  catch { return []; }
}

export function isBriefRead(id) {
  return !!id && loadBriefReceipts().some(item => item && item.id === id);
}

export function setBriefRead(brief, read = true) {
  if (!brief || !brief.id) return [];
  const previous = loadBriefReceipts();
  const list = previous.filter(item => item && item.id !== brief.id);
  const receipt = {
    id: brief.id, contentHash: brief.contentHash || null, dataDate: brief.dataDate || null,
    readAt: Date.now(), surface: location.pathname.startsWith('/deeppulse/') ? 'harness' : 'local-web',
  };
  if (read) list.push(receipt);
  const next = list.slice(-200);
  localStorage.setItem(BRIEF_RECEIPTS_KEY, JSON.stringify(next));
  emit('brief-receipts', next);
  api.saveBriefReceipt(receipt, read).then(profile => {
    const remote = profile && profile.data && Array.isArray(profile.data.brief_receipts)
      ? profile.data.brief_receipts : next;
    localStorage.setItem(BRIEF_RECEIPTS_KEY, JSON.stringify(remote));
    emit('brief-receipts', remote);
    emit('profile-sync', { ok: true, key: 'brief_receipts' });
  }).catch(error => {
    localStorage.setItem(BRIEF_RECEIPTS_KEY, JSON.stringify(previous));
    emit('brief-receipts', previous);
    emit('profile-sync', { ok: false, key: 'brief_receipts', error: error.message });
  });
  return next;
}

/* ---------------- 提醒中心（跨运行端共享） ---------------- */
const ATTENTION_INBOX_KEY = 'dp_attention_inbox_v1';
const ATTENTION_PREFS_KEY = 'dp_attention_preferences_v1';
const ATTENTION_FEEDBACK_KEY = 'dp_attention_feedback_v1';

export function loadAttentionInbox() {
  try {
    const value = JSON.parse(localStorage.getItem(ATTENTION_INBOX_KEY) || '[]');
    return Array.isArray(value) ? value : [];
  } catch { return []; }
}

export function saveAttentionInbox(list) {
  const next = (Array.isArray(list) ? list : []).slice(-200);
  localStorage.setItem(ATTENTION_INBOX_KEY, JSON.stringify(next));
  persistProfile('attention_inbox', next);
  emit('attention', next);
  return next;
}

export function pushAttentionItem(item) {
  if (!item || !item.id) return loadAttentionInbox();
  const previous = loadAttentionInbox();
  const next = [...previous.filter(row => row && row.id !== item.id), item].slice(-200);
  localStorage.setItem(ATTENTION_INBOX_KEY, JSON.stringify(next));
  emit('attention', next);
  api.saveAttentionItem(item).then(profile => {
    const remote = profile && profile.data && Array.isArray(profile.data.attention_inbox)
      ? profile.data.attention_inbox : next;
    localStorage.setItem(ATTENTION_INBOX_KEY, JSON.stringify(remote));
    emit('attention', remote);
    emit('profile-sync', { ok: true, key: 'attention_inbox' });
  }).catch(error => emit('profile-sync', { ok: false, key: 'attention_inbox', error: error.message }));
  return next;
}

export function markAttentionRead(id = null) {
  const next = loadAttentionInbox().map(item => (!id || item.id === id)
    ? { ...item, readAt: item.readAt || Date.now() } : item);
  localStorage.setItem(ATTENTION_INBOX_KEY, JSON.stringify(next));
  emit('attention', next);
  if (!id) {
    persistProfile('attention_inbox', next);
    return next;
  }
  const updated = next.find(item => item && item.id === id);
  if (updated) {
    api.saveAttentionItem(updated).then(profile => {
      const remote = profile && profile.data && Array.isArray(profile.data.attention_inbox)
        ? profile.data.attention_inbox : next;
      localStorage.setItem(ATTENTION_INBOX_KEY, JSON.stringify(remote));
      emit('attention', remote);
      emit('profile-sync', { ok: true, key: 'attention_inbox' });
    }).catch(error => emit('profile-sync', { ok: false, key: 'attention_inbox', error: error.message }));
  }
  return next;
}

export function loadAttentionFeedback() {
  try {
    const value = JSON.parse(localStorage.getItem(ATTENTION_FEEDBACK_KEY) || '[]');
    return Array.isArray(value) ? value : [];
  } catch { return []; }
}

export function attentionLearningContext() {
  const feedback = loadAttentionFeedback();
  const preferences = loadAttentionPreferences();
  const controls = preferences.kindControls || {};
  const relevanceControls = Array.isArray(preferences.relevanceControls)
    ? preferences.relevanceControls : [];
  const counts = { helpful: 0, done: 0, too_frequent: 0, irrelevant: 0 };
  feedback.forEach(row => { if (row && Object.prototype.hasOwnProperty.call(counts, row.signal)) counts[row.signal] += 1; });
  return {
    feedbackCount: feedback.length,
    counts,
    activeControls: Object.keys(controls).length,
    controls: Object.entries(controls).slice(0, 24).map(([kind, control]) => ({ kind, ...control })),
    activeRelevanceControls: relevanceControls.filter(row => row?.status === 'active'
      && Number(row.expiresAt || 0) > Date.now()).length,
    relevanceControls: relevanceControls.slice(-40),
    basis: 'explicit-user-feedback-only',
  };
}

export function feedbackAttentionItem(id, signal) {
  if (!id || !['helpful', 'done', 'too_frequent', 'irrelevant'].includes(signal)) return Promise.resolve(null);
  const previousInbox = loadAttentionInbox();
  const previousFeedback = loadAttentionFeedback();
  const previousPreferences = loadAttentionPreferences();
  const timestamp = Date.now();
  const target = previousInbox.find(item => item && item.id === id);
  if (!target) return Promise.reject(new Error('提醒已不存在'));
  const nextInbox = previousInbox.map(item => item && item.id === id ? {
    ...item, feedback: signal, feedbackAt: timestamp,
    ...(signal === 'done' ? { doneAt: timestamp, readAt: item.readAt || timestamp } : {}),
  } : item);
  const nextFeedback = [...previousFeedback.filter(row => row && row.itemId !== id), {
    itemId: id, kind: target.kind || 'system', signal, at: timestamp,
    surface: typeof location !== 'undefined' && location.pathname.startsWith('/deeppulse/') ? 'harness' : 'local-web',
  }].slice(-500);
  const nextPreferences = { ...previousPreferences, kindControls: { ...(previousPreferences.kindControls || {}) } };
  if (!['price', 'event'].includes(target.kind) && signal === 'too_frequent') {
    nextPreferences.kindControls[target.kind || 'system'] = { delivery: 'digest', reason: signal, updatedAt: timestamp };
  } else if (!['price', 'event'].includes(target.kind) && signal === 'irrelevant') {
    nextPreferences.kindControls[target.kind || 'system'] = { delivery: 'center_only', reason: signal, updatedAt: timestamp };
  }
  localStorage.setItem(ATTENTION_INBOX_KEY, JSON.stringify(nextInbox));
  localStorage.setItem(ATTENTION_FEEDBACK_KEY, JSON.stringify(nextFeedback));
  localStorage.setItem(ATTENTION_PREFS_KEY, JSON.stringify(nextPreferences));
  emit('attention', nextInbox);
  emit('attention-preferences', nextPreferences);
  emit('attention-learning', attentionLearningContext());
  return api.saveAttentionFeedback(id, signal, nextFeedback[nextFeedback.length - 1].surface).then(result => {
    const remote = result?.profile?.data || {};
    if (Array.isArray(remote.attention_inbox)) localStorage.setItem(ATTENTION_INBOX_KEY, JSON.stringify(remote.attention_inbox));
    if (Array.isArray(remote.attention_feedback)) localStorage.setItem(ATTENTION_FEEDBACK_KEY, JSON.stringify(remote.attention_feedback));
    if (remote.attention_preferences) localStorage.setItem(ATTENTION_PREFS_KEY, JSON.stringify(remote.attention_preferences));
    emit('attention', loadAttentionInbox());
    emit('attention-preferences', loadAttentionPreferences());
    emit('attention-learning', result.learning || attentionLearningContext());
    emit('profile-sync', { ok: true, key: 'attention_feedback' });
    return result.learning;
  }).catch(error => {
    localStorage.setItem(ATTENTION_INBOX_KEY, JSON.stringify(previousInbox));
    localStorage.setItem(ATTENTION_FEEDBACK_KEY, JSON.stringify(previousFeedback));
    localStorage.setItem(ATTENTION_PREFS_KEY, JSON.stringify(previousPreferences));
    emit('attention', previousInbox);
    emit('attention-preferences', previousPreferences);
    emit('attention-learning', attentionLearningContext());
    emit('profile-sync', { ok: false, key: 'attention_feedback', error: error.message });
    throw error;
  });
}

export function resetAttentionLearning(kind = null, clearHistory = false) {
  return api.resetAttentionLearning(kind, clearHistory).then(result => {
    const remote = result?.profile?.data || {};
    if (Array.isArray(remote.attention_inbox)) localStorage.setItem(ATTENTION_INBOX_KEY, JSON.stringify(remote.attention_inbox));
    if (Array.isArray(remote.attention_feedback)) localStorage.setItem(ATTENTION_FEEDBACK_KEY, JSON.stringify(remote.attention_feedback));
    if (remote.attention_preferences) localStorage.setItem(ATTENTION_PREFS_KEY, JSON.stringify(remote.attention_preferences));
    emit('attention', loadAttentionInbox());
    emit('attention-preferences', loadAttentionPreferences());
    emit('attention-learning', result.learning || attentionLearningContext());
    return result.learning;
  });
}

export function loadAttentionPreferences() {
  try { return normalizeAttentionPreferences(JSON.parse(localStorage.getItem(ATTENTION_PREFS_KEY) || '{}')); }
  catch { return normalizeAttentionPreferences(); }
}

export function saveAttentionPreferences(value) {
  const next = normalizeAttentionPreferences(value);
  localStorage.setItem(ATTENTION_PREFS_KEY, JSON.stringify(next));
  persistProfile('attention_preferences', next);
  emit('attention-preferences', next);
  return next;
}

const BACKGROUND_MONITOR_KEY = 'dp_background_monitor_v1';

export function loadBackgroundMonitor() {
  try {
    const value = JSON.parse(localStorage.getItem(BACKGROUND_MONITOR_KEY) || '{}');
    return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  } catch { return {}; }
}

const MARKET_ROUTINE_KEY = 'dp_market_routine_v1';

export function loadMarketRoutine() {
  try {
    const value = JSON.parse(localStorage.getItem(MARKET_ROUTINE_KEY) || '{}');
    return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  } catch { return {}; }
}

const EVENT_SERVICE_KEY = 'dp_event_service_v1';

export function loadEventService() {
  try {
    const value = JSON.parse(localStorage.getItem(EVENT_SERVICE_KEY) || '{}');
    return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  } catch { return {}; }
}

const RESEARCH_HYPOTHESES_KEY = 'dp_research_hypotheses_v1';

export function loadResearchHypotheses() {
  try {
    const value = JSON.parse(localStorage.getItem(RESEARCH_HYPOTHESES_KEY) || '[]');
    return Array.isArray(value) ? value : [];
  } catch { return []; }
}

/* ---------------- 行情页状态 ---------------- */
export const marketState = {
  code: null, name: null, klt: 101, fqt: 1, n: 300, ind: 'macd',
  quote: null, kline: null, pools: null,
};
