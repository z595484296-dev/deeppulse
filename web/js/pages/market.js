/* 深脉 DeepPulse — 行情页（个股K线 + 实时行情） */

import { api } from '../api.js?v=1.18.0';
import { marketState, addWatch, loadWatch, emit, state } from '../store.js?v=1.18.0';
import { klineChart } from '../charts.js?v=1.18.0';
import { fmtPct, fmtPrice, fmtBig, pctClass, esc, debounce, toast, UP, DOWN, phaseBandsOf } from '../util.js?v=1.18.0';

let built = false;
let timer = null;

const PERIODS = [
  { klt: 101, label: '日K', n: 320 },
  { klt: 102, label: '周K', n: 200 },
  { klt: 103, label: '月K', n: 120 },
];

export function init(container) {
  if (built) return;
  built = true;
  container.innerHTML = `
    <div class="grid g12">
      <div class="card span-12" style="padding:14px 18px">
        <div style="display:flex;gap:14px;align-items:center;flex-wrap:wrap">
          <div class="search-box" style="width:300px">
            <svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7" fill="none" stroke="currentColor" stroke-width="2"/><path d="M20 20l-3.8-3.8" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
            <input id="mk-search" placeholder="输入代码或名称，如 600519 / 茅台" autocomplete="off"
              role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="mk-results">
            <div class="search-results" id="mk-results" role="listbox"></div>
          </div>
          <div class="tabs" id="mk-periods">
            ${PERIODS.map((p, i) => `<button type="button" class="tab ${i === 0 ? 'active' : ''}" data-klt="${p.klt}">${p.label}</button>`).join('')}
            <button type="button" class="tab" id="mk-fqt" title="复权方式">前复权</button>
          </div>
          <div class="tabs" id="mk-indicators" title="副图指标">
            <button type="button" class="tab active" data-ind="macd">VOL · MACD</button>
            <button type="button" class="tab" data-ind="kdj">KDJ</button>
            <button type="button" class="tab" data-ind="rsi">RSI</button>
          </div>
          <div style="display:flex;gap:8px;margin-left:auto">
            <button class="btn" id="mk-add-watch">☆ 加自选</button>
            <button class="btn" id="mk-zoom-all" title="查看全部K线">完整区间</button>
          </div>
        </div>
      </div>

      <div class="card span-12" id="mk-hero" style="padding:16px 20px">
        <div class="empty">输入代码或名称开始分析 · 支持 A股 / 指数 / 板块（如 BK0815 昨日涨停）</div>
      </div>

      <div class="card span-12" style="padding:10px 8px 4px">
        <div id="mk-chart" class="chart" style="height:540px"></div>
      </div>

      <div class="card span-12" id="mk-disclosures" style="display:none"></div>
    </div>
  `;

  const searchEl = container.querySelector('#mk-search');
  const resEl = container.querySelector('#mk-results');
  let searchHits = [];
  let activeHit = -1;
  let searchSeq = 0;

  const renderHits = () => {
    if (!searchHits.length) {
      resEl.innerHTML = '<div class="empty">未找到匹配标的</div>';
    } else {
      resEl.innerHTML = searchHits.map((h, index) => `
        <button type="button" class="sr-item ${index === activeHit ? 'active' : ''}" role="option"
          id="mk-option-${index}" aria-selected="${index === activeHit}" data-code="${esc(h.code)}" data-name="${esc(h.name)}">
          <span class="sr-name">${esc(h.name)}</span>
          <span class="sr-code">${esc(h.code)}</span>
        </button>`).join('');
    }
    searchEl.setAttribute('aria-expanded', 'true');
    if (activeHit >= 0) searchEl.setAttribute('aria-activedescendant', `mk-option-${activeHit}`);
    else searchEl.removeAttribute('aria-activedescendant');
    resEl.classList.add('show');
  };

  const chooseHit = (hit) => {
    if (!hit) return;
    loadStock(container, hit.code, hit.name);
    searchHits = [];
    activeHit = -1;
    searchEl.value = '';
    searchEl.setAttribute('aria-expanded', 'false');
    resEl.classList.remove('show');
  };

  const doSearch = debounce(async (q) => {
    const seq = ++searchSeq;
    if (!q) {
      searchHits = [];
      searchEl.setAttribute('aria-expanded', 'false');
      resEl.classList.remove('show');
      return;
    }
    try {
      const hits = await api.search(q);
      if (seq !== searchSeq) return;
      searchHits = hits;
      activeHit = hits.length ? 0 : -1;
      renderHits();
    } catch (e) { /* 静默 */ }
  }, 260);

  searchEl.addEventListener('input', () => {
    searchHits = [];
    activeHit = -1;
    doSearch(searchEl.value.trim());
  });
  searchEl.addEventListener('keydown', e => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      if (!searchHits.length) return;
      e.preventDefault();
      const delta = e.key === 'ArrowDown' ? 1 : -1;
      activeHit = (activeHit + delta + searchHits.length) % searchHits.length;
      renderHits();
      resEl.querySelector(`#mk-option-${activeHit}`)?.scrollIntoView({ block: 'nearest' });
      return;
    }
    if (e.key === 'Enter') {
      e.preventDefault();
      const q = searchEl.value.trim();
      if (!q) return;
      if (searchHits.length) {
        chooseHit(searchHits[Math.max(0, activeHit)]);
      } else if (/^(?:\d{6}|BK\d{4})$/i.test(q)) {
        loadStock(container, q, q);
        resEl.classList.remove('show');
      } else {
        api.search(q).then(hits => chooseHit(hits && hits[0])).catch(() => toast('未找到匹配标的', 'err'));
      }
    } else if (e.key === 'Escape') {
      searchEl.setAttribute('aria-expanded', 'false');
      resEl.classList.remove('show');
    }
  });
  resEl.addEventListener('click', e => {
    const it = e.target.closest('.sr-item');
    if (it) {
      chooseHit({ code: it.dataset.code, name: it.dataset.name });
    }
  });
  document.addEventListener('click', e => {
    if (!e.target.closest('.search-box')) {
      searchEl.setAttribute('aria-expanded', 'false');
      resEl.classList.remove('show');
    }
  });

  container.querySelector('#mk-periods').addEventListener('click', e => {
    const t = e.target.closest('.tab');
    if (!t || t.id === 'mk-fqt') return;
    if (t.dataset.klt) {
      container.querySelectorAll('#mk-periods .tab[data-klt]').forEach(x => x.classList.remove('active'));
      t.classList.add('active');
      marketState.klt = +t.dataset.klt;
      const p = PERIODS.find(x => x.klt === marketState.klt);
      marketState.n = p ? p.n : 320;
      reloadKline(container);
    }
  });
  container.querySelector('#mk-indicators').addEventListener('click', e => {
    const t = e.target.closest('.tab');
    if (!t) return;
    container.querySelectorAll('#mk-indicators .tab').forEach(x => x.classList.remove('active'));
    t.classList.add('active');
    marketState.ind = t.dataset.ind || 'macd';
    reloadKline(container);
  });
  container.querySelector('#mk-fqt').addEventListener('click', () => {
    marketState.fqt = marketState.fqt === 1 ? 0 : 1;
    container.querySelector('#mk-fqt').textContent = marketState.fqt === 1 ? '前复权' : '不复权';
    reloadKline(container);
  });
  container.querySelector('#mk-add-watch').addEventListener('click', () => {
    if (!marketState.code) { toast('请先选择一只股票', 'err'); return; }
    const ok = addWatch({ code: marketState.code, name: marketState.name });
    toast(ok ? `已将 ${marketState.name} 加入自选` : '该股票已在自选中', ok ? 'ok' : 'err');
    emit('watch-changed');
  });
  container.querySelector('#mk-zoom-all').addEventListener('click', () => reloadKline(container, true));

  // 外部打开（指数卡/榜单点击）
  document.addEventListener('open-quote', e => {
    loadStock(container, e.detail.code, e.detail.name);
  });

  // 主题切换时重建K线图表
  document.addEventListener('theme-changed', () => {
    if (marketState.code) reloadKline(container);
  });
}

