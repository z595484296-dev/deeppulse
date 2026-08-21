/* 深脉 DeepPulse — 数据源页 */

import { api } from '../api.js?v=1.22.1';
import { esc, toast, downloadText } from '../util.js?v=1.22.1';
import { state } from '../store.js?v=1.22.1';

let built = false;

export function init(container) {
  if (built) return;
  built = true;
  container.innerHTML = `
    <div class="grid g12">
      <div class="card span-8">
        <div class="card-head"><div class="card-title">来源分级与可用性</div><div class="card-sub">一级官方披露优先 · 本地终端增强 · 市场聚合备援</div></div>
        <div class="table-scroll" style="max-height:none"><table class="tbl src-table">
          <thead><tr><th>来源</th><th>等级</th><th>用途</th><th>状态</th><th>最近观测</th><th>入口</th></tr></thead>
          <tbody id="ds-sources"><tr><td colspan="6"><div class="empty">正在读取来源状态…</div></td></tr></tbody>
        </table></div>
        <div style="margin-top:12px;font-size:11.5px;color:var(--text-3);line-height:1.8">
          · “未观测”表示本次运行尚未访问该来源，不等于在线；“查验入口”只提供官方人工核验链接。<br>
          · 服务内置主机熔断、备援切换与指标剔除降级；行情 5 秒、情绪池 25 秒、K 线 60 秒、公告 5 分钟缓存。<br>
          · 通达信 TQ-Local 是可选的 Windows 本地增强源，不可用时不会阻断深脉。
        </div>
      </div>

      <div class="card span-4">
        <div class="card-head"><div class="card-title">运行状态</div></div>
        <div class="kv" id="ds-status"></div>
        <div style="display:flex;flex-direction:column;gap:8px;margin-top:14px">
          <button class="btn primary" id="ds-record">记录今日情绪快照</button>
          <button class="btn" id="ds-refresh">立即刷新全部数据</button>
          <div style="border-top:1px solid var(--line);padding-top:10px;margin-top:4px">
            <div style="font-size:11px;color:var(--text-3);margin-bottom:6px">📦 数据导出（情绪历史快照）</div>
            <div style="display:flex;gap:8px">
              <button class="btn sm" id="ds-export-json">导出 JSON</button>
              <button class="btn sm" id="ds-export-csv">导出 CSV</button>
            </div>
          </div>
          <button class="btn" id="ds-clear-cache">清除盘中临时轨迹</button>
        </div>
      </div>

      <div class="card span-12 tdx-integration">
        <div class="card-head">
          <div>
            <div class="card-title">📡 通达信 TQ-Local</div>
            <div class="card-sub">本机 127.0.0.1:17709 · 行情/K线优先 · 情绪统计交叉验证</div>
          </div>
          <span class="source-tier local">本地只读</span>
        </div>
        <div class="tdx-grid">
          <div id="ds-tdx-status" class="tdx-status"><div class="empty">正在检查本地环境…</div></div>
          <div class="tdx-actions">
            <button class="btn primary" id="ds-tdx-probe">检测并接入</button>
            <a class="btn" id="ds-tdx-help" href="https://help.tdx.com.cn/quant/" target="_blank" rel="noopener noreferrer">TQ 帮助</a>
          </div>
        </div>
        <div class="tdx-safety">🔒 深脉适配器只允许行情、K线、证券信息与市场统计查询；账户、持仓、下单和撤单接口均未开放。</div>
      </div>

      <div class="card span-12 akshare-integration">
        <div class="card-head">
          <div>
            <div class="card-title">🧭 AKShare 补充层</div>
            <div class="card-sub">交易日历 · 宏观事件双源补充 · 事件雷达需单独授权</div>
          </div>
          <span class="source-tier enrichment">补充数据</span>
        </div>
        <div class="tdx-grid">
          <div id="ds-akshare-status" class="tdx-status"><div class="empty">正在读取本机环境…</div></div>
          <div class="tdx-actions">
            <button class="btn primary" id="ds-akshare-probe">核对交易日历</button>
            <button class="btn" id="ds-akshare-research">生成研究增强快照</button>
            <button class="btn" id="ds-akshare-ask" disabled>让 DeepSeek 解读</button>
            <a class="btn" href="https://akshare.akfamily.xyz/" target="_blank" rel="noopener noreferrer">官方文档</a>
          </div>
        </div>
        <div id="ds-akshare-research-panel" class="akresearch-panel">
          <div class="empty">研究增强按需读取，不会自动影响情绪温度。点击“生成研究增强快照”查看宏观与利率背景。</div>
        </div>
        <div class="tdx-safety">分层原则：AKShare 不替代交易所公告、通达信本地行情或实时行情主链路；事件路径只表示透明规则识别的敏感性，不代表因果或方向预测。</div>
      </div>

      <div class="card span-12">
        <div class="card-head">
          <div>
            <div class="card-title">🩺 一键产品诊断</div>
            <div class="card-sub">一次检查数据服务、对话工作台、桌面提醒、数据源与墨水屏；导出包已自动脱敏</div>
          </div>
          <span id="ds-diagnostic-badge" class="source-tier enrichment">检查中</span>
        </div>
        <div id="ds-diagnostics"><div class="empty">正在检查各项能力…</div></div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:14px">
          <button class="btn primary" id="ds-run-diagnostics">重新诊断</button>
          <button class="btn" id="ds-export-diagnostics">导出脱敏诊断包</button>
        </div>
        <div class="tdx-safety" id="ds-diagnostic-privacy">不会导出 API 密钥、配对令牌、本机路径、IP、自选股、提醒内容或聊天记录。</div>
      </div>

      <div class="card span-12">
        <div class="card-head"><div class="card-title">免责声明</div></div>
        <div style="font-size:12px;color:var(--text-2);line-height:2">
          深脉 DeepPulse 是个人金融研究工具。公告优先展示官方原文索引，行情与快讯来自市场聚合接口，
          均可能存在延迟、缺失或错误；情绪温度与策略建议由规则引擎自动生成，<b style="color:var(--amber)">仅供研究与学习参考，
          不构成任何投资建议</b>。市场有风险，决策需独立，盈亏自负。请勿将本工具用于高频或程序化交易。
        </div>
      </div>
    </div>
  `;

  container.querySelector('#ds-record').addEventListener('click', async e => {
    const btn = e.target;
    btn.disabled = true; btn.textContent = '记录中…';
    try {
      const r = await api.recordSnapshot();
      toast(r && r.ok ? '情绪快照已写入历史记忆' : '记录失败', r && r.ok ? 'ok' : 'err');
    } catch (err) { toast('记录失败：' + err.message, 'err'); }
    btn.disabled = false; btn.textContent = '记录今日情绪快照';
    renderStatus(container);
  });
  container.querySelector('#ds-refresh').addEventListener('click', () => {
    location.reload();
  });
  container.querySelector('#ds-tdx-probe').addEventListener('click', async e => {
    const btn = e.currentTarget;
    btn.disabled = true; btn.textContent = '检测中…';
    await renderTdxStatus(container, true);
    await renderSources(container);
    btn.disabled = false; btn.textContent = '重新检测';
  });
  container.querySelector('#ds-akshare-probe').addEventListener('click', async e => {
    const btn = e.currentTarget;
    btn.disabled = true; btn.textContent = '核对中…';
    await renderAkshareStatus(container, true);
    await renderSources(container);
    btn.disabled = false; btn.textContent = '重新核对';
  });
  container.querySelector('#ds-akshare-research').addEventListener('click', async e => {
    const btn = e.currentTarget;
    btn.disabled = true; btn.textContent = '读取中，可能需要约 30–60 秒…';
    await renderAkshareResearch(container, true);
    await renderSources(container);
    btn.disabled = false; btn.textContent = '重新生成研究增强快照';
  });
  container.querySelector('#ds-akshare-ask').addEventListener('click', () => {
    if (!state.akshareResearch || state.akshareResearch.status === 'not_loaded') return;
    document.dispatchEvent(new CustomEvent('ask-akshare-research', {
      detail: { snapshot: state.akshareResearch },
    }));
  });
  container.querySelector('#ds-run-diagnostics').addEventListener('click', async e => {
    const btn = e.currentTarget;
    btn.disabled = true; btn.textContent = '诊断中…';
    await renderDiagnostics(container);
    btn.disabled = false; btn.textContent = '重新诊断';
  });
  container.querySelector('#ds-export-diagnostics').addEventListener('click', async e => {
    const btn = e.currentTarget;
    btn.disabled = true; btn.textContent = '正在生成…';
    try {
      const blob = await api.diagnosticsBundle();
      const link = document.createElement('a');
      link.href = URL.createObjectURL(blob);
      const stamp = new Date().toISOString().replace(/[-:]/g, '').slice(0, 15);
      link.download = `DeepPulse-Diagnostics-${stamp}.zip`;
      document.body.appendChild(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(link.href), 1000);
      toast('脱敏诊断包已导出', 'ok');
    } catch (err) { toast('导出失败：' + err.message, 'err'); }
    btn.disabled = false; btn.textContent = '导出脱敏诊断包';
  });
  container.querySelector('#ds-diagnostics').addEventListener('click', async e => {
    const btn = e.target.closest('[data-diagnostic-repair]');
    if (!btn) return;
    const action = btn.dataset.diagnosticRepair || '';
    const original = btn.textContent;
    btn.disabled = true; btn.textContent = '处理中…';
    try {
      const result = await api.repairDiagnostics(action);
      toast(result.message || (result.ok ? '处理完成' : '仍需手动检查'), result.ok ? 'ok' : 'warn');
      await renderDiagnostics(container);
      if (action === 'probe_tdx') await renderTdxStatus(container, false);
      if (action === 'probe_akshare') await renderAkshareStatus(container, false);
      await renderSources(container);
    } catch (err) {
      toast('处理失败：' + err.message, 'err');
      btn.disabled = false; btn.textContent = original;
    }
  });
  // 导出情绪历史快照
  const exportHistory = async (fmt) => {
    try {
      const em = await api.emotion();
      const h = (em && em.history) || [];
      if (!h.length) { toast('暂无历史快照（收盘后自动记录）', 'err'); return; }
      const date = new Date().toISOString().slice(0, 10);
      if (fmt === 'json') {
        downloadText(`深脉情绪快照_${date}.json`, JSON.stringify(h, null, 2), 'application/json');
      } else {
        const head = '日期,温度,阶段,涨停,跌停,炸板,炸板率%,最高连板,连板家数,昨涨停指数%,昨连板指数%,上涨,下跌,成交额亿,主力净流入亿,上证vsMA20%,研究仓位区间';
        const rows = h.map(s => {
          const r = s.raw || {};
          const adv = s.advice || {};
          return [s.date, s.temp, s.phase, r.zt, r.dt, r.zb, r.zb_rate != null ? (r.zb_rate * 100).toFixed(1) : '', r.height, r.lb_count,
            r.zt_idx_pct ?? '', r.lb_idx_pct ?? '', r.up, r.down, r.turnover_yi ?? '', r.flow_yi ?? '', r.trend_pct ?? '', adv.position || ''].join(',');
        });
        downloadText(`深脉情绪快照_${date}.csv`, head + '\n' + rows.join('\n'), 'text/csv');
      }
      toast('导出成功');
    } catch (e) {
      toast('导出失败：' + e.message, 'err');
    }
  };
  container.querySelector('#ds-export-json').addEventListener('click', () => exportHistory('json'));
  container.querySelector('#ds-export-csv').addEventListener('click', () => exportHistory('csv'));
  container.querySelector('#ds-clear-cache').addEventListener('click', () => {
    try {
      localStorage.removeItem('dp_intraday_v1');
      toast('盘中临时轨迹已清除；自选、提醒和日记均已保留');
    } catch { toast('清空失败', 'err'); }
  });
}

