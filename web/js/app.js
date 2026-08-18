/* 深脉 DeepPulse — 应用主控：路由 / 轮询 / 顶栏 / 状态栏 */

import { api } from './api.js?v=1.5.0';
import { state, marketState, emit, loadAlerts, markTriggered, syncProfile } from './store.js?v=1.5.0';
import { esc, fmtPct, fmtPrice, pctClass, tradingState, toast } from './util.js?v=1.5.0';

import * as pageOverview from './pages/overview.js?v=1.5.0';
import * as pageEmotion from './pages/emotion.js?v=1.5.0';
import * as pageMarket from './pages/market.js?v=1.5.0';
import * as pageLadder from './pages/ladder.js?v=1.5.0';
import * as pageWatch from './pages/watch.js?v=1.5.0';
import * as pageStrategy from './pages/strategy.js?v=1.5.0';
import * as pageEpaper from './pages/epaper.js?v=1.5.0';
import * as pageDatasrc from './pages/datasrc.js?v=1.5.0';
import * as pageAbout from './pages/about.js?v=1.5.0';
import { createChatView, chatStore, ensureGreeting } from './chat.js?v=1.5.0';
import { EMBEDDED, initBridge, exitToSession, applyTheme, askDeepSeek, setBridgeContextProvider } from './bridge.js?v=1.5.0';
import { initOnboarding } from './onboarding.js?v=1.5.0';

const PAGES = {
  overview: { title: '总览', mod: pageOverview, freq: 'emotion' },
  emotion: { title: '情绪周期', mod: pageEmotion, freq: 'emotion' },
  market: { title: '行情', mod: pageMarket, freq: 'none' },
  ladder: { title: '涨停梯队', mod: pageLadder, freq: 'ladder' },
  watch: { title: '自选', mod: pageWatch, freq: 'none' },
  strategy: { title: '策略', mod: pageStrategy, freq: 'emotion' },
  epaper: { title: '墨水屏', mod: pageEpaper, freq: 'manual' },
  datasrc: { title: '数据源', mod: pageDatasrc, freq: 'manual' },
  about: { title: '关于我', mod: pageAbout, freq: 'none' },
};

let currentPage = 'overview';
let emotionTimer = null, indicesTimer = null, newsTimer = null, alertTimer = null;
let booted = false;
let lastPhase = null;      // 情绪阶段变化提醒
let netFailures = 0;       // 连续失败计数 → 全局断线横幅

// 异动监控状态（涨停池/炸板池差分）
let prevZT = null;         // Map<code, lbc>
const moves = [];          // [{t, type, text}]

const $ = (sel) => document.querySelector(sel);

/* ---------------- 路由 ---------------- */
function goto(page, force = false) {
  if (!PAGES[page]) return;
  if (currentPage === page && !force) return;
  currentPage = page;
  document.body.dataset.page = page;
  document.querySelectorAll('.nav-item').forEach(n => {
    const active = n.dataset.page === page;
    n.classList.toggle('active', active);
    if (active) n.setAttribute('aria-current', 'page');
    else n.removeAttribute('aria-current');
  });
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  const el = $('#page-' + page);
  el.classList.add('active');
  $('#page-title').textContent = PAGES[page].title;
  PAGES[page].mod.init(el);
  if (location.hash !== '#' + page) history.replaceState(null, '', '#' + page);
  pushDataToPage(page);
}

