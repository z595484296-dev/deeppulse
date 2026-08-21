/* 深脉 DeepPulse — 总览页 */

import { api } from '../api.js?v=1.8.0';
import { state, bus } from '../store.js?v=1.8.0';
import { loadJournal, loadWatch, loadAlerts, isBriefRead, setBriefRead } from '../store.js?v=1.8.0';
import { gaugeChart, breadthChart, flowChart, sparkChart, hbarChart } from '../charts.js?v=1.8.0';
import { fmtPct, fmtPrice, fmtBig, pctClass, esc, UP, DOWN, FLAT, PHASE_COLORS, fmtSeal, tradingState } from '../util.js?v=1.8.0';
import { buildProactiveBrief } from '../proactive.js?v=1.8.0';

let built = false;
let sparksAt = 0;
let sectorTab = 'up';
let currentBrief = null;

const INDEX_CODES = [
  { code: '000001', name: '上证指数' },
  { code: '399001', name: '深证成指' },
  { code: '399006', name: '创业板指' },
  { code: '000688', name: '科创50' },
  { code: '899050', name: '北证50' },
];

export function init(container) {
  if (built) return;
  built = true;
  container.innerHTML = `
    <section class="card proactive-card" id="ov-proactive" aria-labelledby="ov-proactive-title">
      <div class="proactive-head">
        <div>
          <div class="proactive-kicker"><span class="proactive-pulse" aria-hidden="true"></span><span id="ov-proactive-period">主动简报</span><span class="proactive-status" id="ov-proactive-status">正在汇总</span></div>
          <h2 id="ov-proactive-title">正在读取市场、风险和你的关注项…</h2>
        </div>
        <div class="proactive-head-actions">
          <button class="btn sm" id="ov-proactive-refresh" title="刷新市场数据并重新生成简报">刷新全部数据</button>
          <button class="btn sm ghost" id="ov-proactive-toggle" aria-expanded="true">收起</button>
        </div>
      </div>
      <div class="proactive-body" id="ov-proactive-body">
        <p class="proactive-summary" id="ov-proactive-summary">简报不会替你做决定，只把可信事实整理成下一步研究任务。</p>
        <div class="proactive-facts" id="ov-proactive-facts"></div>
        <div class="proactive-actions" id="ov-proactive-actions"></div>
        <button class="btn sm ghost proactive-more" id="ov-proactive-more" hidden></button>
        <div class="proactive-foot">
          <div class="proactive-evidence" id="ov-proactive-evidence"></div>
          <div class="proactive-foot-actions">
            <button class="btn sm primary" id="ov-ask-harness">让 DeepSeek 核对依据</button>
            <button class="btn sm" id="ov-proactive-handle">标记已读</button>
            <button class="btn sm ghost" id="ov-open-assistant">打开深脉助手</button>
          </div>
        </div>
      </div>
    </section>

    <div class="grid idx-grid" id="ov-indices" style="margin-top:14px"></div>

    <div class="grid g12" style="margin-top:14px">
      <div class="card span-4">
        <div class="card-head"><div class="card-title">情绪温度计</div><div class="card-sub" id="ov-temp-date">--</div></div>
        <div class="gauge-wrap">
          <div class="gauge-chart" id="ov-gauge"></div>
          <div class="temp-big num" id="ov-temp">--<span class="temp-unit">°</span></div>
          <div id="ov-phase"></div>
          <div class="temp-trend" id="ov-trend">--</div>
        </div>
      </div>

      <div class="card span-3">
        <div class="card-head"><div class="card-title">情绪核心指标</div></div>
        <div class="grid g2" id="ov-stats"></div>
      </div>

      <div class="card span-5">
        <div class="card-head"><div class="card-title">今日研究框架</div><div class="card-sub">引擎自动整理 · 非投资建议</div></div>
        <div class="advice-card">
          <div class="advice-title">风险暴露参考区间</div>
          <div class="advice-line"><span id="ov-position" style="font-size:26px;font-weight:800">--</span>
            <span style="margin-left:12px" id="ov-style"></span></div>
          <div class="advice-desc" id="ov-advice-desc">--</div>
        </div>
        <div style="margin-top:12px;font-size:12px;color:var(--text-2);line-height:1.8" id="ov-narrative">--</div>
      </div>
    </div>

    <div class="grid g12" style="margin-top:14px">
      <div class="card span-7">
        <div class="card-head"><div class="card-title">市场宽度与主力资金</div><div class="card-sub" id="ov-turnover">--</div></div>
        <div class="grid g2" style="align-items:center">
          <div>
            <div style="height:52px" id="ov-breadth"></div>
            <div class="grid g3" style="margin-top:10px;text-align:center">
              <div><div class="up num" style="font-size:18px;font-weight:750" id="ov-up">--</div><div class="stat-label">上涨</div></div>
              <div><div class="num flat" style="font-size:18px;font-weight:750" id="ov-flat">--</div><div class="stat-label">平盘</div></div>
              <div><div class="down num" style="font-size:18px;font-weight:750" id="ov-down">--</div><div class="stat-label">下跌</div></div>
            </div>
          </div>
          <div>
            <div style="font-size:11.5px;color:var(--text-3);margin-bottom:2px">两市主力净流入（近30日 / 亿）</div>
            <div class="chart h220" id="ov-flow"></div>
          </div>
        </div>
      </div>

      <div class="card span-5">
        <div class="card-head"><div class="card-title">风险与信号</div><div class="card-sub" id="ov-flags-n">--</div></div>
        <div id="ov-flags"></div>
        <div style="margin-top:12px;padding-top:10px;border-top:1px solid var(--line)">
          <div class="card-sub" style="margin-bottom:6px">今日异动 · 盘中自动播报</div>
          <div id="ov-moves"></div>
        </div>
      </div>
    </div>

    <div class="grid g12" style="margin-top:14px">
      <div class="card span-6">
        <div class="card-head"><div class="card-title">今日涨幅榜</div><div class="card-sub" id="ov-rank-sub">全A · 实时</div></div>
        <div class="table-scroll" style="max-height:400px"><table class="tbl">
          <thead><tr><th>#</th><th>名称</th><th class="r">现价</th><th class="r">涨跌幅</th><th class="r">换手率</th><th class="r">主力净流入</th></tr></thead>
          <tbody id="ov-rank"></tbody>
        </table></div>
      </div>
      <div class="card span-6">
        <div class="card-head"><div class="card-title">板块雷达</div>
          <div class="tabs" id="ov-sector-tabs">
            <button type="button" class="tab active" data-tab="up">涨幅</button>
            <button type="button" class="tab" data-tab="flow">资金流</button>
          </div>
        </div>
        <div class="chart h220" id="ov-sectors"></div>
        <div style="margin-top:8px;font-size:11.5px;color:var(--text-3)">
          <b style="color:var(--text-2)">主线题材</b>（来自涨停池行业聚合）：
          <span id="ov-themes" style="color:var(--amber)">--</span>
        </div>
      </div>
    </div>
  `;

  // 指数卡点击 → 行情页
  container.querySelector('#ov-indices').addEventListener('click', e => {
    const card = e.target.closest('.idx-card');
    if (card) {
      document.querySelector('.nav-item[data-page="market"]')?.click();
      document.dispatchEvent(new CustomEvent('open-quote', { detail: { code: card.dataset.code, name: card.dataset.name } }));
    }
  });
  container.querySelector('#ov-rank').addEventListener('click', e => {
    const tr = e.target.closest('tr');
    if (tr && tr.dataset.code) {
      document.querySelector('.nav-item[data-page="market"]')?.click();
      document.dispatchEvent(new CustomEvent('open-quote', { detail: { code: tr.dataset.code, name: tr.dataset.name } }));
    }
  });
  container.querySelector('#ov-rank').addEventListener('keydown', e => {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    const tr = e.target.closest('tr[data-code]');
    if (tr) { e.preventDefault(); tr.click(); }
  });

  // 板块雷达：涨幅 / 资金流 切换
  container.querySelector('#ov-sector-tabs').addEventListener('click', e => {
    const t = e.target.closest('.tab');
    if (!t) return;
    container.querySelectorAll('#ov-sector-tabs .tab').forEach(x => x.classList.remove('active'));
    t.classList.add('active');
    sectorTab = t.dataset.tab || 'up';
    renderSectors(container);
  });

  container.querySelector('#ov-ask-harness').addEventListener('click', () => {
    const brief = currentBrief || buildProactiveBrief({
      emotion: state.emotion, indices: state.indices, watchlist: loadWatch(), alerts: loadAlerts(),
      journal: loadJournal(), marketState: tradingState().state, asOf: state.lastUpdate,
    });
    document.dispatchEvent(new CustomEvent('ask-proactive-brief', { detail: { brief } }));
  });
  container.querySelector('#ov-open-assistant').addEventListener('click', () => {
    document.dispatchEvent(new CustomEvent('open-assistant'));
  });
  container.querySelector('#ov-proactive-refresh').addEventListener('click', () => {
    document.dispatchEvent(new CustomEvent('refresh-all'));
  });
  container.querySelector('#ov-proactive-handle').addEventListener('click', () => {
    if (currentBrief) setBriefRead(currentBrief, !isBriefRead(currentBrief.id));
  });
  container.querySelector('#ov-proactive-more').addEventListener('click', () => {
    const card = container.querySelector('#ov-proactive');
    const expanded = card.classList.toggle('mobile-expanded');
    const count = Math.max(0, (currentBrief && currentBrief.actions.length || 0) - 1);
    container.querySelector('#ov-proactive-more').textContent = expanded ? '收起次要任务' : `查看另外 ${count} 项`;
  });
  const toggle = container.querySelector('#ov-proactive-toggle');
  const proactive = container.querySelector('#ov-proactive');
  const setCollapsed = collapsed => {
    proactive.classList.toggle('collapsed', collapsed);
    toggle.setAttribute('aria-expanded', String(!collapsed));
    toggle.textContent = collapsed ? '展开' : '收起';
    localStorage.setItem('dp_proactive_collapsed_v1', collapsed ? '1' : '0');
  };
  setCollapsed(localStorage.getItem('dp_proactive_collapsed_v1') === '1');
  toggle.addEventListener('click', () => setCollapsed(!proactive.classList.contains('collapsed')));
  container.querySelector('#ov-proactive-actions').addEventListener('click', e => {
    const button = e.target.closest('[data-brief-page]');
    if (!button) return;
    document.querySelector(`.nav-item[data-page="${button.dataset.briefPage}"]`)?.click();
  });
  const rerenderBrief = () => renderProactiveBrief(container, { emotion: state.emotion, indices: state.indices });
  bus.addEventListener('watch', rerenderBrief);
  bus.addEventListener('alerts', rerenderBrief);
  bus.addEventListener('journal', rerenderBrief);
  bus.addEventListener('brief-receipts', rerenderBrief);

  // 今日异动流（新涨停/炸板，app.js 差分后推送）
  const movesEl = container.querySelector('#ov-moves');
  const renderMoves = (list) => {
    if (!movesEl) return;
    if (!list || !list.length) {
      movesEl.innerHTML = '<div class="empty" style="padding:10px">交易时段出现新涨停 / 炸板时，会自动播报到这里</div>';
      return;
    }
    movesEl.innerHTML = list.slice(0, 8).map(m => `
      <div class="move-item">
        <span class="move-t num">${esc(m.t)}</span>
        <span class="move-text">${esc(m.text)}</span>
      </div>`).join('');
  };
  renderMoves([]);
  bus.addEventListener('moves', e => renderMoves(e.detail));
}