function loadStock(container, code, name) {
  marketState.code = code;
  marketState.name = name;
  marketState.disclosures = [];
  container.querySelector('#mk-search').value = '';
  renderHeroSkeleton(container);
  loadDisclosures(container);
  reloadKline(container);
  startQuoteTimer(container);
}

async function loadDisclosures(container) {
  const el = container.querySelector('#mk-disclosures');
  if (!el || !marketState.code || String(marketState.code).toUpperCase().startsWith('BK')) {
    if (el) el.style.display = 'none';
    return;
  }
  const code = marketState.code;
  el.style.display = '';
  el.innerHTML = `
    <div class="disclosure-head">
      <div class="card-title">官方公告</div>
      <span class="source-tier official">一级源 · 巨潮资讯</span>
      <span class="card-sub">正在读取公告原文索引…</span>
    </div>`;
  try {
    const data = await api.disclosures(code);
    if (marketState.code !== code) return;
    marketState.disclosures = (data.items || []).slice(0, 6).map(item => ({
      date: item.date, title: item.title, url: item.pdf_url,
    }));
    const fetched = data.fetched_at ? new Date(data.fetched_at).toLocaleString('zh-CN', { hour12: false }) : '--';
    const rows = (data.items || []).slice(0, 6).map(item => `
      <a class="disclosure-item" href="${esc(item.pdf_url)}" target="_blank" rel="noopener noreferrer">
        <span class="disclosure-date">${esc(item.date || '--')}</span>
        <span class="disclosure-title">${esc(item.title)}</span>
        ${item.focus ? '<span class="disclosure-focus">重点核验</span>' : '<span></span>'}
      </a>`).join('');
    const stateText = data.degraded
      ? `<span class="card-sub" style="color:var(--amber)">${esc(data.error || '官方源暂时不可用')}</span>`
      : `<span class="card-sub">拉取于 ${esc(fetched)} · 点击查看 PDF 原文</span>`;
    el.innerHTML = `
      <div class="disclosure-head">
        <div class="card-title">官方公告</div>
        <span class="source-tier official">一级源 · 巨潮资讯</span>
        ${stateText}
        <a class="btn sm" style="margin-left:auto" href="${esc(data.query_url)}" target="_blank" rel="noopener noreferrer">官方查验</a>
      </div>
      ${rows ? `<div class="disclosure-list">${rows}</div>` : '<div class="empty" style="padding:18px 10px">暂无匹配公告，请使用“官方查验”确认</div>'}`;
  } catch (e) {
    if (marketState.code !== code) return;
    const query = `https://www.cninfo.com.cn/new/fulltextSearch?keyWord=${encodeURIComponent(code)}`;
    marketState.disclosures = [];
    el.innerHTML = `
      <div class="disclosure-head">
        <div class="card-title">官方公告</div>
        <span class="source-tier official">一级源 · 巨潮资讯</span>
        <span class="card-sub" style="color:var(--amber)">结构化查询暂不可用，未生成替代内容</span>
        <a class="btn sm" style="margin-left:auto" href="${query}" target="_blank" rel="noopener noreferrer">前往官方查验</a>
      </div>`;
  }
}

