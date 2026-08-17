/* 深脉 DeepPulse — 状态存储：会话状态 + 本机统一档案（各运行端共享） */

import { api } from './api.js?v=1.4.2';

export const state = {
  emotion: null,      // /api/emotion 数据
  indices: null,
  news: null,
  health: null,
  lastUpdate: null,
  degraded: false,
  sparks: null,       // 指数迷你K线
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
};
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
        const localValue = JSON.parse(localStorage.getItem(storageKey) || '[]');
        // 空的新来源不能抢先覆盖旧来源中尚待迁移的真实数据。
        if (Array.isArray(localValue) && localValue.length) migration[key] = localValue;
      } catch { /* 等待其他来源迁移或首次真实写入 */ }
    }
  });
  if (Object.keys(migration).length) await api.saveProfile(migration);
  emit('watch', loadWatch());
  emit('alerts', loadAlerts());
  emit('journal', loadJournal());
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

/* ---------------- 行情页状态 ---------------- */
export const marketState = {
  code: null, name: null, klt: 101, fqt: 1, n: 300, ind: 'macd',
  quote: null, kline: null, pools: null,
};