function renderProactiveBrief(container, data) {
  const card = container.querySelector('#ov-proactive');
  if (!card) return;
  const brief = buildProactiveBrief({
    emotion: data && data.emotion,
    indices: data && data.indices,
    watchlist: loadWatch(),
    alerts: loadAlerts(),
    journal: loadJournal(),
    marketState: tradingState().state,
    asOf: state.lastUpdate,
  });
  currentBrief = brief;
  const read = isBriefRead(brief.id);
  card.classList.toggle('read', read);
  card.dataset.tone = brief.tone;
  container.querySelector('#ov-proactive-period').textContent = `${brief.period}主动简报`;
  container.querySelector('#ov-proactive-status').textContent = read ? '已读' : brief.status;
  container.querySelector('#ov-proactive-title').textContent = brief.headline;
  container.querySelector('#ov-proactive-summary').textContent = brief.summary;
  container.querySelector('#ov-proactive-facts').innerHTML = brief.facts.length
    ? brief.facts.map(item => `<div class="proactive-fact"><span>${esc(item.label)}</span><b class="num">${esc(item.value)}</b></div>`).join('')
    : '<div class="proactive-fact"><span>状态</span><b>等待首轮数据</b></div>';
  container.querySelector('#ov-proactive-actions').innerHTML = brief.actions.map((item, index) => `
    <article class="proactive-action ${esc(item.tone)}">
      <div class="proactive-action-index">${index + 1}</div>
      <div class="proactive-action-copy"><b>${esc(item.title)}</b><span>${esc(item.detail)}</span></div>
      <button class="btn sm" data-brief-page="${esc(item.page)}">${esc(item.label)}</button>
    </article>`).join('');
  container.querySelector('#ov-proactive-evidence').innerHTML = brief.evidence
    .map(item => `<span>${esc(item)}</span>`).join('');
  const handleButton = container.querySelector('#ov-proactive-handle');
  const hasData = Boolean(brief.dataDate);
  handleButton.disabled = !hasData;
  handleButton.textContent = hasData ? (read ? '标记未读' : '标记已读') : '等待数据';
  handleButton.setAttribute('aria-pressed', String(read));
  container.querySelector('#ov-ask-harness').disabled = !hasData;
  const more = container.querySelector('#ov-proactive-more');
  const hiddenActions = Math.max(0, brief.actions.length - 1);
  more.hidden = hiddenActions === 0;
  more.textContent = `查看另外 ${hiddenActions} 项`;
  card.classList.remove('mobile-expanded');
}

