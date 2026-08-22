/* 深脉 DeepPulse — 情绪周期页 */

import { api } from '../api.js?v=1.27.0';
import { bus } from '../store.js?v=1.27.0';
import { tempHistoryChart, ztIdxChart, hbarChart, distChart, intradayChart } from '../charts.js?v=1.27.0';
import { esc, PHASE_COLORS, fmtPct, pctClass } from '../util.js?v=1.27.0';

let built = false;
let bkAt = 0;
let premAt = 0;
let intradayPoints = [];

const PHASES = [
  { name: '冰点期', color: 'blue', range: '0≤T<20', desc: '亏钱效应集中，重点验证恐慌是否收敛与回暖信号是否出现。' },
  { name: '修复期', color: 'cyan', range: '20≤T<40', desc: '情绪开始回暖，重点验证溢价、广度与核心标的反馈是否同步。' },
  { name: '发酵期', color: 'amber', range: '40≤T<60', desc: '赚钱效应扩散，重点验证主线梯队完整性与持续性。' },
  { name: '高潮期', color: 'red', range: '60≤T<80', desc: '普涨接近分歧，重点核对拥挤度、兑现压力与高位反馈。' },
  { name: '亢奋期', color: 'violet', range: '80≤T≤100', desc: '情绪过热，重点观察退潮迹象、风险扩散与新周期线索。' },
];

