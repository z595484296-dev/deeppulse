/* 深脉 DeepPulse — 数据源页 */

import { api } from '../api.js';
import { esc, toast, downloadText } from '../util.js';

let built = false;

export function init(container) {
  if (built) return;
  built = true;
  container.innerHTML = `
    <div class="grid g12">
      <div class="card span-8">
        <div class="card-head"><div class="card-title">来源分级与可用性</div><div class="card-sub">一级官方披露优先 · 市场聚合仅作行情和线索</div></div>
        <div class="table-scroll" style="max-height:none"><table class="tbl src-table">
          <thead><tr><th>来源</th><th>等级</th><th>用途</th><th>状态</th><th>最近观测</th><th>入口</th></tr></thead>
          <tbody id="ds-sources"><tr><td colspan="6"><div class="empty">正在读取来源状态…</div></td></tr></tbody>
        </table></div>
        <div style="margin-top:12px;font-size:11.5px;color:var(--text-3);line-height:1.8">
          · “未观测”表示本次运行尚未访问该来源，不等于在线；“查验入口”只提供官方人工核验链接。<br>
          · 服务内置主机熔断、备援切换与指标剔除降级；行情 5 秒、情绪池 25 秒、K 线 60 秒、公告 5 分钟缓存。
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
          <button class="btn" id="ds-clear-cache">清空本地缓存</button>
        </div>
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
        const head = '日期,温度,阶段,涨停,跌停,炸板,炸板率%,最高连板,连板家数,昨涨停指数%,昨连板指数%,上涨,下跌,成交额亿,主力净流入亿,上证vsMA20%,建议仓位';
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
      localStorage.clear();
      toast('本地缓存已清空（自选/日记已一并清除）');
    } catch { toast('清空失败', 'err'); }
  });
}

const STATUS_LABELS = {
  ok: '最近访问成功', degraded: '访问降级', reference: '官方查验入口',
  unobserved: '本次尚未访问',
};

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
        <td><span class="source-tier ${esc(s.tier)}">${s.tier === 'official' ? '一级官方' : '市场聚合'}</span></td>
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

async function renderStatus(container) {
  const el = container.querySelector('#ds-status');
  el.innerHTML = '<div class="v">检测中…</div>';
  try {
    const h = await api.health();
    let em = null;
    try { em = await api.emotion(); } catch { /* 静默 */ }
    const engine = (em && em.engine) || {};
    const degraded = engine.degraded;
    el.innerHTML = `
      <span class="k">服务状态</span><span class="v"><i class="dot ok" style="width:7px;height:7px;border-radius:50%;background:var(--down);display:inline-block;margin-right:6px"></i>运行中（v1.2）</span>
      <span class="k">服务时间</span><span class="v num">${esc(h.time || '--')}</span>
      <span class="k">端口</span><span class="v num">${location.port}</span>
      <span class="k">情绪数据</span><span class="v">${degraded
        ? `<span style="color:var(--amber)">部分降级（${esc((engine.missing || []).join('、'))}）</span>`
        : '<span style="color:var(--down)">完整可用</span>'}</span>
      <span class="k">数据日期</span><span class="v num">${esc((em && em.date) || '--')}</span>
      <span class="k">历史快照</span><span class="v num">${(em && em.history && em.history.length) || 0} 天</span>
      <span class="k">引擎温度</span><span class="v num">${engine.temp ?? '--'}° · ${esc(engine.phase || '--')}</span>
    `;
  } catch (e) {
    el.innerHTML = `<span class="k">服务状态</span><span class="v" style="color:var(--up)">不可用：${esc(e.message)}</span>`;
  }
}

export async function refresh(container) {
  init(container);
  await Promise.allSettled([renderSources(container), renderStatus(container)]);
}
