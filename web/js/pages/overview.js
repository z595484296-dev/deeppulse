/* 深脉 DeepPulse — 总览页 */

import { api } from '../api.js?v=1.17.0';
import { state, bus, syncProfile } from '../store.js?v=1.17.0';
import { loadJournal, loadWatch, loadAlerts, isBriefRead, setBriefRead } from '../store.js?v=1.17.0';
import { gaugeChart, breadthChart, flowChart, sparkChart, hbarChart } from '../charts.js?v=1.17.0';
import { fmtPct, fmtPrice, fmtBig, pctClass, esc, UP, DOWN, FLAT, PHASE_COLORS, fmtSeal, tradingState, toast } from '../util.js?v=1.17.0';
import { buildProactiveBrief } from '../proactive.js?v=1.17.0';

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

    <section class="card routine-card" id="ov-routine" aria-labelledby="ov-routine-title">
      <div class="routine-main">
        <div class="routine-heading">
          <div><span class="routine-dot" aria-hidden="true"></span><b id="ov-routine-title">主动服务日程</b></div>
          <span class="routine-state" id="ov-routine-state">正在读取</span>
          <span class="routine-next" id="ov-routine-next">下一次服务时间待确认</span>
        </div>
        <div class="routine-options" role="group" aria-label="选择深脉主动服务时段">
          <label><input type="checkbox" data-routine-task="pre_market"><span><b>盘前准备</b><small>08:45 后整理观察清单</small></span></label>
          <label><input type="checkbox" data-routine-task="intraday"><span><b>盘中检查</b><small>只做一次结构检查</small></span></label>
          <label><input type="checkbox" data-routine-task="close_review"><span><b>收盘复盘</b><small>15:10 后生成复盘待办</small></span></label>
        </div>
      </div>
      <p class="routine-boundary">逐项授权，关闭网页后仍由本机服务执行；关闭本机服务即停止。按北京时间工作日窗口运行，每条提醒都会注明数据日，不会把旧数据冒充实时行情。</p>
    </section>

    <section class="card event-radar-card" id="ov-event-radar" aria-labelledby="ov-event-title">
      <div class="event-radar-head">
        <div>
          <div class="event-radar-kicker">事件影响雷达 <span class="tag" id="ov-event-state">未开启</span></div>
          <h2 id="ov-event-title">让深脉理解“这件事为什么与你有关”</h2>
          <p>宏观日历与市场事件 → 敏感行业 → 你的自选；每一步都展示来源、时间和规则依据。</p>
        </div>
        <button class="btn sm primary" id="ov-event-toggle">授权开启</button>
      </div>
      <div class="event-radar-consent" id="ov-event-consent">
        默认关闭。开启后，本机后台会访问 AKShare 宏观日历与市场快讯；关闭网页后仍可生成提醒，关闭本机服务即停止。不会连接交易账户或自动下单。
      </div>
      <div class="event-radar-controls" id="ov-event-controls" hidden>
        <label><input type="checkbox" data-event-scope="macro"> 宏观日历</label>
        <label><input type="checkbox" data-event-scope="market_news"> 市场快讯</label>
        <label><input type="checkbox" data-event-link="watchlist"> 关联我的自选</label>
        <select id="ov-event-delivery" aria-label="事件提醒方式">
          <option value="digest">重要事件进入摘要</option>
          <option value="center_only">只收入提醒中心</option>
        </select>
        <select id="ov-event-horizon" aria-label="研究假设观察窗口">
          <option value="1">假设窗口：1个工作日</option>
          <option value="3">假设窗口：3个工作日</option>
          <option value="5" selected>假设窗口：5个工作日</option>
          <option value="10">假设窗口：10个工作日</option>
          <option value="20">假设窗口：20个工作日</option>
        </select>
      </div>
      <div class="event-radar-summary" id="ov-event-summary" hidden></div>
      <div class="event-radar-list" id="ov-event-list">
        <div class="event-radar-empty">尚未授权，因此没有访问事件数据源。</div>
      </div>
      <div class="event-radar-boundary">事实层与规则层分开呈现 · 相关性不等于因果 · 质量分不代表预测准确率</div>
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
  bus.addEventListener('market-routine', e => renderRoutine(container, e.detail));
  bus.addEventListener('event-impact', e => renderEventImpact(container, e.detail));
  bus.addEventListener('research-hypotheses', e => {
    state.hypotheses = e.detail;
    renderEventImpact(container, state.eventImpact);
  });

  container.querySelector('#ov-routine').addEventListener('change', async e => {
    const input = e.target.closest('[data-routine-task]');
    if (!input) return;
    const inputs = [...container.querySelectorAll('[data-routine-task]')];
    const tasks = Object.fromEntries(inputs.map(node => [node.dataset.routineTask, node.checked]));
    inputs.forEach(node => { node.disabled = true; });
    try {
      const result = await api.saveRoutineConfig({ tasks });
      state.routine = result.routine;
      await syncProfile();
      renderRoutine(container, result.routine);
      toast(tasks[input.dataset.routineTask]
        ? `${input.parentElement.querySelector('b').textContent}已开启`
        : `${input.parentElement.querySelector('b').textContent}已关闭`);
    } catch {
      input.checked = !input.checked;
      toast('主动服务设置失败，请确认本机深脉服务正在运行', 'err');
      renderRoutine(container, state.routine);
    } finally {
      inputs.forEach(node => { node.disabled = false; });
    }
  });
  refreshRoutine(container);

  container.querySelector('#ov-event-toggle').addEventListener('click', async e => {
    const button = e.currentTarget;
    const current = state.eventImpact && state.eventImpact.config || {};
    const enabled = current.enabled === true;
    button.disabled = true;
    try {
      await api.saveEventServiceConfig({ ...current, enabled: !enabled });
      state.eventImpact = await api.eventImpact();
      renderEventImpact(container, state.eventImpact);
      await syncProfile();
      toast(enabled ? '事件影响雷达已关闭，不再进行新检查' : '事件影响雷达已开启，将从可核验事件开始学习', 'ok');
    } catch {
      toast('事件服务设置失败，请确认本机深脉服务正在运行', 'err');
    } finally {
      button.disabled = false;
    }
  });
  container.querySelector('#ov-event-controls').addEventListener('change', async () => {
    const current = state.eventImpact && state.eventImpact.config || {};
    const scopes = Object.fromEntries([...container.querySelectorAll('[data-event-scope]')]
      .map(input => [input.dataset.eventScope, input.checked]));
    const watchlistLink = container.querySelector('[data-event-link="watchlist"]').checked;
    const delivery = container.querySelector('#ov-event-delivery').value;
    localStorage.setItem('dp_event_horizon_v1', container.querySelector('#ov-event-horizon').value);
    try {
      await api.saveEventServiceConfig({ ...current, enabled: true, scopes, watchlist_link: watchlistLink, delivery });
      state.eventImpact = await api.eventImpact();
      renderEventImpact(container, state.eventImpact);
      await syncProfile();
    } catch { toast('事件雷达设置未保存，请稍后重试', 'err'); }
  });
  container.querySelector('#ov-event-list').addEventListener('click', e => {
    const askButton = e.target.closest('[data-event-ask]');
    const saveButton = e.target.closest('[data-event-save]');
    const eventId = askButton?.dataset.eventAsk || saveButton?.dataset.eventSave;
    if (!eventId) return;
    const item = ((state.eventImpact && state.eventImpact.impact && state.eventImpact.impact.items) || [])
      .find(row => row.event && row.event.id === eventId);
    if (askButton) {
      document.dispatchEvent(new CustomEvent('ask-event-impact', { detail: { item } }));
      return;
    }
    saveButton.disabled = true;
    const horizonDays = Number(container.querySelector('#ov-event-horizon').value || 5);
    api.mutateResearchHypothesis('create', { eventItem: item, horizonDays }).then(result => {
      state.hypotheses = result.hypotheses;
      bus.dispatchEvent(new CustomEvent('research-hypotheses', { detail: result.hypotheses }));
      syncProfile().catch(() => {});
      toast(result.created ? `已保存 ${horizonDays} 个工作日研究假设，到期会提醒复盘` : '这条事件已有观察中的研究假设');
    }).catch(error => {
      saveButton.disabled = false;
      toast('保存研究假设失败：' + error.message, 'err');
    });
  });
  renderEventImpact(container, state.eventImpact);

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