function renderIndices(el, indices) {
  el.innerHTML = (indices || []).map((ix, i) => {
    if (!ix || ix.error) return '';
    const cls = pctClass(ix.pct);
    return `<button type="button" class="card idx-card" data-code="${esc(ix.code)}" data-name="${esc(ix.name)}" aria-label="查看${esc(ix.name)}行情">
      <div class="idx-name">${esc(ix.name)}</div>
      <div class="idx-price num ${cls}">${fmtPrice(ix.price)}</div>
      <div class="idx-row"><span class="num ${cls}">${fmtPct(ix.pct)}</span><span class="num ${cls}">${ix.chg > 0 ? '+' : ''}${fmtPrice(ix.chg)}</span></div>
      <div class="spark" id="ov-spark-${i}"></div>
    </button>`;
  }).join('');
}

export async function refresh(container, data) {
  init(container);
  const safeData = data || {};
  const em = safeData.emotion;
  renderProactiveBrief(container, safeData);
  if (!em) return;
  const engine = em.engine || {};
  const raw = engine.raw || {};

  // ---- 指数卡
  const idxEl = container.querySelector('#ov-indices');
  const idxData = data.indices && data.indices.length ? data.indices : (em.indices || []);
  if (idxEl) renderIndices(idxEl, idxData);
  // 迷你K线（5分钟刷新一次）
  if (Date.now() - sparksAt > 300000) {
    sparksAt = Date.now();
    INDEX_CODES.forEach((ix, i) => {
      api.kline(ix.code, 101, 1, 30).then(k => {
        const el = container.querySelector('#ov-spark-' + i);
        const color = (idxData[i] && idxData[i].pct >= 0) ? UP : DOWN;
        if (el) sparkChart(el, k.rows, color);
      }).catch(() => {});
    });
  }

  // ---- 温度计
  container.querySelector('#ov-temp-date').textContent = em.date || '--';
  const tempEl = container.querySelector('#ov-temp');
  tempEl.innerHTML = (engine.temp ?? '--') + '<span class="temp-unit">°</span>';
  const tc = PHASE_COLORS[engine.color] || '#e9eef8';
  tempEl.style.color = tc;
  const gaugeEl = container.querySelector('#ov-gauge');
  if (gaugeEl) gaugeChart(gaugeEl, engine.temp);
  container.querySelector('#ov-phase').innerHTML =
    `<span class="badge lg ${esc(engine.color || 'gray')}">${esc(engine.phase || '--')}</span>`;
  const trendEl = container.querySelector('#ov-trend');
  const dynamics = engine.dynamics || {};
  if (dynamics.delta1 != null) {
    const d = dynamics.delta1;
    trendEl.innerHTML = `${esc(dynamics.direction || '变化')} <b class="${d > 0 ? 'up' : d < 0 ? 'down' : 'flat'}">${dynamics.arrow || '→'} ${Math.abs(d)}°</b> · 覆盖率 ${engine.coverage ?? 0}% · 数据质量分 ${engine.confidence ?? 0}`;
  } else {
    trendEl.textContent = '历史温度将随每个交易日收盘自动累积';
  }

  // ---- 核心指标
  const statsEl = container.querySelector('#ov-stats');
  const S = [
    ['涨停家数', raw.zt, '', raw.zt >= 60 ? 'up' : raw.zt >= 30 ? 'flat' : 'down'],
    ['跌停家数', raw.dt, '', raw.dt <= 5 ? 'down' : raw.dt <= 15 ? 'flat' : 'up'],
    ['炸板率', raw.zb_rate != null ? (raw.zb_rate * 100).toFixed(0) + '%' : '--', '', raw.zb_rate < 0.25 ? 'down' : raw.zb_rate < 0.35 ? 'flat' : 'up'],
    ['最高连板', raw.height, '板', raw.height >= 4 ? 'up' : 'flat'],
    ['连板家数', raw.lb_count, '家', raw.lb_count >= 8 ? 'up' : 'flat'],
    ['昨日涨停指数', raw.zt_idx_pct != null ? fmtPct(raw.zt_idx_pct) : '--', '', (raw.zt_idx_pct ?? 0) >= 0 ? 'up' : 'down'],
    ['昨日连板指数', raw.lb_idx_pct != null ? fmtPct(raw.lb_idx_pct) : '--', '', (raw.lb_idx_pct ?? 0) >= 0 ? 'up' : 'down'],
    ['上涨占比', raw.up_ratio != null ? (raw.up_ratio * 100).toFixed(1) + '%' : '--', '', raw.up_ratio >= 0.6 ? 'up' : raw.up_ratio >= 0.4 ? 'flat' : 'down'],
    ['数据质量分', engine.confidence != null ? engine.confidence : '--', '', engine.confidence >= 80 ? 'down' : engine.confidence >= 60 ? 'flat' : 'up'],
    ['昨日涨停均涨', '<span id="ov-prem-avg" style="color:var(--flat)">--</span>', '', 'flat'],
  ];
  statsEl.innerHTML = S.map(([label, v, unit, cls]) => `
    <div class="stat" style="padding:9px 10px;background:var(--panel-2);border:1px solid var(--line);border-radius:10px">
      <div class="stat-label">${label}</div>
      <div class="num ${cls}" style="font-size:19px;font-weight:750">${v ?? '--'}${v != null && unit ? `<span style="font-size:11px;color:var(--text-3)">${unit}</span>` : ''}</div>
    </div>`).join('');

  // ---- 作战指令
  const adv = engine.advice || {};
  container.querySelector('#ov-position').textContent = adv.position || '--';
  const styleEl = container.querySelector('#ov-style');
  styleEl.innerHTML = `<span class="badge ${esc(engine.color || 'gray')}">${esc(adv.style || '--')}</span>`;
  container.querySelector('#ov-advice-desc').textContent = adv.plan || '--';
  container.querySelector('#ov-narrative').textContent = engine.narrative || '--';

  // ---- 宽度与资金
  const b = em.breadth || {};
  container.querySelector('#ov-turnover').textContent =
    raw.turnover_yi ? `两市成交 ${fmtBig(raw.turnover_yi * 1e8)}` : '';
  breadthChart(container.querySelector('#ov-breadth'), b.up || 0, b.flat || 0, b.down || 0);
  container.querySelector('#ov-up').textContent = b.up ?? '--';
  container.querySelector('#ov-flat').textContent = b.flat ?? '--';
  container.querySelector('#ov-down').textContent = b.down ?? '--';
  const flows = em.flows || {};
  if (flows.sh && flows.sz) {
    const rows = flows.sh.map((r, i) => ({
      date: r.date, main: r.main + (flows.sz[i] ? flows.sz[i].main : 0),
    }));
    flowChart(container.querySelector('#ov-flow'), rows.slice(-22));
  } else {
    container.querySelector('#ov-flow').innerHTML = '<div class="empty">主力资金数据暂不可用</div>';
  }

  // ---- 风险信号
  const flags = [...new Map((engine.flags || []).map(flag => [`${flag.type}|${flag.text}`, flag])).values()];
  // 收盘复盘提醒（快照已记录但今日还没写复盘）
  const closedNow = tradingState().state !== 'open';
  const hasTodaySnap = (em.history || []).some(s => s.date === em.date);
  const hasTodayJournal = loadJournal().some(j => j.date === em.date);
  if (closedNow && hasTodaySnap && !hasTodayJournal && !flags.some(flag => flag.text.includes('复盘还没写'))) {
    flags.push({ type: 'info', text: '📝 今日已收盘、情绪快照已记录，但复盘还没写——去策略页一键生成' });
  }
  container.querySelector('#ov-flags-n').textContent = flags.length ? flags.length + ' 条' : '';
  const warns = flags.filter(f => f.type === 'warn' || f.type === 'risk');
  const others = flags.filter(f => f.type !== 'warn' && f.type !== 'risk');
  const flagHtml = (list) => list.map(f => `<div class="flag ${esc(f.type)}"><span class="f-dot"></span><span>${esc(f.text)}</span></div>`).join('');
  container.querySelector('#ov-flags').innerHTML =
    (warns.length ? `<div style="font-size:11px;color:var(--text-3);margin-bottom:6px">风险预警</div>` + flagHtml(warns) : '') +
    (others.length ? `<div style="font-size:11px;color:var(--text-3);margin:8px 0 6px">信号观察</div>` + flagHtml(others) : '') ||
    '<div class="empty">今日无异常信号，按计划执行</div>';
}

