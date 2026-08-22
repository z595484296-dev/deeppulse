/* 深脉 DeepPulse — API 客户端（本地后端）
   嵌入模式自动发现 8971~8980 中兼容的本地服务；独立模式走相对路径。 */

export const EMBEDDED = (() => {
  try {
    const loc = typeof location !== 'undefined' ? location : { pathname: '', port: '' };
    return loc.pathname.startsWith('/deeppulse/') || loc.port === '3080';
  } catch { return false; }
})();

const MIN_VERSION = '1.34.0';
const LOCAL_BASES = Array.from({ length: 10 }, (_, index) => `http://127.0.0.1:${8971 + index}`);
let cachedBase = EMBEDDED ? null : '';

function normalizeBase(value) {
  const text = String(value || '').trim().replace(/\/+$/, '');
  return /^http:\/\/(127\.0\.0\.1|localhost):(?:897[1-9]|8980)$/i.test(text) ? text : '';
}

function configuredBase() {
  if (typeof window === 'undefined') return '';
  try {
    return normalizeBase(window.__DEEPPULSE_BASE__ || window.parent?.__DEEPPULSE_BASE__);
  } catch { return normalizeBase(window.__DEEPPULSE_BASE__); }
}

function versionAtLeast(value, minimum) {
  const left = String(value || '').split('.').map(part => Number.parseInt(part, 10) || 0);
  const right = String(minimum || '').split('.').map(part => Number.parseInt(part, 10) || 0);
  for (let index = 0; index < Math.max(left.length, right.length); index += 1) {
    const a = left[index] || 0;
    const b = right[index] || 0;
    if (a !== b) return a > b;
  }
  return true;
}

async function probeBase(base, signal) {
  try {
    const response = await fetch(`${base}/api/health`, { cache: 'no-store', signal });
    if (!response.ok) return '';
    const body = await response.json();
    const health = (body && body.data) || body || {};
    const capabilities = health.capabilities || {};
    return versionAtLeast(health.version, MIN_VERSION)
      && capabilities.tdx_read_only === true
      && capabilities.proactive_brief === 1
      && capabilities.profile_brief_receipts === 1
      && capabilities.attention_center === 1
      && capabilities.profile_attention === 1
      && capabilities.attention_learning === 1
      && capabilities.attention_triage === 1
      && capabilities.attention_center_only_boundary === 1
      && capabilities.chat_answer_freshness === 1
      && capabilities.background_monitor === 1
      && capabilities.market_routine === 1
      && capabilities.akshare_enrichment === 1
      && capabilities.akshare_research_snapshot === 1
      && capabilities.akshare_research_packs === 1
      && capabilities.akshare_interface_health === 1
      && capabilities.source_lineage === 1
      && capabilities.event_impact === 1
      && capabilities.event_background_service === 1
      && capabilities.research_hypotheses === 1
      && capabilities.hypothesis_due_reminders === 1
      && capabilities.hypothesis_evidence_candidates === 1
      && capabilities.hypothesis_market_control === 1
      && capabilities.unified_delivery === 1
      && capabilities.desktop_system_notifications === 1
      && capabilities.epaper_delivery_receipts === 1
      && capabilities.notification_deep_links === 1
      && capabilities.delivery_timeline === 1
      && capabilities.product_diagnostics === 1
      && capabilities.diagnostics_export === 1
      && capabilities.desktop_heartbeat === 1
      && capabilities.diagnostic_repairs === 1
      && capabilities.diagnostic_history === 1
      && capabilities.diagnostic_issue_template === 1
      && capabilities.service_plan_preview === 1
      && capabilities.service_plan_confirm === 1
      && capabilities.routine_timeline === 1
      && capabilities.routine_skip_pause === 1
      && capabilities.routine_effectiveness === 1
      && capabilities.routine_effect_suggestions === 1
      && capabilities.routine_effect_undo === 1
      && capabilities.research_cockpit === 1
      && capabilities.research_priority_controls === 1
      && capabilities.research_cockpit_context === 1
      && capabilities.research_memory === 1
      && capabilities.research_memory_controls === 1
      && capabilities.research_memory_context === 1
      && capabilities.research_workflows === 1
      && capabilities.research_workflow_preview === 1
      && capabilities.research_workflow_permissions === 1
      && capabilities.research_result_cards === 1
      && capabilities.research_template_parameters === 1
      && capabilities.research_run_comparison === 1
      && capabilities.research_workflow_lineage === 1
      && capabilities.research_evidence_timeline === 1
      && capabilities.research_watch === 1
      && capabilities.research_suggestion_inbox === 1
      && capabilities.research_suggestion_preview === 1
      && capabilities.research_handoff === 1
      && capabilities.research_journey === 1
      && capabilities.epaper_research_workflow === 1 ? base : '';
  } catch { return ''; }
}