function currentHarnessContext() {
  const em = state.emotion || {};
  const engine = em.engine || {};
  const tdx = em.tdx_local || {};
  const quote = marketState.quote || {};
  const raw = engine.raw || {};
  const rawKeys = [
    'zt', 'dt', 'zb', 'zb_rate', 'zt_equiv', 'dt_equiv', 'universe',
    'height', 'lb_count', 'zt_idx_pct', 'lb_idx_pct', 'up', 'down', 'flat',
    'up_ratio', 'turnover_yi', 'vol_ratio', 'volume_basis', 'flow_yi',
    'trend_pct', 'ma20', 'close',
  ];
  const rawMetrics = Object.fromEntries(rawKeys
    .filter(key => raw[key] !== undefined && raw[key] !== null)
    .map(key => [key, raw[key]]));
  const tdxFields = Object.entries(tdx.fields || {}).slice(0, 20).map(([key, value]) => {
    const row = value && typeof value === 'object' ? value : { value };
    const fieldValue = row.value;
    return {
      key: String(key).slice(0, 60),
      label: String(row.label || key).slice(0, 80),
      value: typeof fieldValue === 'number' || typeof fieldValue === 'boolean'
        ? fieldValue : String(fieldValue ?? '').slice(0, 120),
    };
  });
  const recentHistory = (em.history || []).slice(-20).map(s => ({
    date: s.date, temp: s.temp, phase: s.phase,
    coverage: s.coverage, confidence: s.confidence,
  }));
  return {
    page: currentPage,
    pageTitle: PAGES[currentPage] ? PAGES[currentPage].title : currentPage,
    asOf: state.lastUpdate ? new Date(state.lastUpdate).toISOString() : null,
    selectedSecurity: marketState.code ? {
      code: marketState.code, name: marketState.name,
      price: quote.price, pct: quote.pct,
      officialDisclosures: (marketState.disclosures || []).slice(0, 6),
    } : null,
    market: {
      dataDate: em.date || null,
      temperature: engine.temp ?? null,
      phase: engine.phase || null,
      phaseCandidate: engine.phase_candidate || null,
      direction: engine.dynamics && engine.dynamics.direction,
      delta1: engine.dynamics && engine.dynamics.delta1,
      delta3: engine.dynamics && engine.dynamics.delta3,
      coverage: engine.coverage ?? null,
      confidence: engine.confidence ?? null,
      consensus: engine.consensus ?? null,
      dimensions: (engine.dimensions || []).map(d => ({ name: d.name, value: d.value, coverage: d.coverage })),
      transition: engine.transition || null,
      divergences: engine.divergences || [],
      position: engine.advice && engine.advice.position,
      actionable: !!engine.actionable,
      riskSignals: (engine.flags || []).slice(0, 6).map(f => f.text),
      degraded: !!engine.degraded,
      sourceVerification: {
        tdxLocal: {
          status: tdx.status || 'unavailable',
          fieldsAvailable: Object.keys(tdx.fields || {}).length,
          readOnly: true,
          asOf: tdx.as_of || null,
          reason: tdx.reason || null,
          error: tdx.error || null,
          fields: tdxFields,
        },
      },
    },
    emotionAnalysis: {
      modelVersion: engine.model_version || null,
      formula: 'temperature = clamp(50 + 2.5 × weightedMean(score), 0, 100)',
      scoreRange: [-20, 20],
      phaseThresholds: [
        { name: '冰点期', min: 0, max: 20, condition: '0 ≤ temp < 20' },
        { name: '修复期', min: 20, max: 40, condition: '20 ≤ temp < 40' },
        { name: '发酵期', min: 40, max: 60, condition: '40 ≤ temp < 60' },
        { name: '高潮期', min: 60, max: 80, condition: '60 ≤ temp < 80' },
        { name: '亢奋期', min: 80, max: 100, condition: '80 ≤ temp ≤ 100' },
      ],
      positionNature: '研究仓位区间；由阶段映射，并受数据可信度门控，不构成投资建议',
      transitionCalibrated: engine.transition ? engine.transition.calibrated === true : false,
      raw: rawMetrics,
      signals: (engine.signals || []).slice(0, 16).map(s => ({
        key: s.key, name: s.name, value: s.value, display: s.display, unit: s.unit,
        score: s.score, weight: s.weight, contribution: s.contribution,
        available: !!s.avail, note: s.note,
      })),
      history: recentHistory,
      missing: (engine.missing || []).slice(0, 16),
    },
    indices: (state.indices || []).slice(0, 5).map(i => ({
      code: i.code, name: i.name, price: i.price, pct: i.pct,
    })),
    sources: [
      { name: '巨潮资讯', tier: 'official', role: '公司公告原文' },
      { name: '上交所/深交所/证监会', tier: 'official', role: '官方查验入口' },
      { name: '通达信 TQ-Local', tier: 'local', role: '本地只读行情与市场统计交叉验证', status: tdx.status || 'unavailable' },
      { name: '东方财富/腾讯行情', tier: 'market', role: '行情与市场线索' },
    ],
    disclaimer: '数据仅供研究参考，不构成投资建议。',
  };
}