const STATUS_LABELS = {
  ok: '最近访问成功', degraded: '访问降级', reference: '官方查验入口',
  unobserved: '本次尚未访问', not_installed: '未安装', not_running: '客户端未启动',
  unavailable: '本地服务不可用', unsupported: '当前系统不支持', disabled: '已关闭',
};

const TIER_LABELS = { official: '一级官方', local: '本地终端', market: '市场聚合', enrichment: '补充数据' };

async function renderSources(container) {
  const body = container.querySelector('#ds-sources');
  try {
    const data = await api.sources();
    body.innerHTML = (data.items || []).map(s => {
      const observed = s.last_observed
        ? new Date(s.last_observed).toLocaleString('zh-CN', { hour12: false })
        : '--';
      const detail = s.latency_ms != null ? `${observed} · ${s.latency_ms}ms` : observed;
      return `<tr>
        <td><div class="source-name">${esc(s.name)}</div><div class="code-sub">${esc((s.hosts || []).join(' / '))}</div></td>
        <td><span class="source-tier ${esc(s.tier)}">${esc(TIER_LABELS[s.tier] || s.tier)}</span></td>
        <td style="color:var(--text-2)">${esc(s.role)}</td>
        <td><span class="source-status ${esc(s.status)}">${esc(STATUS_LABELS[s.status] || s.status)}</span></td>
        <td class="code-sub">${esc(detail)}</td>
        <td><a class="btn sm" href="${esc(s.homepage)}" target="_blank" rel="noopener noreferrer">查看</a></td>
      </tr>`;
    }).join('');
  } catch (e) {
    body.innerHTML = `<tr><td colspan="6"><div class="empty">来源状态读取失败：${esc(e.message)}</div></td></tr>`;
  }
}