const EVENT_STATE_LABELS = {
  disabled: '未开启', starting: '启动中', ok: '已连接', degraded: '部分降级',
  unavailable: '暂无可用来源', error: '需要检查', stopped: '已停止',
};

function eventTime(value) {
  if (!value) return '时点待确认';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).slice(0, 16);
  return date.toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false });
}

function renderEventImpact(container, snapshot) {
  const root = container.querySelector('#ov-event-radar');
  if (!root) return;
  const value = snapshot || {};
  const config = value.config || {};
  const enabled = config.enabled === true;
  const runtimeState = value.state || value.runtime && value.runtime.state || (enabled ? 'starting' : 'disabled');
  root.dataset.state = runtimeState;
  root.querySelector('#ov-event-state').textContent = EVENT_STATE_LABELS[runtimeState] || runtimeState;
  root.querySelector('#ov-event-toggle').textContent = enabled ? '关闭事件雷达' : '授权开启';
  root.querySelector('#ov-event-toggle').classList.toggle('primary', !enabled);
  root.querySelector('#ov-event-consent').hidden = enabled;
  root.querySelector('#ov-event-controls').hidden = !enabled;
  root.querySelectorAll('[data-event-scope]').forEach(input => {
    input.checked = !config.scopes || config.scopes[input.dataset.eventScope] !== false;
  });
  const link = root.querySelector('[data-event-link="watchlist"]');
  if (link) link.checked = config.watchlist_link !== false;
  root.querySelector('#ov-event-delivery').value = config.delivery || 'digest';
  root.querySelector('#ov-event-horizon').value = localStorage.getItem('dp_event_horizon_v1') || '5';
  const impact = value.impact || {};
  const summary = impact.summary || {};
  const summaryEl = root.querySelector('#ov-event-summary');
  summaryEl.hidden = !enabled;
  summaryEl.innerHTML = enabled ? `
    <div><b class="num">${Number(summary.events || 0)}</b><span>今日事件</span></div>
    <div><b class="num">${Number(summary.linkedEvents || 0)}</b><span>建立行业路径</span></div>
    <div><b class="num">${Number(summary.watchMatches || 0)}</b><span>命中自选</span></div>
    <div><b class="num">${Number(summary.highImportance || 0)}</b><span>高重要性</span></div>` : '';
  const list = root.querySelector('#ov-event-list');
  if (!enabled) {
    list.innerHTML = '<div class="event-radar-empty">尚未授权，因此没有访问事件数据源。</div>';
    return;
  }
  const items = (impact.items || []).filter(item => item.sectors?.length || item.watchlist?.length).slice(0, 5);
  if (!items.length) {
    const errors = (value.errors || []).join('；');
    list.innerHTML = `<div class="event-radar-empty">${esc(errors || '正在等待能建立可核验影响路径的新事件。')}</div>`;
    return;
  }
  list.innerHTML = items.map(item => {
    const event = item.event || {};
    const quality = item.quality || {};
    const sources = (event.sources || []).map(source => source.name).filter(Boolean).join(' / ') || '来源待确认';
    const watches = (item.watchlist || []).map(row => `<span class="event-watch">${esc(row.name || row.code)}</span>`).join('');
    const sectors = (item.sectors || []).slice(0, 5).map(name => `<span>${esc(name)}</span>`).join('');
    const saved = (state.hypotheses?.items || []).some(row => row.baseline?.eventId === event.id
      && ['observing', 'review_due'].includes(row.effectiveStatus));
    return `<article class="event-path-item">
      <div class="event-path-main">
        <div class="event-path-meta"><span>${event.type === 'macro' ? '宏观事件' : '市场快讯'}</span><time>${esc(eventTime(event.scheduledAt))}</time><span>质量 ${esc(quality.score ?? '--')}</span></div>
        <b>${esc(event.title)}</b>
        <div class="event-path-flow"><span class="fact">事实</span><i>→</i><span class="rules">敏感行业</span>${sectors}<i>→</i>${watches || '<em>未命中自选</em>'}</div>
        <p>${esc(item.explanation || '')}</p>
        <small>来源：${esc(sources)} · 观测：${esc(eventTime(event.observedAt))}</small>
      </div>
      <div class="event-path-actions">
        <button class="btn sm" data-event-ask="${esc(event.id)}">让 DeepSeek 核对</button>
        <button class="btn sm ${saved ? 'ghost' : 'primary'}" data-event-save="${esc(event.id)}" ${saved ? 'disabled' : ''}>${saved ? '已保存假设' : '保存研究假设'}</button>
      </div>
    </article>`;
  }).join('');
}