export function init(container) {
  if (built) return;
  built = true;
  container.innerHTML = `
    <div class="card">
      <div class="phase-strip" id="em-phase-strip">
        ${PHASES.map((p, i) => `
          <div class="phase-pill ${p.color}" data-idx="${i}" title="${esc(p.desc)}">
            ${p.name}<span class="pp-range">${p.range}</span>
          </div>`).join('')}
      </div>
      <div id="em-phase-desc" style="font-size:12.5px;color:var(--text-2);line-height:1.8;padding:2px 4px 6px">--</div>
    </div>

    <div class="emotion-state-grid" style="margin-top:14px">
      <div class="emotion-state"><span>情绪温度</span><b id="em-state-temp" class="num">--</b></div>
      <div class="emotion-state"><span>变化方向</span><b id="em-state-direction">--</b><small id="em-state-delta">--</small></div>
      <div class="emotion-state"><span>数据覆盖率</span><b id="em-state-coverage" class="num">--</b></div>
      <div class="emotion-state"><span>数据质量分</span><b id="em-state-confidence" class="num">--</b></div>
      <div class="emotion-state"><span>信号一致度</span><b id="em-state-consensus" class="num">--</b></div>
    </div>

    <div class="grid g12" style="margin-top:14px">
      <div class="card span-8">
        <div class="card-head"><div class="card-title">六维情绪结构</div><div class="card-sub">分值越高代表该维度反馈越健康；不以单项替代总判断</div></div>
        <div class="emotion-dim-grid" id="em-dimensions"></div>
      </div>
      <div class="card span-4">
        <div class="card-head"><div class="card-title">状态倾向</div><div class="card-sub">启发式 · 尚未做历史概率校准</div></div>
        <div class="transition-bars" id="em-transition"></div>
        <div id="em-divergences" class="emotion-divergences"></div>
      </div>
    </div>

    <div class="grid g12" style="margin-top:14px">
      <div class="card span-8">
        <div class="card-head"><div class="card-title">情绪温度历史</div><div class="card-sub">每个交易日收盘后自动记录 · 颜色=当日阶段</div></div>
        <div class="chart h260" id="em-temp-hist"></div>
        <div style="margin-top:8px;padding-top:8px;border-top:1px solid var(--line)">
          <div class="card-sub" style="margin-bottom:4px">今日盘中温度轨迹（交易时段 5 分钟一记）</div>
          <div class="chart h120" id="em-intraday"></div>
        </div>
      </div>
      <div class="card span-4">
        <div class="card-head"><div class="card-title">周期阶段手册</div></div>
        <div id="em-handbook">
          ${PHASES.map(p => `
            <div style="display:flex;gap:9px;align-items:flex-start;padding:7px 2px;border-bottom:1px solid rgba(148,163,184,.06)">
              <span class="badge ${p.color}" style="flex:0 0 auto">${p.name}</span>
              <span style="font-size:11.5px;color:var(--text-2);line-height:1.6">${esc(p.desc)}</span>
            </div>`).join('')}
        </div>
      </div>
    </div>

    <div class="grid g12" style="margin-top:14px">
      <div class="card span-6">
        <div class="card-head"><div class="card-title">昨日涨停 / 昨日连板指数</div><div class="card-sub">归一化走势 · 打板溢价的温度计</div></div>
        <div class="chart h260" id="em-bk"></div>
        <div style="margin-top:6px;font-size:11px;color:var(--text-3)">
          昨日涨停指数涨=打板次日有溢价（情绪好）；昨日连板指数涨=高位接力赚钱（情绪强）。二者转负是退潮的先行信号。
        </div>
      </div>
      <div class="card span-3">
        <div class="card-head"><div class="card-title">涨停梯队分布</div><div class="card-sub">今日</div></div>
        <div class="chart h260" id="em-ladder-dist"></div>
      </div>
      <div class="card span-3">
        <div class="card-head"><div class="card-title">全A涨跌分布</div><div class="card-sub">今日</div></div>
        <div class="chart h260" id="em-dist"></div>
      </div>
    </div>

    <div class="card" style="margin-top:14px">
      <div class="card-head"><div class="card-title">昨日涨停 · 今日表现（打板溢价）</div><div class="card-sub" id="em-prem-sub">--</div></div>
      <div class="prem-stats" id="em-prem-stats"></div>
      <div class="table-scroll" style="max-height:430px"><table class="tbl">
        <thead><tr>
          <th>名称</th><th class="r">今涨跌</th><th class="r">开盘溢价</th><th class="r">盘中最高</th><th class="c">今日</th><th>行业</th>
        </tr></thead>
        <tbody id="em-prem-body"></tbody>
      </table></div>
    </div>

    <div class="card" style="margin-top:14px">
      <div class="card-head"><div class="card-title">情绪评分明细</div><div class="card-sub">温度 = 50 + 2.5×Σ(得分×权重)/Σ权重 · <b style="color:var(--amber)">暖色=推高温度</b> / <b style="color:var(--accent)">冷色=压低温度</b></div></div>
      <div class="table-scroll" style="max-height:none"><table class="tbl">
        <thead><tr>
          <th>指标</th><th>今日数值</th><th class="c">得分贡献</th><th style="width:34%">评分分布（-20 ~ +20）</th><th class="r">权重</th><th>解读</th>
        </tr></thead>
        <tbody id="em-signals"></tbody>
      </table></div>
    </div>
  `;

  // 盘中温度轨迹（app.js 交易时段每 5 分钟推送）
  bus.addEventListener('intraday', e => {
    intradayPoints = e.detail || [];
    const el = container.querySelector('#em-intraday');
    if (el && intradayPoints.length >= 2) intradayChart(el, intradayPoints);
  });

  // 溢价表点击 → 行情页
  container.querySelector('#em-prem-body').addEventListener('click', e => {
    const tr = e.target.closest('tr');
    if (tr && tr.dataset.code) {
      document.querySelector('.nav-item[data-page="market"]').click();
      document.dispatchEvent(new CustomEvent('open-quote', { detail: { code: tr.dataset.code, name: tr.dataset.name } }));
    }
  });
  container.querySelector('#em-prem-body').addEventListener('keydown', e => {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    const tr = e.target.closest('tr[data-code]');
    if (tr) { e.preventDefault(); tr.click(); }
  });
}

