/* 深脉 DeepPulse — 通用工具与格式化 */

export const UP = '#f6465d';
export const DOWN = '#2ebd85';
export const FLAT = '#8b95a8';
export const ACCENT = '#4f8cff';
export const PHASE_COLORS = { blue: '#4f8cff', cyan: '#22d3ee', amber: '#f0b90b', red: '#f6465d', violet: '#a855f7' };

export function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

export function fmtPct(v, digits = 2) {
  if (v === null || v === undefined || isNaN(v)) return '--';
  const s = Number(v).toFixed(digits);
  return (v > 0 ? '+' : '') + s + '%';
}

export function fmtPrice(v) {
  if (v === null || v === undefined || isNaN(v)) return '--';
  return Number(v).toFixed(2);
}

export function fmtNum(v, digits = 0) {
  if (v === null || v === undefined || isNaN(v)) return '--';
  return Number(v).toFixed(digits);
}

/** 大数：1.23万亿 / 45.6亿 / 7800万 */
export function fmtBig(v) {
  if (v === null || v === undefined || isNaN(v)) return '--';
  const abs = Math.abs(v);
  const sign = v < 0 ? '-' : '';
  if (abs >= 1e12) return sign + (abs / 1e12).toFixed(2) + '万亿';
  if (abs >= 1e8) return sign + (abs / 1e8).toFixed(2) + '亿';
  if (abs >= 1e4) return sign + (abs / 1e4).toFixed(1) + '万';
  return String(Math.round(v));
}

export function pctClass(v) {
  if (v === null || v === undefined || isNaN(v)) return 'flat';
  if (v > 0) return 'up';
  if (v < 0) return 'down';
  return 'flat';
}

export function pctColor(v) {
  return { up: UP, down: DOWN, flat: FLAT }[pctClass(v)];
}

/** 封板时间 HHMMSS → HH:MM */
export function fmtSeal(t) {
  if (!t) return '--';
  const s = String(t).padStart(6, '0');
  return s.slice(0, 2) + ':' + s.slice(2, 4);
}

export function toast(msg, type = 'ok', ms = 3200) {
  const wrap = document.getElementById('toast-wrap');
  if (!wrap) return;
  const el = document.createElement('div');
  el.className = 'toast ' + type;
  el.textContent = msg;
  wrap.appendChild(el);
  setTimeout(() => {
    el.classList.add('out');
    setTimeout(() => el.remove(), 260);
  }, ms);
}

export function debounce(fn, ms = 300) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

/** 统一空态组件：图标 + 标题 + 引导语 */
export function emptyState(el, icon = '📭', title = '暂无数据', desc = '') {
  el.innerHTML = `<div class="empty-state">
    <div class="es-icon">${icon}</div>
    <div class="es-title">${esc(title)}</div>
    ${desc ? `<div class="es-desc">${esc(desc)}</div>` : ''}
  </div>`;
}

/** 由情绪快照构建 K 线阶段色带（升序排列，相邻同日阶段合并为区间） */
export function phaseBandsOf(snapshots) {
  const snaps = (snapshots || [])
    .filter(s => s.date && s.color)
    .sort((a, b) => (a.date < b.date ? -1 : 1));
  if (!snaps.length) return [];
  const bands = [];
  let cur = { start: snaps[0].date, end: snaps[0].date, color: snaps[0].color, phase: snaps[0].phase };
  for (let i = 1; i < snaps.length; i++) {
    const s = snaps[i];
    if (s.color === cur.color) {
      cur.end = s.date;
    } else {
      bands.push(cur);
      cur = { start: s.date, end: s.date, color: s.color, phase: s.phase };
    }
  }
  bands.push(cur);
  return bands;
}

/** 统一下载助手（带 BOM，Excel 打开中文不乱码） */
export function downloadText(filename, text, mime = 'text/plain') {
  const blob = new Blob(['\ufeff' + text], { type: mime + ';charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}

export function throttle(fn, ms = 1000) {
  let last = 0;
  return (...args) => {
    const now = Date.now();
    if (now - last >= ms) { last = now; fn(...args); }
  };
}

/** A股交易时段判断（北京时间） */
export function tradingState(now = new Date()) {
  const day = now.getDay();
  const hm = now.getHours() * 100 + now.getMinutes();
  const isTradingDay = day >= 1 && day <= 5;
  const inSession = isTradingDay && ((hm >= 930 && hm <= 1130) || (hm >= 1300 && hm <= 1500));
  if (inSession) return { state: 'open', label: '交易中' };
  if (isTradingDay && hm < 930) return { state: 'pre', label: '未开盘' };
  if (isTradingDay && hm > 1130 && hm < 1300) return { state: 'break', label: '午间休市' };
  return { state: 'closed', label: '已收盘' };
}