async function discoverBase() {
  if (!EMBEDDED) return '';
  if (cachedBase) return cachedBase;
  const preferred = configuredBase();
  const candidates = [...new Set([preferred, ...LOCAL_BASES].filter(Boolean))];
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 3500);
  try {
    const results = await Promise.all(candidates.map(base => probeBase(base, controller.signal)));
    const found = results.find(Boolean);
    if (!found) throw new Error('没有找到兼容的深脉 1.34.0+ 本地服务');
    cachedBase = found;
    return found;
  } finally {
    clearTimeout(timer);
  }
}

async function fetchLocal(path, options = {}, timeoutMs = 20000) {
  let base = await discoverBase();
  const run = async () => {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      return await fetch(base + path, { cache: 'no-store', ...options, signal: controller.signal });
    } finally {
      clearTimeout(timer);
    }
  };
  try {
    return await run();
  } catch {
    if (EMBEDDED) cachedBase = null;
    await new Promise(resolve => setTimeout(resolve, 500));
    base = await discoverBase();
    try { return await run(); }
    catch { throw new Error('无法连接兼容的深脉本地服务，请确认桌面 App 已启动'); }
  }
}

async function request(path, timeoutMs = 20000) {
  const response = await fetchLocal(path, {}, timeoutMs);
  if (!response.ok) throw new Error('HTTP ' + response.status);
  const body = await response.json();
  if (!body || body.ok !== true) throw new Error((body && body.error) || '接口返回异常');
  return body.data;
}

async function post(path, payload, timeoutMs = 20000) {
  const response = await fetchLocal(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: payload === undefined ? undefined : JSON.stringify(payload),
  }, timeoutMs);
  const body = await response.json();
  if (!response.ok || !body || body.ok !== true) throw new Error((body && body.error) || ('HTTP ' + response.status));
  return body.data;
}

async function requestBlob(path, timeoutMs = 60000) {
  const response = await fetchLocal(path, {}, timeoutMs);
  if (!response.ok) throw new Error('HTTP ' + response.status);
  return response.blob();
}