/* ---- 板块雷达（涨幅/资金流） ---- */
async function renderSectors(container) {
  const el = container.querySelector('#ov-sectors');
  if (!el) return;
  if (sectorTab === 'flow') {
    const f = await api.sectorsFlow();
    const items = [
      ...(f.inflow || []).map(s => ({ name: s.name, v: s.flow_yi })),
      ...(f.outflow || []).map(s => ({ name: s.name, v: s.flow_yi })),
    ];
    if (items.length) {
      hbarChart(el,
        items.map(s => s.name),
        items.map(s => s.v),
        (label, v) => (v >= 0 ? UP : DOWN));
    } else {
      el.innerHTML = '<div class="empty">板块资金数据暂不可用</div>';
    }
  } else {
    const sectors = await api.sectors();
    if (sectors.length) {
      hbarChart(el,
        sectors.slice(0, 10).map(s => s.name),
        sectors.slice(0, 10).map(s => +s.pct.toFixed(2)),
        (label, v) => (v >= 0 ? UP : DOWN));
    }
  }
}

/* ---- 涨幅榜 & 行业（低频数据） ---- */
let premAtOv = 0;
export async function refreshSecondary(container) {
  if (!built) return;
  // 打板溢价均涨（低频）
  if (Date.now() - premAtOv > 120000) {
    premAtOv = Date.now();
    api.premium().then(p => {
      const el = container.querySelector('#ov-prem-avg');
      if (el && p.stats && p.stats.avg_pct != null) {
        el.textContent = (p.stats.avg_pct > 0 ? '+' : '') + p.stats.avg_pct + '%';
        el.className = '';
        el.style.color = p.stats.avg_pct >= 0 ? 'var(--up)' : 'var(--down)';
        const tile = el.closest('.stat');
        if (tile) tile.querySelector('.stat-label').title = `基准日 ${p.prev_date} 的涨停股今日平均表现`;
      }
    }).catch(() => {});
  }
  try {
    const rank = await api.rank('up');
    const tb = container.querySelector('#ov-rank');
    tb.innerHTML = rank.slice(0, 10).map((r, i) => `
      <tr data-code="${esc(r.code)}" data-name="${esc(r.name)}" tabindex="0" role="link" aria-label="查看${esc(r.name)}行情" style="cursor:pointer">
        <td class="c" style="color:var(--text-3)">${i + 1}</td>
        <td><div class="name-cell"><b>${esc(r.name)}</b><span class="code-sub">${r.code}</span></div></td>
        <td class="r num">${fmtPrice(r.price)}</td>
        <td class="r num up" style="font-weight:650">${fmtPct(r.pct)}</td>
        <td class="r num">${(r.turnover ?? 0).toFixed(2)}%</td>
        <td class="r num ${r.main_flow >= 0 ? 'up' : 'down'}">${fmtBig(r.main_flow)}</td>
      </tr>`).join('');
  } catch { /* 静默 */ }

  try {
    await renderSectors(container);
  } catch { /* 静默 */ }

  try {
    const pool = (await api.ladder('ZT')).pool || [];
    const agg = {};
    pool.forEach(it => { const k = it.hybk || '其他'; agg[k] = (agg[k] || 0) + 1; });
    const top = Object.entries(agg).sort((a, b) => b[1] - a[1]).slice(0, 4)
      .map(([k, v]) => `${k} ×${v}`);
    container.querySelector('#ov-themes').textContent = top.length ? top.join('　') : '今日暂无涨停';
  } catch { /* 静默 */ }
}