function askCurrentPage() {
  const target = marketState.code ? `${marketState.name || marketState.code}（${marketState.code}）` : '当前市场';
  const emotionFocus = currentPage === 'emotion'
    ? '请完整使用 emotionAnalysis 中的模型公式、阶段阈值、原始指标、11项信号、维度、历史与缺失项；当前未选中个股时，不要把公告为空误判为公告源故障。'
    : '';
  return askDeepSeek({
    question: `请基于深脉当前的「${PAGES[currentPage].title}」页面，分析${target}。${emotionFocus}请区分事实与推断，优先核对适用的官方披露，指出数据时点、风险、反证条件和下一步应查的数据。`,
    context: { intent: 'analyze-current-page' },
  });
}

function pushDataToPage(page) {
  const el = $('#page-' + page);
  if (!el) return;
  const mod = PAGES[page].mod;
  if (state.emotion && mod.refresh) {
    if (page === 'ladder' || page === 'emotion' || page === 'strategy' || page === 'overview') {
      mod.refresh(el, { emotion: state.emotion, indices: state.indices }).catch(() => {});
    }
  }
  if (page === 'overview' && state.emotion) {
    pageOverview.refreshSecondary(el).catch(() => {});
  }
  if (page === 'datasrc') {
    pageDatasrc.refresh(el).catch(() => {});
  }
}

/* ---------------- 顶栏：指数走马灯 ---------------- */
function renderTape() {
  const idx = state.indices || [];
  if (!idx.length) return;
  const items = idx.filter(i => i && i.name).map(i => {
    const cls = pctClass(i.pct);
    return `<span class="tape-item"><span class="t-name">${esc(i.name)}</span>
      <span class="t-price num ${cls}">${fmtPrice(i.price)}</span>
      <span class="num ${cls}">${fmtPct(i.pct)}</span></span>`;
  }).join('');
  const track = $('#tape-track');
  const clone = items.replaceAll('<span class="tape-item"', '<span aria-hidden="true" class="tape-item"');
  track.innerHTML = items + clone; // 双份实现无缝循环；复制段不重复朗读
  track.style.animationDuration = Math.max(24, idx.length * 7) + 's';
}

/* ---------------- 快讯条 ---------------- */
function renderNewsline() {
  const news = state.news || [];
  const items = news.slice(0, 18).map(n =>
    `<span class="news-item" data-url="${esc(n.url)}" title="市场聚合资讯 · ${esc(n.source_name || '来源未标注')}"><span class="n-time">${esc(n.time)}</span>${esc(n.title)}<span class="n-source"> · ${esc(n.source_name || '市场资讯')}</span></span>`).join('');
  const track = $('#newsline-track');
  const clone = items.replaceAll('<span class="news-item"', '<span aria-hidden="true" class="news-item"');
  track.innerHTML = items ? items + clone : '<span class="news-item">快讯加载中…</span>';
}
$('#newsline-track')?.addEventListener('click', e => {
  const it = e.target.closest('.news-item');
  if (!it || !it.dataset.url) return;
  // 应用内弹层：不再跳出新标签页
  let modal = document.getElementById('news-modal');
  if (!modal) {
    modal = document.createElement('div');
    modal.id = 'news-modal';
    modal.className = 'seats-modal';
    modal.innerHTML = `
      <div class="seats-panel" style="width:520px">
        <div class="seats-head"><b>📰 快讯详情</b><button class="seats-close">✕</button></div>
        <div class="seats-body">
          <div id="news-modal-title" style="font-size:13.5px;line-height:1.8"></div>
          <div id="news-modal-time" style="font-size:11px;color:var(--text-3);margin-top:6px"></div>
          <div style="display:flex;gap:8px;margin-top:14px">
            <button class="btn sm primary" id="news-modal-open">在浏览器打开</button>
            <button class="btn sm ghost" id="news-modal-copy">复制链接</button>
          </div>
        </div>
      </div>`;
    document.body.appendChild(modal);
    modal.addEventListener('click', ev => {
      if (ev.target === modal || ev.target.closest('.seats-close')) modal.style.display = 'none';
    });
    modal.querySelector('#news-modal-open').addEventListener('click', () => {
      const url = modal.dataset.url;
      if (url) window.open(url, '_blank');
      modal.style.display = 'none';
    });
    modal.querySelector('#news-modal-copy').addEventListener('click', () => {
      navigator.clipboard?.writeText(modal.dataset.url || '').then(() => toast('链接已复制'));
      modal.style.display = 'none';
    });
  }
  modal.dataset.url = it.dataset.url;
  const timeEl = it.querySelector('.n-time');
  const timeText = timeEl ? timeEl.textContent : '';
  modal.querySelector('#news-modal-title').textContent = it.textContent.replace(timeText, '').trim();
  modal.querySelector('#news-modal-time').textContent = timeText;
  modal.style.display = 'grid';
});

