/* 深脉 DeepPulse — 统一注意力策略（纯规则层） */

export const DEFAULT_ATTENTION_PREFERENCES = Object.freeze({
  mode: 'balanced',
  quietEnabled: true,
  quietStart: '22:30',
  quietEnd: '08:00',
  pausedUntil: null,
  systemDigestMinutes: 15,
  desktopSystemEnabled: false,
  desktopSystemEnabledAt: null,
  epaperDeliveryEnabled: false,
  epaperDeliveryEnabledAt: null,
  kindControls: {},
});

const MODES = new Set(['balanced', 'high_only', 'center_only']);
const PRIORITIES = new Set(['high', 'medium', 'low']);

function cleanTime(value, fallback) {
  return /^([01]\d|2[0-3]):[0-5]\d$/.test(String(value || '')) ? String(value) : fallback;
}

export function normalizeAttentionPreferences(value = {}) {
  const source = value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  const paused = Number(source.pausedUntil);
  const rawControls = source.kindControls && typeof source.kindControls === 'object' && !Array.isArray(source.kindControls)
    ? source.kindControls : {};
  const kindControls = {};
  Object.entries(rawControls).slice(0, 24).forEach(([kind, control]) => {
    if (!/^[a-z0-9_-]{1,32}$/i.test(kind) || !control || typeof control !== 'object') return;
    const delivery = control.delivery === 'center_only' ? 'center_only'
      : control.delivery === 'digest' ? 'digest' : null;
    if (!delivery) return;
    kindControls[kind] = {
      delivery,
      reason: control.reason === 'irrelevant' ? 'irrelevant' : 'too_frequent',
      updatedAt: Number(control.updatedAt) || null,
    };
  });
  return {
    mode: MODES.has(source.mode) ? source.mode : DEFAULT_ATTENTION_PREFERENCES.mode,
    quietEnabled: source.quietEnabled !== false,
    quietStart: cleanTime(source.quietStart, DEFAULT_ATTENTION_PREFERENCES.quietStart),
    quietEnd: cleanTime(source.quietEnd, DEFAULT_ATTENTION_PREFERENCES.quietEnd),
    pausedUntil: Number.isFinite(paused) && paused > 0 ? paused : null,
    systemDigestMinutes: Math.max(5, Math.min(60, Number(source.systemDigestMinutes) || 15)),
    desktopSystemEnabled: source.desktopSystemEnabled === true,
    desktopSystemEnabledAt: Number(source.desktopSystemEnabledAt) > 0 ? Number(source.desktopSystemEnabledAt) : null,
    epaperDeliveryEnabled: source.epaperDeliveryEnabled === true,
    epaperDeliveryEnabledAt: Number(source.epaperDeliveryEnabledAt) > 0 ? Number(source.epaperDeliveryEnabledAt) : null,
    kindControls,
  };
}

function defaultExpiry(kind, createdAt) {
  const hours = kind === 'move' ? 2 : kind === 'phase' ? 24 : kind === 'price' ? 24 : kind === 'event' ? 24 : 36;
  return createdAt + hours * 60 * 60 * 1000;
}

function minuteOfDay(text) {
  const [hour, minute] = text.split(':').map(Number);
  return hour * 60 + minute;
}

export function isQuietTime(preferences, now = new Date()) {
  const prefs = normalizeAttentionPreferences(preferences);
  if (!prefs.quietEnabled) return false;
  const current = now.getHours() * 60 + now.getMinutes();
  const start = minuteOfDay(prefs.quietStart);
  const end = minuteOfDay(prefs.quietEnd);
  if (start === end) return true;
  return start < end ? current >= start && current < end : current >= start || current < end;
}

export function makeAttentionItem(input = {}, now = Date.now()) {
  const createdAt = Number(input.createdAt) || Number(now);
  const kind = String(input.kind || 'system').slice(0, 32);
  const priority = PRIORITIES.has(input.priority) ? input.priority : 'medium';
  const fingerprint = String(input.fingerprint || `${kind}:${input.title || ''}:${input.detail || ''}`).slice(0, 180);
  const expiresAt = Number(input.expiresAt) || defaultExpiry(kind, createdAt);
  return {
    id: String(input.id || `${createdAt.toString(36)}:${fingerprint}`).slice(0, 240),
    fingerprint,
    kind,
    priority,
    title: String(input.title || '市场更新').trim().slice(0, 80),
    detail: String(input.detail || '').trim().slice(0, 220),
    reason: String(input.reason || '市场状态发生变化').trim().slice(0, 160),
    page: String(input.page || 'overview').slice(0, 24),
    delivery: input.delivery === 'center_only' ? 'center_only'
      : input.delivery === 'immediate' ? 'immediate' : 'digest',
    createdAt,
    expiresAt,
    readAt: Number(input.readAt) || null,
    doneAt: Number(input.doneAt) || null,
    feedback: ['helpful', 'done', 'too_frequent', 'irrelevant'].includes(input.feedback) ? input.feedback : null,
  };
}

/** 决定是否打断用户；所有事件无论结果如何都应先进入提醒中心。 */
export function attentionDecision(item, preferences, now = new Date()) {
  const prefs = normalizeAttentionPreferences(preferences);
  const timestamp = now instanceof Date ? now.getTime() : Number(now);
  if (Number(item.expiresAt) > 0 && timestamp >= Number(item.expiresAt)) return { interrupt: false, reason: 'expired' };
  if (item.delivery === 'center_only') return { interrupt: false, reason: 'item_center_only' };
  if (prefs.pausedUntil && timestamp < prefs.pausedUntil) return { interrupt: false, reason: 'paused' };
  if (prefs.mode === 'center_only') return { interrupt: false, reason: 'center_only' };
  if (prefs.mode === 'high_only' && item.priority !== 'high') return { interrupt: false, reason: 'priority' };
  // 用户亲自设置的到价条件，在平衡模式下不受系统安静时段影响；“暂停到明天”仍会阻止弹出。
  if (item.kind === 'price' && item.priority === 'high') return { interrupt: true, reason: 'user_price_alert' };
  const learned = prefs.kindControls[item.kind];
  if (learned?.delivery === 'center_only') return { interrupt: false, reason: 'learned_center_only' };
  if (learned?.delivery === 'digest') return { interrupt: false, reason: 'digest' };
  if (isQuietTime(prefs, now)) return { interrupt: false, reason: 'quiet' };
  if (item.delivery !== 'immediate') return { interrupt: false, reason: 'digest' };
  return { interrupt: true, reason: 'immediate' };
}

export function nextMorning(now = new Date()) {
  const result = new Date(now);
  result.setDate(result.getDate() + 1);
  result.setHours(8, 0, 0, 0);
  return result.getTime();
}

export function digestMessage(items = []) {
  const unread = items.filter(item => item && !item.readAt);
  const phases = unread.filter(item => item.kind === 'phase').length;
  const moves = unread.filter(item => item.kind === 'move').length;
  const events = unread.filter(item => item.kind === 'event').length;
  const parts = [];
  if (phases) parts.push(`${phases} 项阶段变化`);
  if (moves) parts.push(`${moves} 项盘中异动`);
  if (events) parts.push(`${events} 项事件影响`);
  const rest = unread.length - phases - moves - events;
  if (rest > 0) parts.push(`${rest} 项其他更新`);
  return parts.length ? `市场摘要：${parts.join('、')}` : '暂无新的系统更新';
}