export async function refresh(container, data) {
  init(container);
  const em = data.emotion;
  if (!em) return;
  // 打板溢价（低频 120s）
  if (Date.now() - premAt > 120000) {
    premAt = Date.now();
    api.premium().then(p => renderPremium(container, p)).catch(e => renderPremiumError(container, e));
  }
  const engine = em.engine || {};
  const raw = engine.raw || {};
  const dynamics = engine.dynamics || {};
  const transition = engine.transition || {};

  // 阶段条
  const strip = container.querySelector('#em-phase-strip');
  strip.querySelectorAll('.phase-pill').forEach(p => p.classList.remove('active'));
  const active = strip.querySelector(`.phase-pill[data-idx="${engine.phase_idx ?? 0}"]`);
  if (active) active.classList.add('active');
  container.querySelector('#em-phase-desc').innerHTML =
    `当前：<b style="color:${PHASE_COLORS[engine.color] || '#fff'}">${esc(engine.phase || '--')}</b>（温度 ${engine.temp ?? '--'}°）。${esc(engine.phase_desc || '')}` +
    `${engine.phase_pending ? ' <span class="badge cyan">切换确认中</span>' : ''}`;

  // 温度之外同时展示方向、覆盖率与可信度，避免把单一数字当成确定结论。
  container.querySelector('#em-state-temp').textContent = `${engine.temp ?? '--'}°`;
  container.querySelector('#em-state-temp').style.color = PHASE_COLORS[engine.color] || 'var(--text)';
  container.querySelector('#em-state-direction').textContent = `${dynamics.arrow || '·'} ${dynamics.direction || '待积累'}`;
  container.querySelector('#em-state-direction').className = dynamics.direction === '升温' ? 'up' : dynamics.direction === '降温' ? 'down' : 'flat';
  container.querySelector('#em-state-delta').textContent = dynamics.delta1 == null ? '等待历史快照' : `Δ1 ${dynamics.delta1 > 0 ? '+' : ''}${dynamics.delta1}°${dynamics.delta3 == null ? '' : ` · Δ3 ${dynamics.delta3 > 0 ? '+' : ''}${dynamics.delta3}°`}`;
  container.querySelector('#em-state-coverage').textContent = `${engine.coverage ?? 0}%`;
  container.querySelector('#em-state-confidence').textContent = `${engine.confidence ?? 0}`;
  container.querySelector('#em-state-consensus').textContent = `${engine.consensus ?? 0}%`;

  container.querySelector('#em-dimensions').innerHTML = (engine.dimensions || []).map(d => {
    const value = d.value == null ? 0 : d.value;
    const color = value >= 65 ? 'var(--up)' : value < 40 ? 'var(--down)' : 'var(--amber)';
    return `<div class="emotion-dim ${d.available ? '' : 'muted'}">
      <div class="emotion-dim-head"><span>${esc(d.name)}</span><b class="num" style="color:${color}">${d.value == null ? '--' : d.value}</b></div>
      <div class="emotion-dim-track"><i style="width:${value}%;background:${color}"></i></div>
      <small>指标覆盖 ${d.coverage ?? 0}%</small>
    </div>`;
  }).join('') || '<div class="empty">结构数据暂不可用</div>';

  const transitionItems = [
    ['升阶', transition.upgrade ?? 0, 'var(--up)'],
    ['维持', transition.stay ?? 0, 'var(--amber)'],
    ['降阶', transition.downgrade ?? 0, 'var(--down)'],
  ];
  container.querySelector('#em-transition').innerHTML = transitionItems.map(([name, value, color]) => `
    <div class="transition-row"><span>${name}</span><div><i style="width:${value}%;background:${color}"></i></div><b class="num">${value}%</b></div>`).join('');
  const divergences = engine.divergences || [];
  container.querySelector('#em-divergences').innerHTML = divergences.length
    ? `<div class="emotion-div-title">结构背离</div>${divergences.map(item => `<p>⚠ ${esc(item)}</p>`).join('')}`
    : '<p class="ok">当前未识别到显著结构背离</p>';

  // 温度历史
  const snaps = em.history || [];
  const histEl = container.querySelector('#em-temp-hist');
  if (snaps.length) tempHistoryChart(histEl, snaps);
  else histEl.innerHTML = '<div class="empty">首个交易日收盘后，这里将自动累积情绪温度曲线</div>';

  // 昨涨停/连板指数（低频）
  const bkEl = container.querySelector('#em-bk');
  if (Date.now() - bkAt > 300000) {
    bkAt = Date.now();
    try {
      const [zt, lb] = await Promise.all([api.kline('BK0815', 101, 1, 60), api.kline('BK0816', 101, 1, 60)]);
      if (zt.rows && zt.rows.length) {
        ztIdxChart(bkEl, [
          { name: '昨日涨停指数', rows: zt.rows, color: '#f0b90b' },
          { name: '昨日连板指数', rows: lb.rows, color: '#22d3ee' },
        ]);
      }
    } catch {
      bkEl.innerHTML = '<div class="empty">指数历史暂不可用（上游限流恢复后自动重试）</div>';
    }
  }

  // 涨停梯队分布
  const pool = (em.pools && em.pools.ZT && em.pools.ZT.pool) || [];
  const ladderEl = container.querySelector('#em-ladder-dist');
  const dist = {};
  pool.forEach(it => { const k = it.lbc >= 1 ? it.lbc : 1; dist[k] = (dist[k] || 0) + 1; });
  const keys = Object.keys(dist).map(Number).sort((a, b) => a - b);
  if (keys.length) {
    hbarChart(ladderEl,
      keys.map(k => k + '板'),
      keys.map(k => dist[k]),
      (l, v, i) => ['#4f8cff', '#22d3ee', '#f0b90b', '#f6465d', '#a855f7'][Math.min(i, 4)]);
  } else {
    ladderEl.innerHTML = '<div class="empty">今日涨停池暂无数据</div>';
  }

  // 涨跌分布
  const bins = (em.breadth && em.breadth.bins) || {};
  if (Object.keys(bins).length) distChart(container.querySelector('#em-dist'), bins);
  else container.querySelector('#em-dist').innerHTML = '<div class="empty">暂无数据</div>';

  // 评分明细
  const tb = container.querySelector('#em-signals');
  const MAX_ABS = 20;
  tb.innerHTML = (engine.signals || []).map(s => {
    const sc = s.avail ? s.score : null;
    let bar;
    if (sc === null) {
      bar = '<span style="color:var(--text-3);font-size:11px">数据暂缺</span>';
    } else {
      const pct = Math.min(100, Math.abs(sc) / MAX_ABS * 100);
      const left = sc >= 0;
      bar = `<div style="position:relative;height:8px;background:rgba(148,163,184,.1);border-radius:4px">
        <div style="position:absolute;top:0;bottom:0;left:50%;width:1px;background:rgba(148,163,184,.35)"></div>
        <div style="position:absolute;top:0;bottom:0;${left ? 'left:50%' : 'right:50%'};width:${pct / 2}%;border-radius:4px;background:${sc >= 0 ? 'linear-gradient(90deg,#f0b90b,#f6465d)' : 'linear-gradient(90deg,#4f8cff,#22d3ee)'}"></div>
      </div>`;
    }
    return `<tr>
      <td style="font-weight:600">${esc(s.name)}</td>
      <td class="num">${s.display}<span style="color:var(--text-3);font-size:10.5px">${esc(s.unit)}</span></td>
      <td class="c num" style="font-weight:700;color:${sc === null ? 'var(--text-3)' : sc >= 0 ? 'var(--amber)' : 'var(--accent)'}">${sc === null ? '--' : (sc > 0 ? '+' : '') + sc}</td>
      <td>${bar}</td>
      <td class="r num" style="color:var(--text-3)">${s.weight}</td>
      <td style="color:var(--text-2);white-space:normal;min-width:220px;font-size:11.5px;line-height:1.6">${esc(s.note)}</td>
    </tr>`;
  }).join('');

  // ---- 打板溢价（低频 120s） ----
  if (Date.now() - premAt > 120000) {
    premAt = Date.now();
    api.premium().then(p => renderPremium(container, p)).catch(e => renderPremiumError(container, e));
  }
  // ---- 盘中温度轨迹 ----
  const intraEl = container.querySelector('#em-intraday');
  if (intradayPoints.length >= 2) intradayChart(intraEl, intradayPoints);
  else intraEl.innerHTML = '<div class="empty" style="padding:14px">交易时段每 5 分钟记录一次，开盘后这里会长出今天的温度曲线</div>';
}