/* ---------------- 状态栏与市场状态 ---------------- */
function renderMarketState() {
  const ts = tradingState();
  const el = $('#market-state');
  el.classList.toggle('open', ts.state === 'open');
  $('#state-text').textContent = ts.label + (state.emotion ? ` · ${state.emotion.date}` : '');
}

function renderStatus() {
  const set = (dot, txt, val, cls) => {
    const d = $(dot); if (d) d.className = 'dot ' + (cls || 'ok');
    const t = $(txt); if (t) t.textContent = val;
  };
  const ok = state.emotion != null;
  set('#dot-em', '#st-em', ok ? (state.degraded ? '部分降级' : '正常') : '连接中', ok ? (state.degraded ? 'warn' : 'ok') : 'warn');
  set('#dot-idx', '#st-idx', state.indices && state.indices.length ? '正常' : '--', state.indices && state.indices.length ? 'ok' : 'warn');
  set('#dot-news', '#st-news', state.news && state.news.length ? '正常' : '--', state.news && state.news.length ? 'ok' : 'warn');
  if (state.lastUpdate) {
    const d = new Date(state.lastUpdate);
    $('#st-updated').textContent = '最后更新 ' + d.toLocaleTimeString('zh-CN', { hour12: false });
  }
}

/* ---------------- 数据轮询 ---------------- */
function netBanner(show, label) {
  const el = $('#net-banner');
  if (!el) return;
  if (show) {
    el.textContent = '⚠ ' + (label || '数据连接中断') + '，正在自动重试…';
    el.classList.add('show');
  } else {
    el.classList.remove('show');
  }
}

async function pollEmotion() {
  try {
    const data = await api.emotion();
    state.emotion = data;
    state.degraded = !!(data.engine && data.engine.degraded);
    state.lastUpdate = Date.now();
    netFailures = 0;
    netBanner(false);
    emit('emotion', data);
    renderMarketState();
    renderStatus();
    diffMoves(data);
    trackIntraday(data.engine);
    // 独立窗口：标题实时反映市场体温（嵌入态由壳层设置标题）
    if (!EMBEDDED) {
      document.title = data.engine && data.engine.temp != null
        ? `深脉 ${data.engine.temp}° ${data.engine.phase} · 金融工作台`
        : '深脉 DeepPulse · AI 金融工作台';
    }
    // 情绪阶段变化提醒（周期切换是重要事件）
    const phase = data.engine && data.engine.phase;
    if (lastPhase && phase && phase !== lastPhase) {
      toast(`情绪阶段切换：${lastPhase} → ${phase}（温度 ${data.engine.temp}°）`, 'err', 6000);
    }
    if (phase) lastPhase = phase;
    $('#identity-sub').textContent = data.engine && data.engine.phase
      ? `心跳 ${data.engine.temp ?? '--'}° · ${data.engine.phase}` : '神经已连接';
    pushDataToPage(currentPage);
    pulseRefresh();
  } catch (e) {
    state.degraded = true;
    netFailures++;
    if (netFailures >= 2) netBanner(true, '情绪数据');
    renderStatus();
    $('#identity-sub').textContent = '神经连接中断，重试中…';
  }
}

