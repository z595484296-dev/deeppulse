/* 深脉 DeepPulse — API 客户端（本地后端）
   嵌入模式（/deeppulse/ 或 harness 端口 3080）下自动切换为绝对后端地址；
   独立窗口模式走相对路径。 */

export const EMBEDDED = (() => {
  try {
    const loc = typeof location !== 'undefined' ? location : { pathname: '', port: '' };
    return loc.pathname.startsWith('/deeppulse/') || loc.port === '3080';
  } catch { return false; }
})();
const BASE = EMBEDDED ? 'http://127.0.0.1:8971' : '';

async function request(path, timeoutMs = 20000) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  let resp;
  try {
    resp = await fetch(BASE + path, { signal: ctrl.signal, cache: 'no-store' });
  } catch (e) {
    clearTimeout(timer);
    // 网络层失败重试一次
    await new Promise(r => setTimeout(r, 600));
    try {
      resp = await fetch(BASE + path, { cache: 'no-store' });
    } catch (e2) {
      throw new Error('无法连接本地服务，请确认「深脉」后端正在运行');
    }
  } finally {
    clearTimeout(timer);
  }
  if (!resp.ok) throw new Error('HTTP ' + resp.status);
  const j = await resp.json();
  if (!j || j.ok !== true) throw new Error((j && j.error) || '接口返回异常');
  return j.data;
}

export const api = {
  health: () => request('/api/health'),
  sources: () => request('/api/sources'),
  tdxStatus: (fresh = false) => request('/api/tdx/status?probe=1' + (fresh ? '&fresh=1' : ''), 10000),
  disclosures: (code, n = 8) => request(`/api/disclosures?code=${encodeURIComponent(code)}&n=${n}`),
  brain: () => request('/api/brain'),
  indices: () => request('/api/indices'),
  emotion: (record) => request('/api/emotion' + (record ? '?record=1' : ''), 60000),
  recordSnapshot: () => fetch(BASE + '/api/emotion/record', { method: 'POST' }).then(r => r.json()),
  ladder: (type = 'ZT') => request('/api/ladder?type=' + type),
  premium: () => request('/api/premium', 60000),
  dragon: () => request('/api/dragon', 60000),
  dragonSeats: (code, date) => request(`/api/dragon-seats?code=${encodeURIComponent(code)}&date=${encodeURIComponent(date || '')}`, 60000),
  sectorCycle: () => request('/api/sector-cycle', 90000),
  weights: () => request('/api/weights'),
  saveWeights: async (weights) => {
    const resp = await fetch(BASE + '/api/weights', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ weights }),
    });
    const j = await resp.json();
    return j.data || {};
  },
  quote: (code) => request('/api/quote?code=' + encodeURIComponent(code)),
  kline: (code, klt = 101, fqt = 1, n = 320) =>
    request(`/api/kline?code=${encodeURIComponent(code)}&klt=${klt}&fqt=${fqt}&n=${n}`),
  rank: (sort = 'up') => request('/api/rank?sort=' + sort),
  sectors: () => request('/api/sectors'),
  sectorsFlow: () => request('/api/sectors-flow'),
  news: () => request('/api/news'),
  search: (q) => request('/api/search?q=' + encodeURIComponent(q)),
  chat: async (messages) => {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 60000);
    try {
      const resp = await fetch(BASE + '/api/chat', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ messages }), signal: ctrl.signal,
      });
      const j = await resp.json();
      return (j && j.data) || { mode: 'local' };
    } finally {
      clearTimeout(timer);
    }
  },
};
