/* 深脉 DeepPulse — 涨停梯队页 */

import { api } from '../api.js?v=1.6.0';
import { hbarChart } from '../charts.js?v=1.6.0';
import { esc, fmtSeal, fmtPrice, fmtBig, fmtPct, pctClass } from '../util.js?v=1.6.0';

let built = false;
let mode = 'ZT';
let cycleAt = 0;

const MODES = [
  { key: 'ZT', label: '涨停梯队', empty: '今日无涨停' },
  { key: 'DT', label: '跌停池', empty: '今日无跌停' },
  { key: 'ZB', label: '炸板池', empty: '今日无炸板' },
  { key: 'DRAGON', label: '龙虎榜', empty: '今日无龙虎榜数据' },
];

export function init(container) {
  if (built) return;
  built = true;
  container.innerHTML = `
    <div class="card">
      <div class="card-head">
        <div class="tabs" id="ld-tabs">
          ${MODES.map(m => `<button type="button" class="tab ${m.key === 'ZT' ? 'active' : ''}" data-mode="${m.key}">${m.label}</button>`).join('')}
        </div>
        <div id="ld-emotion-context"></div>
      </div>
      <div id="ld-body"></div>
    </div>

    <div class="grid g12" style="margin-top:14px">
      <div class="card span-6" id="ld-sectors-card">
        <div class="card-head"><div class="card-title">涨停题材热度 TOP10</div><div class="card-sub">按行业聚合 · 识别主线</div></div>
        <div class="chart h280" id="ld-sectors"></div>
      </div>
      <div class="card span-6">
        <div class="card-head"><div class="card-title">梯队情绪解读</div></div>
        <div id="ld-insight" class="about-copy" style="font-size:12.5px"></div>
      </div>

      <div class="card span-12" style="margin-top:14px">
        <div class="card-head"><div class="card-title">题材周期跟踪</div><div class="card-sub" id="ld-cycle-sub">近5个交易日 · 主线连续性</div></div>
        <div id="ld-cycle"></div>
      </div>
    </div>
  `;

  container.querySelector('#ld-tabs').addEventListener('click', e => {
    const t = e.target.closest('.tab');
    if (!t) return;
    mode = t.dataset.mode;
    container.querySelectorAll('#ld-tabs .tab').forEach(x => x.classList.remove('active'));
    t.classList.add('active');
    refresh(container, null);
  });

  container.querySelector('#ld-body').addEventListener('click', e => {
    const toggle = e.target.closest('[data-expand-first]');
    if (toggle) {
      const row = toggle.closest('.first-board');
      const expanded = row.classList.toggle('expanded');
      toggle.setAttribute('aria-expanded', String(expanded));
      toggle.textContent = expanded ? '收起首板' : `展开全部 ${toggle.dataset.total} 只首板`;
      return;
    }
    const chip = e.target.closest('.chip');
    if (chip) {
      document.querySelector('.nav-item[data-page="market"]').click();
      document.dispatchEvent(new CustomEvent('open-quote', {
        detail: { code: chip.dataset.code, name: chip.dataset.name },
      }));
    }
    const row = e.target.closest('.ld-row');
    if (row) {
      if (mode === 'DRAGON') {
        // 龙虎榜：打开席位明细弹层
        openSeats(container, row.dataset.code, row.dataset.name);
      } else {
        document.querySelector('.nav-item[data-page="market"]').click();
        document.dispatchEvent(new CustomEvent('open-quote', {
          detail: { code: row.dataset.code, name: row.dataset.name },
        }));
      }
    }
  });
  container.querySelector('#ld-body').addEventListener('keydown', e => {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    const row = e.target.closest('.ld-row');
    if (row) { e.preventDefault(); row.click(); }
  });
}