function renderHeroSkeleton(container) {
  container.querySelector('#mk-hero').innerHTML = `
    <div class="quote-hero">
      <div>
        <div style="display:flex;align-items:baseline;gap:8px">
          <span class="quote-name">${esc(marketState.name || marketState.code)}</span>
          <span class="quote-code">${esc(marketState.code)}</span>
          <span id="mk-tags"></span>
        </div>
        <div style="display:flex;align-items:baseline;gap:12px;margin-top:6px">
          <span class="quote-price num" id="mk-price">--</span>
          <span class="quote-delta num" id="mk-delta">--</span>
        </div>
      </div>
      <div class="quote-meta" id="mk-meta" style="margin-left:auto"></div>
    </div>`;
}

function renderQuote(container, q) {
  const cls = pctClass(q.pct);
  container.querySelector('#mk-price').textContent = fmtPrice(q.price);
  container.querySelector('#mk-price').className = 'quote-price num ' + cls;
  container.querySelector('#mk-delta').innerHTML =
    `${fmtPct(q.pct)} <span style="font-size:12.5px;color:var(--text-3)">${Number(q.chg) > 0 ? '+' : ''}${fmtPrice(Number(q.chg))}</span>`;
  container.querySelector('#mk-delta').className = 'quote-delta num ' + cls;
  const M = [
    ['今开', fmtPrice(q.open)], ['最高', fmtPrice(q.high)], ['最低', fmtPrice(q.low)],
    ['昨收', fmtPrice(q.prev_close)], ['成交量', fmtBig(q.volume * 100) + '股'],
    ['成交额', fmtBig(q.amount)], ['换手率', (q.turnover ?? 0) + '%'],
    ['量比', q.vol_ratio || '--'], ['市盈率TTM', q.pe > 0 ? q.pe.toFixed(2) : '--'],
    ['市净率', q.pb > 0 ? q.pb.toFixed(2) : '--'],
    ['总市值', fmtBig(q.mktcap)], ['流通市值', fmtBig(q.float_mktcap)],
  ];
  container.querySelector('#mk-meta').innerHTML = M.map(([k, v]) => `
    <div class="qm-item"><span class="qm-label">${k}</span><span class="qm-value num">${v}</span></div>`).join('');
}