async function pollIndices() {
  try {
    state.indices = await api.indices();
    netFailures = 0;
    netBanner(false);
    renderTape();
    renderStatus();
    if (currentPage === 'overview' && state.emotion) {
      pushDataToPage('overview');
    }
  } catch { /* 静默重试 */ }
}

/** 刷新感知：状态栏时间微闪 */
function pulseRefresh() {
  const el = $('#st-updated');
  if (!el) return;
  el.classList.remove('pulse');
  void el.offsetWidth;
  el.classList.add('pulse');
}

/** 价格提醒轮询（全局，自选页未打开也生效） */
async function pollAlerts() {
  const pending = loadAlerts().filter(a => !a.triggered);
  if (!pending.length) return;
  for (const al of pending) {
    try {
      const q = await api.quote(al.code);
      if (al.dir === 'up' && q.price >= al.price) {
        markTriggered(al.id);
        toast(`🔔 ${al.name || al.code} 已上破 ${al.price}（现价 ${q.price.toFixed(2)}）`, 'ok', 9000);
      } else if (al.dir === 'down' && q.price <= al.price) {
        markTriggered(al.id);
        toast(`🔔 ${al.name || al.code} 已下破 ${al.price}（现价 ${q.price.toFixed(2)}）`, 'err', 9000);
      }
    } catch { /* 单只失败不影响其他 */ }
  }
}

/** 异动差分：新涨停 / 炸板（仅交易时段播报，防止盘后修订误报） */
function diffMoves(data) {
  const ztPool = (data.pools && data.pools.ZT && data.pools.ZT.pool) || [];
  const zbPool = (data.pools && data.pools.ZB && data.pools.ZB.pool) || [];
  const zt = new Map(ztPool.map(it => [it.code, it.lbc || 1]));
  const zb = new Map(zbPool.map(it => [it.code, it.name || it.code]));
  const trading = tradingState().state === 'open';
  if (prevZT && trading) {
    for (const [code, lbc] of zt) {
      if (!prevZT.has(code)) {
        const it = ztPool.find(x => x.code === code);
        addMove('zt', `🔥 新涨停：${it ? it.name : code}${lbc >= 2 ? '（' + lbc + '连板）' : ''}`);
      }
    }
    for (const [code] of prevZT) {
      if (!zt.has(code) && zb.has(code)) {
        addMove('zb', `💥 炸板：${zb.get(code)}`);
      }
    }
  }
  prevZT = zt;
}

function addMove(type, text) {
  moves.unshift({ t: new Date().toLocaleTimeString('zh-CN', { hour12: false }), type, text });
  if (moves.length > 30) moves.pop();
  emit('moves', moves.slice());
  toast(text, type === 'zt' ? 'ok' : 'err', 7000);
}

/** 盘中温度轨迹：交易时段每 5 分钟记录一点（localStorage 持久，按日重置） */
let intradayLast = '';
function trackIntraday(engine) {
  if (tradingState().state !== 'open' || !engine || engine.temp == null) return;
  try {
    const d = new Date();
    const hm = d.toLocaleTimeString('zh-CN', { hour12: false }).slice(0, 5);
    const date = d.toLocaleDateString('sv');
    const key = 'dp_intraday_v1';
    let store = JSON.parse(localStorage.getItem(key) || '{"date":"","points":[]}');
    if (store.date !== date) { store = { date, points: [] }; intradayLast = ''; }
    if (hm === intradayLast) return;
    intradayLast = hm;
    store.points.push({ t: hm, temp: engine.temp });
    if (store.points.length > 120) store.points.shift();
    localStorage.setItem(key, JSON.stringify(store));
    emit('intraday', store.points.slice());
  } catch { /* 忽略 */ }
}

async function pollNews() {
  try {
    state.news = await api.news();
    renderNewsline();
    renderStatus();
  } catch { /* 静默 */ }
}