/** 席位明细弹层（游资是谁在买 + 3日胜率） */
async function openSeats(container, code, name) {
  let modal = container.querySelector('#seats-modal');
  if (!modal) {
    modal = document.createElement('div');
    modal.id = 'seats-modal';
    modal.className = 'seats-modal';
    modal.innerHTML = `
      <div class="seats-panel">
        <div class="seats-head">
          <b id="seats-title">--</b>
          <button class="seats-close">✕</button>
        </div>
        <div class="seats-body" id="seats-body"></div>
      </div>`;
    container.appendChild(modal);
    modal.addEventListener('click', e => {
      if (e.target === modal || e.target.closest('.seats-close')) modal.style.display = 'none';
    });
    window.addEventListener('keydown', e => { if (e.key === 'Escape') modal.style.display = 'none'; });
  }
  modal.style.display = 'grid';
  container.querySelector('#seats-title').textContent = `${name || code}（${code}）· 龙虎榜席位`;
  const body = container.querySelector('#seats-body');
  body.innerHTML = '<div class="skeleton" style="height:220px"></div>';
  try {
    const res = await api.dragonSeats(code);
    const seatTable = (rows, title) => `
      <div class="seats-sec">
        <div class="seats-sec-title">${title}</div>
        <table class="tbl">
          <thead><tr><th>席位</th><th class="r">买入(亿)</th><th class="r">卖出(亿)</th><th class="r">净买(亿)</th><th class="r">3日胜率</th></tr></thead>
          <tbody>${rows.map(r => `
            <tr>
              <td style="font-size:11.5px;color:var(--text-2);white-space:normal;line-height:1.5">${esc(r.dept)}</td>
              <td class="r num up">${r.buy}</td>
              <td class="r num down">${r.sell}</td>
              <td class="r num ${r.net >= 0 ? 'up' : 'down'}" style="font-weight:700">${r.net > 0 ? '+' : ''}${r.net}</td>
              <td class="r num">${r.win3 != null ? r.win3.toFixed(1) + '%' : '--'}${r.times3 ? `<span style="color:var(--text-3);font-size:10px"> · ${r.times3}次</span>` : ''}</td>
            </tr>`).join('')}</tbody>
        </table>
      </div>`;
    body.innerHTML = seatTable(res.buy || [], '💰 买入席位 TOP') + seatTable(res.sell || [], '📤 卖出席位 TOP');
    if (!(res.buy || []).length && !(res.sell || []).length) {
      body.innerHTML = '<div class="empty">该股当日无席位明细</div>';
    }
  } catch (e) {
    body.innerHTML = `<div class="empty">席位明细加载失败：${esc(e.message)}</div>`;
  }
}

function ladderHTML(pool) {
  if (!pool || !pool.length) return '<div class="empty">暂无数据</div>';
  const groups = {};
  pool.forEach(it => {
    const k = Math.max(1, it.lbc || 1);
    (groups[k] = groups[k] || []).push(it);
  });
  const keys = Object.keys(groups).map(Number).sort((a, b) => b - a);
  return `<div class="ladder">` + keys.map(k => {
    const list = groups[k].sort((a, b) => b.fund - a.fund);
    const cls = k >= 6 ? 'h6' : k >= 4 ? 'h5' : k >= 3 ? 'h4' : '';
    const visible = k === 1 ? list.slice(0, 15) : list;
    const hidden = k === 1 ? list.slice(15) : [];
    const chipHTML = (it, extra = false) => {
      const isZhazha = k >= 3 && it.zbc > 0;
      return `<button type="button" class="chip ${isZhazha ? 'zha' : ''} ${extra ? 'first-extra' : ''}" data-code="${esc(it.code)}" data-name="${esc(it.name)}" aria-label="查看${esc(it.name)}行情">
        <span class="ch-name">${esc(it.name)} <span class="code-sub">${esc(it.code)}</span></span>
        <span class="ch-row">
          <span class="num ${it.pct >= 0 ? 'up' : 'down'}">${it.pct >= 0 ? '+' : ''}${it.pct.toFixed(2)}%</span>
          <span>首封 ${fmtSeal(it.fbt)}</span>
          <span>封单 ${fmtBig(it.fund)}</span>
          <span>换手 ${it.turnover.toFixed(2)}%</span>
          ${isZhazha ? `<span style="color:var(--amber)">炸${it.zbc}</span>` : ''}
          <span style="color:var(--text-3)">${esc(it.hybk)}</span>
        </span>
      </button>`;
    };
    return `<div class="ladder-row ${k === 1 ? 'first-board' : ''}">
      <div class="ladder-label ${cls}">
        <div class="lb-n num">${k}</div>
        <div class="lb-t">${k === 1 ? '首板' : '连板'}</div>
        <div class="lb-t">${list.length} 只</div>
      </div>
      <div class="ladder-chips">${visible.map(it => chipHTML(it)).join('')}${hidden.map(it => chipHTML(it, true)).join('')}
        ${hidden.length ? `<button type="button" class="btn sm ghost first-board-toggle" data-expand-first data-total="${list.length}" aria-expanded="false">展开全部 ${list.length} 只首板</button>` : ''}
      </div></div>`;
  }).join('') + '</div>';
}