/** 昨日涨停 · 今日表现（打板溢价榜） */
function renderPremium(container, p) {
  const s = p.stats || {};
  container.querySelector('#em-prem-sub').textContent =
    `基准日 ${p.prev_date || '--'} → ${p.date || '--'} · ${s.count ?? 0} 只样本 · ${esc((p.source && p.source.name) || '市场源')}`;
  container.querySelector('#em-prem-stats').innerHTML = [
    ['平均涨幅', s.avg_pct != null ? fmtPct(s.avg_pct) : '--', (s.avg_pct ?? 0) >= 0 ? 'up' : 'down', ''],
    ['红盘率', s.up_ratio != null ? s.up_ratio + '%' : '--', (s.up_ratio ?? 0) >= 50 ? 'up' : 'down', ''],
    ['涨停晋级', s.limit_again ?? '--', 'flat', s.limit_again_ratio != null ? s.limit_again_ratio + '%' : ''],
    ['连板晋级率', s.lb_ratio != null ? s.lb_ratio + '%' : '--', (s.lb_ratio ?? 0) >= 30 ? 'up' : 'down', ''],
    ['大面数 ≤-5%', s.big_loss ?? '--', (s.big_loss ?? 99) <= 5 ? 'down' : 'up', ''],
    ['炸板数', s.zha_count ?? '--', (s.zha_count ?? 99) <= 5 ? 'down' : 'up', ''],
  ].map(([label, v, cls, note]) => `
    <div class="prem-chip">
      <div class="pc-label">${label}</div>
      <div class="pc-value num ${cls}">${v}</div>
      ${note ? `<div class="pc-note">${note}</div>` : ''}
    </div>`).join('');
  container.querySelector('#em-prem-body').innerHTML = (p.list || []).map(r => {
    let badge = '—';
    if (r.up_today && r.today_lbc >= 2) badge = `<span class="badge red">${r.today_lbc}连板</span>`;
    else if (r.up_today) badge = '<span class="badge red">涨停</span>';
    else if (r.zha_today) badge = '<span class="badge amber">炸板</span>';
    return `<tr style="cursor:pointer" tabindex="0" role="link" aria-label="查看${esc(r.name)}行情" data-code="${esc(r.code)}" data-name="${esc(r.name)}">
      <td><div class="name-cell"><b>${esc(r.name)}</b><span class="code-sub">${esc(r.code)}</span></div></td>
      <td class="r num ${pctClass(r.pct)}" style="font-weight:650">${fmtPct(r.pct)}</td>
      <td class="r num ${pctClass(r.open_pct)}">${r.open_pct != null ? fmtPct(r.open_pct) : '--'}</td>
      <td class="r num ${pctClass(r.high_pct)}">${r.high_pct != null ? fmtPct(r.high_pct) : '--'}</td>
      <td class="c">${badge}</td>
      <td style="color:var(--text-2)">${esc(r.hybk || '--')}</td>
    </tr>`;
  }).join('') || '<tr><td colspan="6"><div class="empty">暂无数据</div></td></tr>';
}

function renderPremiumError(container, error) {
  container.querySelector('#em-prem-sub').textContent = '数据可靠性校验未通过';
  container.querySelector('#em-prem-stats').innerHTML = '';
  container.querySelector('#em-prem-body').innerHTML = `<tr><td colspan="6"><div class="empty">打板溢价暂不可用：${esc(error && error.message || '上游数据异常')}。系统不会使用休市日或重复数据生成结论。</div></td></tr>`;
}