export const api = {
  health: () => request('/api/health'),
  sources: () => request('/api/sources'),
  diagnostics: () => request('/api/diagnostics', 10000),
  diagnosticsBundle: () => requestBlob('/api/diagnostics/export.zip', 15000),
  repairDiagnostics: (action) => post('/api/diagnostics/repair', { action }, 30000),
  tdxStatus: (fresh = false) => request('/api/tdx/status?probe=1' + (fresh ? '&fresh=1' : ''), 10000),
  akshareStatus: (probe = true) => request('/api/akshare/status?probe=' + (probe ? '1' : '0'), 30000),
  akshareResearchConfig: () => request('/api/akshare/research-config', 10000),
  saveAkshareResearchConfig: (preferences) => post('/api/akshare/research-config', { preferences }, 10000),
  akshareResearch: (refresh = false) => request('/api/akshare/research-snapshot?refresh=' + (refresh ? '1' : '0'), 120000),
  disclosures: (code, n = 8) => request(`/api/disclosures?code=${encodeURIComponent(code)}&n=${n}`),
  brain: () => request('/api/brain'),
  profile: () => request('/api/profile', 5000),
  saveProfile: (data) => post('/api/profile', { data }, 5000),
  saveBriefReceipt: (receipt, read = true) => post('/api/profile/brief-receipt', { receipt, read }, 5000),
  saveAttentionItem: (item, remove = false) => post('/api/profile/attention-item', { item, remove }, 5000),
  attentionLearning: () => request('/api/attention/learning', 5000),
  attentionTriage: () => request('/api/attention/triage', 5000),
  mutateAttentionTriage: (groupId, action, signal = null, surface = 'web') => post(
    '/api/attention/triage', { groupId, action, signal, surface }, 5000),
  saveAttentionFeedback: (itemId, signal, surface = 'web') => post('/api/profile/attention-feedback', { itemId, signal, surface }, 5000),
  resetAttentionLearning: (kind = null, clearHistory = false) => post('/api/attention/learning/reset', { kind, clearHistory }, 5000),
  deliveryStatus: () => request('/api/delivery/status', 5000),
  pullDelivery: (channel, consumer = 'web') => post('/api/delivery/pull', { channel, consumer }, 5000),
  acknowledgeDelivery: (channel, itemId, status = 'delivered', consumer = 'web', error = '') =>
    post('/api/delivery/ack', { channel, itemId, status, consumer, error }, 5000),
  retryDelivery: (channel, itemId) => post('/api/delivery/retry', { channel, itemId }, 5000),
  monitorStatus: () => request('/api/monitor/status', 5000),
  saveMonitorConfig: (config) => post('/api/monitor/config', { config }, 5000),
  routineStatus: () => request('/api/routine/status', 5000),
  saveRoutineConfig: (config) => post('/api/routine/config', { config }, 5000),
  previewServicePlan: (text) => post('/api/service-plan/preview', { text }, 5000),
  applyServicePlan: (draft) => post('/api/service-plan/apply', { draft, confirmed: true }, 5000),
  mutateRoutine: (action) => post('/api/routine/action', { action }, 5000),
  routineEffectiveness: () => request('/api/routine/effectiveness', 5000),
  mutateRoutineEffect: (action, suggestionId = null, actionId = null) => post(
    '/api/routine/effectiveness', { action, suggestionId, actionId, confirmed: action === 'apply_suggestion' }, 5000),
  eventImpact: () => request('/api/event-impact', 90000),
  eventServiceStatus: () => request('/api/event-service/status', 5000),
  saveEventServiceConfig: (config) => post('/api/event-service/config', { config }, 10000),
  researchHypotheses: () => request('/api/research-hypotheses', 10000),
  mutateResearchHypothesis: (action, payload = {}) => post('/api/research-hypotheses', { action, ...payload }, 30000),
  researchCockpit: () => request('/api/research-cockpit', 10000),
  mutateResearchCockpit: (action, itemId) => post('/api/research-cockpit', { action, itemId }, 10000),
  researchMemory: () => request('/api/research-memory', 10000),
  mutateResearchMemory: (action, payload = {}) => post('/api/research-memory', { action, ...payload }, 10000),
  researchWorkflows: () => request('/api/research-workflows', 10000),
  mutateResearchWorkflow: (action, payload = {}) => post('/api/research-workflows', { action, ...payload }, 90000),
  researchSuggestions: () => request('/api/research-suggestions', 10000),
  mutateResearchSuggestion: (action, payload = {}) => post('/api/research-suggestions', { action, ...payload }, 10000),
  deviceConfig: () => request('/api/device/config', 10000),
  saveDeviceConfig: (config) => post('/api/device/config', { config }, 15000),
  rotateDeviceToken: () => post('/api/device/token/rotate', undefined, 15000),
  deviceState: (demo = '') => request('/api/device/state' + (demo ? `?demo=${encodeURIComponent(demo)}` : ''), 60000),
  devicePreview: (demo = '') => requestBlob('/api/device/preview.bmp' + (demo ? `?demo=${encodeURIComponent(demo)}` : ''), 60000),
  deviceFrame: (demo = '') => requestBlob('/api/device/frame.bin' + (demo ? `?demo=${encodeURIComponent(demo)}` : ''), 60000),
  indices: () => request('/api/indices'),
  emotion: (record) => request('/api/emotion' + (record ? '?record=1' : ''), 60000),
  recordSnapshot: () => post('/api/emotion/record'),
  ladder: (type = 'ZT') => request('/api/ladder?type=' + type),
  premium: () => request('/api/premium', 60000),
  dragon: () => request('/api/dragon', 60000),
  dragonSeats: (code, date) => request(`/api/dragon-seats?code=${encodeURIComponent(code)}&date=${encodeURIComponent(date || '')}`, 60000),
  sectorCycle: () => request('/api/sector-cycle', 90000),
  weights: () => request('/api/weights'),
  saveWeights: (weights) => post('/api/weights', { weights }),
  quote: (code) => request('/api/quote?code=' + encodeURIComponent(code)),
  kline: (code, klt = 101, fqt = 1, n = 320) =>
    request(`/api/kline?code=${encodeURIComponent(code)}&klt=${klt}&fqt=${fqt}&n=${n}`),
  rank: (sort = 'up') => request('/api/rank?sort=' + sort),
  sectors: () => request('/api/sectors'),
  sectorsFlow: () => request('/api/sectors-flow'),
  news: () => request('/api/news'),
  search: (q) => request('/api/search?q=' + encodeURIComponent(q)),
  chat: (messages) => post('/api/chat', { messages }, 60000),
};