function startQuoteTimer(container) {
  if (timer) clearInterval(timer);
  timer = setInterval(async () => {
    if (!marketState.code) return;
    try {
      const q = await api.quote(marketState.code);
      marketState.quote = q;
      if (document.getElementById('pages').querySelector('#page-market.active') && marketState.code) {
        renderQuote(container, q);
      }
    } catch { /* 静默 */ }
  }, 5000);
}

async function reloadKline(container, zoomAll = false) {
  if (!marketState.code) return;
  const el = container.querySelector('#mk-chart');
  el.innerHTML = '<div class="skeleton" style="height:100%"></div>';
  try {
    const [q, k] = await Promise.all([
      api.quote(marketState.code),
      api.kline(marketState.code, marketState.klt, marketState.fqt, marketState.n),
    ]);
    marketState.quote = q;
    renderQuote(container, q);
    // 情绪阶段色带（日K时叠加，来自收盘快照历史）
    const bands = marketState.klt === 101
      ? phaseBandsOf((state.emotion && state.emotion.history) || [])
      : [];
    const chart = klineChart(el, k.rows, {
      name: marketState.name,
      pct: k.rows.length && k.rows[k.rows.length - 1].close > 100 ? 2 : 3,
      indicator: marketState.ind || 'macd',
      bands,
    });
    if (zoomAll) chart.dispatchAction({ type: 'dataZoom', start: 0, end: 100 });
    renderTags(container);
  } catch (e) {
    el.innerHTML = `<div class="empty">加载失败：${esc(e.message)}</div>`;
  }
}

async function renderTags(container) {
  const tags = container.querySelector('#mk-tags');
  tags.innerHTML = '';
  try {
    const em = await api.emotion();
    if (!em || !em.pools) return;
    const zt = em.pools.ZT.pool || [];
    const dt = em.pools.DT.pool || [];
    const zb = em.pools.ZB.pool || [];
    const hits = [];
    zt.forEach(it => { if (it.code === marketState.code) hits.push(`<span class="badge red">涨停${it.lbc >= 2 ? ' · ' + it.lbc + '连板' : ''}</span>`); });
    dt.forEach(it => { if (it.code === marketState.code) hits.push('<span class="badge green">跌停</span>'); });
    zb.forEach(it => { if (it.code === marketState.code) hits.push('<span class="badge amber">炸板</span>'); });
    tags.innerHTML = hits.join('');
  } catch { /* 静默 */ }
}

export async function refresh(container, data) {
  init(container);
  // 行情页主要依赖自身定时器与用户交互，此处置空
}