async function renderTdxStatus(container, fresh = false) {
  const el = container.querySelector('#ds-tdx-status');
  if (!el) return;
  el.innerHTML = '<div class="empty">正在执行 Windows / 安装 / 进程 / HTTP 四步检查…</div>';
  try {
    const s = await api.tdxStatus(fresh);
    const labels = {
      ok: ['已连接', 'ok'], not_installed: ['未安装通达信', 'warn'],
      not_running: ['通达信客户端未启动', 'warn'], unavailable: ['TQ 本地服务不可用', 'err'],
      unsupported: ['仅支持 Windows', 'warn'], disabled: ['本地增强已关闭', 'warn'],
      unobserved: ['等待服务探测', 'warn'],
    };
    const [label, tone] = labels[s.status] || [s.status || '未知状态', 'warn'];
    const install = s.install || {};
    const details = [];
    details.push(`系统：${esc(s.system || '--')}`);
    details.push(`安装：${s.installed ? esc(install.name || '已检测到') : '未检测到'}`);
    details.push(`客户端：${s.process_running ? 'TdxW.exe 运行中' : '未运行'}`);
    details.push(`本地服务：${s.service_ready ? `可用 · ${Number(s.latency_ms || 0)}ms` : '未就绪'}`);
    el.innerHTML = `
      <div class="tdx-state ${tone}"><i class="dot ${tone === 'ok' ? 'ok' : tone === 'err' ? 'err' : 'warn'}"></i>${esc(label)}</div>
      <div class="tdx-detail">${details.map(x => `<span>${x}</span>`).join('')}</div>
      ${s.error ? `<div class="tdx-error">${esc(s.error)}</div>` : ''}
      ${s.status === 'not_installed' ? `<a class="tdx-download" href="${esc(s.installer_url || '#')}" target="_blank" rel="noopener noreferrer">打开通达信官方安装包地址</a>` : ''}
    `;
    if (s.status === 'ok') toast('通达信 TQ-Local 已接入，只读模式', 'ok');
  } catch (e) {
    el.innerHTML = `<div class="tdx-state err"><i class="dot err"></i>检测失败</div><div class="tdx-error">${esc(e.message)}</div>`;
  }
}

