/* 深脉 DeepPulse — 状态存储：会话状态 + 本地记忆（自选/日记） */

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
  emit('journal', list);
  return list;
}

export function deleteJournalEntry(date) {
  saveJournalRaw(loadJournal().filter(e => e.date !== date));
}

function saveJournalRaw(list) {
  localStorage.setItem(JOURNAL_KEY, JSON.stringify(list));
  emit('journal', list);
}

/* ---------------- 行情页状态 ---------------- */
export const marketState = {
  code: null, name: null, klt: 101, fqt: 1, n: 300, ind: 'macd',
  quote: null, kline: null, pools: null,
};