/** 龙虎榜：净买额榜（游资动向） */
function dragonHTML(res) {
  if (!res || !res.list || !res.list.length) return '<div class="empty">暂无龙虎榜数据（休市日显示最近交易日）</div>';
  const s = res.stats || {};
  const chips = [
    ['上榜家数', s.count ?? '--', 'flat'],
    ['总净买额', s.total_net != null ? (s.total_net > 0 ? '+' : '') + s.total_net + '亿' : '--', (s.total_net ?? 0) >= 0 ? 'up' : 'down'],
    ['净买榜首', s.top_net || '--', 'up'],
  ];
  return `
    <div class="prem-stats" style="grid-template-columns:repeat(3,1fr);margin-bottom:12px">
      ${chips.map(([label, v, cls]) => `
        <div class="prem-chip"><div class="pc-label">${label}</div><div class="pc-value num ${cls}">${v}</div></div>`).join('')}
    </div>
    <div style="font-size:11px;color:var(--text-3);margin-bottom:8px">
      数据日期 ${esc(res.date)} · 按净买额排序 · 点击查看个股
    </div>
    <div class="table-scroll"><table class="tbl">
      <thead><tr>
        <th>名称</th><th class="r">涨跌幅</th><th class="r">净买额(亿)</th><th class="r">买入(亿)</th><th class="r">卖出(亿)</th><th class="r">成交额(亿)</th><th class="r">换手率</th><th>上榜原因</th>
      </tr></thead>
      <tbody>` + res.list.map(it => `
        <tr style="cursor:pointer" class="ld-row" tabindex="0" role="link" aria-label="查看${esc(it.name)}详情" data-code="${esc(it.code)}" data-name="${esc(it.name)}">
          <td><div class="name-cell"><b>${esc(it.name)}</b><span class="code-sub">${esc(it.code)}</span></div></td>
          <td class="r num ${pctClass(it.pct)}" style="font-weight:650">${fmtPct(it.pct)}</td>
          <td class="r num ${it.net >= 0 ? 'up' : 'down'}" style="font-weight:700">${it.net > 0 ? '+' : ''}${it.net}</td>
          <td class="r num">${it.buy}</td>
          <td class="r num">${it.sell}</td>
          <td class="r num">${it.amount}</td>
          <td class="r num">${it.turnover}%</td>
          <td style="color:var(--text-2);font-size:11.5px;white-space:normal;min-width:200px;line-height:1.5">${esc(it.reason || '--')}</td>
        </tr>`).join('') + '</tbody></table></div>';
}

function poolTable(pool) {  if (!pool || !pool.length) return '<div class="empty">暂无数据</div>';
  return `<div class="table-scroll"><table class="tbl">
    <thead><tr><th>名称</th><th class="r">现价</th><th class="r">涨跌幅</th><th class="r">换手率</th><th class="r">封单/成交额</th><th class="c">封板时间</th><th class="c">炸板次数</th><th>行业</th></tr></thead>
    <tbody>` + pool.map(it => `
      <tr style="cursor:pointer" class="ld-row" tabindex="0" role="link" aria-label="查看${esc(it.name)}行情" data-code="${esc(it.code)}" data-name="${esc(it.name)}">
        <td><div class="name-cell"><b>${esc(it.name)}</b><span class="code-sub">${esc(it.code)}</span></div></td>
        <td class="r num">${fmtPrice(it.price)}</td>
        <td class="r num ${pctClass(it.pct)}" style="font-weight:650">${fmtPct(it.pct)}</td>
        <td class="r num">${it.turnover}%</td>
        <td class="r num">${fmtBig(it.fund || it.amount)}</td>
        <td class="c num">${fmtSeal(it.fbt || it.lbt)}</td>
        <td class="c num">${it.zbc || 0}</td>
        <td style="color:var(--text-2)">${esc(it.hybk || '--')}</td>
      </tr>`).join('') + '</tbody></table></div>';
}

export async function refresh(container, data) {
  init(container);
  const body = container.querySelector('#ld-body');
  body.innerHTML = '<div class="skeleton" style="height:220px"></div>';
  // 题材周期（低频：每 2 分钟）
  if (Date.now() - cycleAt > 120000) {
    cycleAt = Date.now();
    api.sectorCycle().then(c => renderCycle(container, c)).catch(() => {
      container.querySelector('#ld-cycle').innerHTML = '<div class="empty">题材周期数据暂不可用</div>';
    });
  }
  try {
    if (mode === 'DRAGON') {
      const res = await api.dragon();
      body.innerHTML = dragonHTML(res);
      return;
    }
    const res = await api.ladder(mode);
    const pool = res.pool || [];
    body.innerHTML = mode === 'ZT' ? ladderHTML(pool) : poolTable(pool);
    if (mode === 'ZT') {
      const agg = {};
      pool.forEach(it => { const k = it.hybk || '其他'; agg[k] = (agg[k] || 0) + 1; });
      const top = Object.entries(agg).sort((a, b) => b[1] - a[1]).slice(0, 10);
      if (top.length) {
        hbarChart(container.querySelector('#ld-sectors'),
          top.map(t => t[0]), top.map(t => t[1]),
          (l, v, i) => ['#f6465d', '#f0b90b', '#f0b90b', '#4f8cff', '#4f8cff'][Math.min(i, 4)]);
      }
      renderInsight(container, pool, res.total);
    }
  } catch (e) {
    body.innerHTML = `<div class="empty">加载失败：${esc(e.message)}</div>`;
  }

  // 情绪上下文
  try {
    const em = data && data.emotion ? data.emotion : await api.emotion();
    const en = em.engine || {};
    container.querySelector('#ld-emotion-context').innerHTML =
      `<span class="badge ${esc(en.color || 'gray')}">情绪 ${en.temp ?? '--'}° · ${esc(en.phase || '--')}</span>`;
  } catch { /* 静默 */ }
}