async function renderAkshareStatus(container, probe = false) {
  const el = container.querySelector('#ds-akshare-status');
  if (!el) return;
  el.innerHTML = '<div class="empty">正在核对 AKShare 与交易日历…</div>';
  try {
    const s = await api.akshareStatus(probe);
    const calendar = s.calendar || {};
    const tone = s.status === 'ok' ? 'ok' : s.status === 'not_installed' ? 'warn' : 'warn';
    const label = s.status === 'ok' ? '交易日历已接入'
      : s.status === 'not_installed' ? '本机未安装 AKShare'
        : s.status === 'degraded' ? '交易日历降级' : '已安装，尚未核对';
    const details = [
      `版本：${esc(s.version || (s.installed ? '等待载入' : '--'))}`,
      calendar.date ? `日期：${esc(calendar.date)} · ${calendar.is_trade_date ? '交易日' : '非交易日'}` : '日历：等待主动服务调用',
      `定位：${esc(s.role || '补充数据层')}`,
      s.interfaces ? `事件接口：宏观日历 ${s.interfaces.macro_calendar ? '可用' : '不可用'} · 互证 ${s.interfaces.macro_corroboration ? '可用' : '不可用'}` : '事件接口：等待载入',
      s.event_service ? `事件雷达：${s.event_service.enabled ? '已授权' : '未授权'}` : '事件雷达：等待服务状态',
    ];
    el.innerHTML = `
      <div class="tdx-state ${tone}"><i class="dot ${tone}"></i>${esc(label)}</div>
      <div class="tdx-detail">${details.map(x => `<span>${x}</span>`).join('')}</div>
      ${(s.error || calendar.error) ? `<div class="tdx-error">${esc(s.error || calendar.error)}</div>` : ''}
    `;
    if (s.status === 'ok') toast('AKShare 交易日历核对成功', 'ok');
  } catch (e) {
    el.innerHTML = `<div class="tdx-state err"><i class="dot err"></i>核对失败</div><div class="tdx-error">${esc(e.message)}</div>`;
  }
}