function schedule() {
  if (emotionTimer) clearInterval(emotionTimer);
  if (indicesTimer) clearInterval(indicesTimer);
  if (newsTimer) clearInterval(newsTimer);
  if (alertTimer) clearInterval(alertTimer);
  const open = tradingState().state === 'open';
  emotionTimer = setInterval(pollEmotion, open ? 30000 : 120000);
  indicesTimer = setInterval(pollIndices, open ? 8000 : 60000);
  newsTimer = setInterval(pollNews, open ? 60000 : 300000);
  alertTimer = setInterval(pollAlerts, 10000);
  if (open) pollAlerts();
}

/* ---------------- 时钟 ---------------- */
function tickClock() {
  const d = new Date();
  $('#clock').textContent = d.toLocaleTimeString('zh-CN', { hour12: false });
}

/* ---------------- 启动 ---------------- */
async function boot() {
  // 构建页面容器
  const pages = $('#pages');
  pages.innerHTML = Object.keys(PAGES).map(p => `<section class="page" id="page-${p}"></section>`).join('');

  document.querySelectorAll('.nav-item').forEach(n => {
    n.addEventListener('click', e => {
      e.preventDefault();
      goto(n.dataset.page);
    });
  });

  window.addEventListener('hashchange', () => {
    const p = location.hash.slice(1);
    if (PAGES[p]) goto(p);
  });

  // ---- 蚂小财：全局调度事件（对话里一句话跳页/刷数据） ----
  document.addEventListener('nav', e => {
    if (e.detail && e.detail.page) goto(e.detail.page);
  });
  document.addEventListener('refresh-all', () => {
    pollEmotion(); pollIndices(); pollNews();
  });

  // ---- 蚂小财：全局抽屉 ----
  const drawer = $('#mxc-drawer');
  const fab = $('#mxc-fab');
  $('#mxc-body').innerHTML = '';
  const chatView = createChatView($('#mxc-body'));
  let drawerOpen = false;
  const toggleDrawer = (open) => {
    drawerOpen = open;
    drawer.classList.toggle('open', open);
    drawer.setAttribute('aria-hidden', String(!open));
    fab.setAttribute('aria-expanded', String(open));
    if (open) ensureGreeting();
  };
  fab.addEventListener('click', () => toggleDrawer(!drawerOpen));
  $('#mxc-close').addEventListener('click', () => toggleDrawer(false));
  document.addEventListener('open-assistant', () => toggleDrawer(true));
  // 大脑状态（DeepSeek 云端 / 本地智脑兜底）
  api.brain().then(b => {
    const el = $('#mxc-brain');
    if (el) el.textContent = b && b.mode === 'llm'
      ? `${b.model} 本体 · 调度全局` : '本地智脑在线 · 调度全局';
  }).catch(() => {
    const el = $('#mxc-brain');
    if (el) el.textContent = '本地智脑在线 · 调度全局';
  });

  // ---- 全局断线横幅 ----
  const banner = document.createElement('div');
  banner.id = 'net-banner';
  document.body.appendChild(banner);

  // ---- 快捷键体系 + 帮助面板 ----
  const PAGE_KEYS = ['overview', 'emotion', 'market', 'ladder', 'watch', 'strategy', 'epaper', 'datasrc', 'about'];
  const PAGE_NAMES = { overview: '总览', emotion: '情绪周期', market: '行情', ladder: '涨停梯队', watch: '自选', strategy: '策略', epaper: '墨水屏', datasrc: '数据源', about: '关于我' };
  const helpEl = document.createElement('div');
  helpEl.id = 'help-overlay';
  helpEl.innerHTML = `
    <div class="help-panel">
      <div class="help-head"><b>键盘快捷键</b><button class="help-close">✕</button></div>
      <div class="help-grid">
        ${PAGE_KEYS.map((k, i) => `<div class="help-row"><kbd>${i + 1}</kbd><span>切换到${PAGE_NAMES[k]}</span></div>`).join('')}
        <div class="help-row"><kbd>?</kbd><span>打开/关闭本面板</span></div>
        <div class="help-row"><kbd>Esc</kbd><span>关闭面板/蚂小财抽屉</span></div>
        <div class="help-row"><kbd>/</kbd><span>聚焦行情搜索</span></div>
      </div>
      <div class="help-tip">💡 在会话里提到深脉链接，点击可直接打开工作台对应页面</div>
    </div>`;
  document.body.appendChild(helpEl);
  helpEl.addEventListener('click', e => { if (e.target === helpEl || e.target.closest('.help-close')) helpEl.classList.remove('show'); });
  window.addEventListener('keydown', (e) => {
    const tag = (e.target.tagName || '').toLowerCase();
    const typing = tag === 'input' || tag === 'textarea' || (e.target.isContentEditable);
    if (typing) return;
    if (e.key === 'Escape') {
      if (helpEl.classList.contains('show')) helpEl.classList.remove('show');
      else if (drawerOpen) toggleDrawer(false);
      return;
    }
    if (e.key === '?') { helpEl.classList.toggle('show'); return; }
    if (e.key === '/') {
      e.preventDefault();
      const inp = $('#mk-search');
      if (inp) { goto('market'); setTimeout(() => inp.focus(), 80); }
      return;
    }
    if (e.key >= '1' && e.key <= '9' && !e.ctrlKey && !e.altKey && !e.metaKey) {
      goto(PAGE_KEYS[Number(e.key) - 1]);
    }
  });

  $('#btn-refresh').addEventListener('click', async () => {
    const btn = $('#btn-refresh');
    btn.classList.add('spin');
    try {
      await Promise.all([pollEmotion(), pollIndices(), pollNews()]);
      toast('数据已刷新');
    } catch { toast('刷新失败，请检查网络', 'err'); }
    setTimeout(() => btn.classList.remove('spin'), 500);
  });

  const harnessBtn = $('#btn-harness');
  setBridgeContextProvider(currentHarnessContext);
  if (EMBEDDED && harnessBtn) {
    harnessBtn.style.display = '';
    harnessBtn.addEventListener('click', () => {
      harnessBtn.classList.add('pending');
      if (!askCurrentPage()) {
        harnessBtn.classList.remove('pending');
        toast('当前未连接 DeepSeek Harness', 'err');
      }
    });
  }
  document.addEventListener('ask-harness', () => {
    if (!askCurrentPage()) toast('请在 DeepSeek Harness 中打开深脉后使用', 'err');
  });
  document.addEventListener('harness-ask-result', e => {
    harnessBtn?.classList.remove('pending');
    const result = e.detail || {};
    if (result.ok) toast('已把当前页面与来源上下文交给 DeepSeek', 'ok');
    else toast(result.error || '发送失败，请先打开一个 Harness 会话', 'err', 6000);
  });

  tickClock();
  setInterval(tickClock, 1000);
  setInterval(() => { renderMarketState(); schedule(); }, 60000);

  // 双向桥：壳层导航指令 + 返回会话按钮（嵌入模式专属）
  initBridge();
  // 主题：嵌入态由壳层 dp-theme 推送；独立窗口跟随系统偏好（?theme= 参数优先）
  const tp = new URLSearchParams(location.search).get('theme');
  const wantLight = tp ? tp === 'light' : (window.matchMedia && matchMedia('(prefers-color-scheme: light)').matches);
  applyTheme(wantLight);
  // 主题切换后重渲染当前页图表（ECharts 在 setOption 时取色，重渲染即生效）
  document.addEventListener('theme-changed', () => {
    pushDataToPage(currentPage);
  });
  if (EMBEDDED) {
    const sessionBtn = $('#btn-session');
    if (sessionBtn) {
      sessionBtn.style.display = '';
      sessionBtn.addEventListener('click', () => exitToSession());
    }
  }

  try {
    await syncProfile();
  } catch (error) {
    toast('本机档案暂未同步，将继续使用当前端数据', 'err', 5000);
  }

  goto(location.hash.slice(1) || 'overview', true);
  await Promise.allSettled([pollEmotion(), pollIndices(), pollNews()]);
  booted = true;
  schedule();

  // 首次启动引导（四步闭环，仅一次）
  setTimeout(initOnboarding, 900);

  // 页面隐藏时暂停轮询，回来立即补一次
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      if (emotionTimer) clearInterval(emotionTimer);
      if (indicesTimer) clearInterval(indicesTimer);
      if (newsTimer) clearInterval(newsTimer);
    } else {
      pollEmotion(); pollIndices(); pollNews();
      schedule();
    }
  });
}

boot();