/** 题材周期：涨停家数序列 + 主线连续性 + 趋势状态 */
function renderCycle(container, c) {
  const el = container.querySelector('#ld-cycle');
  const sub = container.querySelector('#ld-cycle-sub');
  if (!el) return;
  if (!c || !c.sectors || !c.sectors.length) {
    sub.textContent = `${(c && c.dates && c.dates.length) || 0} 个真实交易日快照 · 不使用伪历史补齐`;
    el.innerHTML = `<div class="empty">${esc((c && c.message) || '暂无题材周期数据')}</div>`;
    return;
  }
  sub.textContent = `${(c.dates || []).length} 个真实交易日快照 · 柱高=当日该题材涨停家数`;
  const maxCount = Math.max(...c.sectors.flatMap(s => s.counts), 1);
  el.innerHTML = `<div class="cyc-table">` + c.sectors.map(s => {
    let badge;
    if (s.streak >= 3) badge = '<span class="badge red">主线 · 连续' + s.streak + '天</span>';
    else if (s.streak === 2) badge = '<span class="badge amber">发酵 · 连续2天</span>';
    else if (s.trend >= 3) badge = '<span class="badge cyan">启动 · 今日放量</span>';
    else if (s.trend < 0) badge = '<span class="badge gray">退潮</span>';
    else badge = '<span class="badge gray">观察</span>';
    const bars = s.counts.map(v => `
      <div class="cyc-bar-col" title="${v} 家">
        <div class="cyc-bar" style="height:${Math.max(8, Math.round(v / maxCount * 100))}%"></div>
        <span class="cyc-bar-v">${v}</span>
      </div>`).join('');
    return `<div class="cyc-row">
      <div class="cyc-head">
        <span class="cyc-name">${esc(s.name)}</span>
        <span class="cyc-trend ${s.trend >= 0 ? 'up' : 'down'}">${s.trend >= 0 ? '+' : ''}${s.trend}</span>
        ${badge}
      </div>
      <div class="cyc-bars">${bars}</div>
    </div>`;
  }).join('') + '</div>';
}

function renderInsight(container, pool, total) {
  const el = container.querySelector('#ld-insight');
  const heights = pool.map(it => it.lbc || 1);
  const maxH = heights.length ? Math.max(...heights) : 0;
  const lbCount = heights.filter(h => h >= 2).length;
  const zha = pool.filter(it => it.zbc > 0).length;
  const oneBoard = heights.filter(h => h === 1).length;
  let insight = '';
  if (maxH >= 5) insight += `最高 ${maxH} 连板，空间打开，超短赚钱效应具备高度，可以围绕最高板所属题材寻找补涨与中位接力。`;
  else if (maxH >= 3) insight += `最高 ${maxH} 连板，高度适中，处于周期发酵或分歧阶段，接力优先选主线核心而非跟风。`;
  else insight += `梯队无高度（最高 ${maxH} 板），情绪处于低位或退潮期，只适合首板试错与低吸，不宜追高。`;
  if (lbCount >= 10) insight += `连板 ${lbCount} 家，梯队厚实、集团作战，主线有宽度。`;
  else if (lbCount > 0) insight += `连板仅 ${lbCount} 家，梯队单薄，谨防独苗断板引发退潮。`;
  if (zha > 0) insight += `注意：${zha} 只梯队股曾开板回封（炸${'板'}），封板质量存疑，明日竞价分歧概率大。`;
  insight += `今日涨停合计 ${total} 家，首板 ${oneBoard} 家占多数时说明情绪以试错轮动为主，聚焦「昨日涨停指数」能否转正确认赚钱效应。`;
  el.innerHTML = insight;
}