async function renderAkshareResearch(container, refresh = false) {
  const el = container.querySelector('#ds-akshare-research-panel');
  if (!el) return;
  if (refresh) el.innerHTML = '<div class="empty">正在读取宏观与利率背景，并核对每项数据日期…</div>';
  try {
    const snapshot = await api.akshareResearch(refresh);
    state.akshareResearch = snapshot;
    if (!snapshot || snapshot.status === 'not_loaded') return;
    const refreshButton = container.querySelector('#ds-akshare-research');
    const askButton = container.querySelector('#ds-akshare-ask');
    if (refreshButton) refreshButton.textContent = '重新生成研究增强快照';
    if (askButton) askButton.disabled = snapshot.status === 'not_installed';
    if (snapshot.status === 'not_installed') {
      el.innerHTML = '<div class="tdx-error">本机未安装 AKShare，研究增强暂不可用。</div>';
      return;
    }
    const statusLabels = { current: '时效正常', partial: '部分陈旧', stale: '数据陈旧', unavailable: '暂不可用' };
    const metricLabels = { current: '可用', stale: '陈旧', unavailable: '缺失' };
    const summary = snapshot.summary || {};
    el.innerHTML = `
      <div class="akresearch-head">
        <div><b>研究增强快照</b><span>${esc(snapshot.generatedAt ? new Date(snapshot.generatedAt).toLocaleString('zh-CN', { hour12: false }) : '--')}</span></div>
        <div class="akresearch-summary"><span class="ok">${Number(summary.current || 0)} 项可用</span><span class="warn">${Number(summary.stale || 0)} 项陈旧</span><span>${Number(summary.unavailable || 0)} 项缺失</span></div>
      </div>
      <div class="akresearch-modules">
        ${(snapshot.modules || []).map(module => `<section class="akresearch-module ${esc(module.status || '')}">
          <header><div><b>${esc(module.label)}</b><small>${esc(module.purpose)}</small></div><span>${esc(statusLabels[module.status] || module.status || '--')}</span></header>
          <div class="akresearch-metrics">${(module.metrics || []).map(metric => `<div class="akresearch-metric ${esc(metric.status || '')}">
            <div><span>${esc(metric.label)}</span><strong>${metric.value == null ? '--' : esc(metric.value)}${metric.value == null ? '' : `<small>${esc(metric.unit || '')}</small>`}</strong></div>
            <div class="akresearch-meta"><span>${esc(metricLabels[metric.status] || metric.status || '--')} · ${esc(metric.asOf || '无数据日期')}</span><span>上游：${esc(metric.source?.upstream || '--')}</span></div>
            ${metric.note ? `<p>${esc(metric.note)}</p>` : ''}
          </div>`).join('')}</div>
        </section>`).join('')}
      </div>
      ${(snapshot.errors || []).length ? `<div class="akresearch-errors"><b>降级记录：</b>${snapshot.errors.map(row => `${esc(row.interface)}：${esc(row.error)}`).join(' · ')}</div>` : ''}
      <div class="akresearch-boundary"><b>来源规则：</b>${esc(snapshot.lineagePolicy || '')}<br><b>产品边界：</b>${esc(snapshot.boundary || '')}</div>
    `;
  } catch (error) {
    el.innerHTML = `<div class="tdx-error">研究增强读取失败：${esc(error.message)}</div>`;
  }
}

async function renderStatus(container) {
  const el = container.querySelector('#ds-status');
  el.innerHTML = '<div class="v">正在检测本地服务…</div>';
  try {
    const h = await api.health();
    el.innerHTML = `
      <span class="k">服务状态</span><span class="v"><i class="dot ok" style="width:7px;height:7px;border-radius:50%;background:var(--down);display:inline-block;margin-right:6px"></i>运行中（v${esc(h.version || '1.4')}）</span>
      <span class="k">服务时间</span><span class="v num">${esc(h.time || '--')}</span>
      <span class="k">端口</span><span class="v num">${location.port}</span>
      <span class="k">情绪数据</span><span class="v" id="ds-emotion-state">正在后台核验…</span>
    `;
    let em = null;
    try { em = await api.emotion(); } catch { /* 静默 */ }
    const engine = (em && em.engine) || {};
    const degraded = engine.degraded;
    const emotionState = el.querySelector('#ds-emotion-state');
    if (emotionState) emotionState.innerHTML = !em
      ? '<span style="color:var(--amber)">核验超时，可稍后刷新</span>'
      : degraded
        ? `<span style="color:var(--amber)">部分降级（${esc((engine.missing || []).join('、'))}）</span>`
        : '<span style="color:var(--down)">完整可用</span>';
    el.insertAdjacentHTML('beforeend', `
      <span class="k">数据日期</span><span class="v num">${esc((em && em.date) || '--')}</span>
      <span class="k">历史快照</span><span class="v num">${(em && em.history && em.history.length) || 0} 天</span>
      <span class="k">引擎温度</span><span class="v num">${engine.temp ?? '--'}° · ${esc(engine.phase || '--')}</span>
    `);
  } catch (e) {
    el.innerHTML = `<span class="k">服务状态</span><span class="v" style="color:var(--up)">不可用：${esc(e.message)}</span>`;
  }
}