const ROUTINE_STATES = {
  disabled: '未开启',
  waiting: '等待下一时段',
  non_trading_day: '非交易日等待',
  due: '正在准备',
  published: '已生成提醒',
  completed_window: '本时段已完成',
  error: '需要检查',
  stopped: '服务已停止',
};

function renderRoutine(container, routine) {
  const root = container.querySelector('#ov-routine');
  if (!root) return;
  const value = routine || {};
  const tasks = value.config && value.config.tasks || {};
  root.querySelectorAll('[data-routine-task]').forEach(input => {
    input.checked = tasks[input.dataset.routineTask] === true;
  });
  const stateName = value.runtime && value.runtime.state || 'disabled';
  root.dataset.state = stateName;
  root.querySelector('#ov-routine-state').textContent = ROUTINE_STATES[stateName] || '等待服务';
  const next = value.next_service;
  if (next && next.at) {
    const at = new Date(next.at);
    const day = at.toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' });
    const time = at.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false });
    root.querySelector('#ov-routine-next').textContent = `下一次：${day} ${time} · ${next.label}`;
  } else {
    root.querySelector('#ov-routine-next').textContent = value.config && value.config.enabled
      ? '等待下一个已授权时段' : '选择时段后由本机主动服务';
  }
}

async function refreshRoutine(container) {
  try {
    const routine = await api.routineStatus();
    state.routine = routine;
    renderRoutine(container, routine);
  } catch {
    const root = container.querySelector('#ov-routine');
    if (root) {
      root.dataset.state = 'error';
      root.querySelector('#ov-routine-state').textContent = '连接失败';
      root.querySelector('#ov-routine-next').textContent = '请确认本机深脉服务正在运行';
    }
  }
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