async function renderDiagnostics(container) {
  const el = container.querySelector('#ds-diagnostics');
  const badge = container.querySelector('#ds-diagnostic-badge');
  if (!el) return;
  el.innerHTML = '<div class="empty">正在检查各项能力…</div>';
  try {
    const report = await api.diagnostics();
    const labels = { ok: '正常', attention: '需要留意', action_required: '需要处理' };
    const colors = { ok: 'var(--down)', attention: 'var(--amber)', action_required: 'var(--up)' };
    badge.textContent = labels[report.overall] || '已完成';
    badge.style.color = colors[report.overall] || 'var(--text-2)';
    const trendLabels = { recovered: '已恢复', persistent: '持续存在', new_issue: '刚出现', first_observation: '首次记录' };
    el.innerHTML = `
      <div style="display:flex;gap:14px;flex-wrap:wrap;margin-bottom:10px;font-size:11.5px;color:var(--text-3)">
        <span>历史基线 ${(report.history && report.history.samples) || 0} 次</span>
        ${report.history && report.history.recovered ? `<span style="color:var(--down)">已恢复 ${report.history.recovered} 项</span>` : ''}
        ${report.history && report.history.persistent ? `<span style="color:var(--amber)">持续问题 ${report.history.persistent} 项</span>` : ''}
        ${report.history && report.history.newIssues ? `<span style="color:var(--up)">新问题 ${report.history.newIssues} 项</span>` : ''}
      </div>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:10px">
        ${(report.components || []).map(row => {
          const tone = row.state === 'ok' ? 'ok' : row.state === 'error' ? 'err' : 'warn';
          const stateLabel = row.state === 'ok' ? '正常' : row.state === 'error' ? '需处理' : row.state === 'warn' ? '留意' : '可选';
          return `<div style="border:1px solid var(--line);border-radius:12px;padding:12px;background:var(--panel-2)">
            <div class="tdx-state ${tone}" style="margin-bottom:7px"><i class="dot ${tone}"></i>${esc(row.label)} · ${stateLabel}${trendLabels[row.trend] ? ` · ${trendLabels[row.trend]}` : ''}</div>
            <div style="font-size:12px;color:var(--text-2);line-height:1.7">${esc(row.summary)}</div>
            ${row.action ? `<div style="font-size:11.5px;color:var(--text-3);line-height:1.7;margin-top:5px">建议：${esc(row.action)}</div>` : ''}
            ${row.repairAction ? `<button class="btn sm" data-diagnostic-repair="${esc(row.repairAction)}" style="margin-top:8px">${esc(row.repairLabel || '尝试修复')}</button>` : ''}
          </div>`;
        }).join('')}
      </div>
      ${(report.actions || []).length ? `<div style="margin-top:12px;padding:10px 12px;border-left:3px solid var(--amber);background:var(--panel-2);font-size:12px;color:var(--text-2);line-height:1.8">
        <b>优先处理：</b>${report.actions.map(row => esc(row.text)).join(' · ')}
      </div>` : ''}`;
    const privacy = container.querySelector('#ds-diagnostic-privacy');
    if (privacy) privacy.textContent = '🔒 ' + (report.privacy || '诊断报告已脱敏。');
  } catch (err) {
    badge.textContent = '检查失败';
    el.innerHTML = `<div class="tdx-error">无法完成诊断：${esc(err.message)}</div>`;
  }
}

export async function refresh(container) {
  init(container);
  await Promise.allSettled([
    renderSources(container), renderStatus(container), renderTdxStatus(container), renderAkshareStatus(container),
    renderAkshareResearch(container, false), renderDiagnostics(container),
  ]);
}
