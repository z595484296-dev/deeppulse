/* 深脉 DeepPulse — 策略页（情绪周期策略引擎 · 复盘与日记） */

import { api } from '../api.js?v=1.7.0';
import { loadJournal, saveJournalEntry, deleteJournalEntry, bus } from '../store.js?v=1.7.0';
import { esc, toast, PHASE_COLORS, emptyState, downloadText } from '../util.js?v=1.7.0';
import { EMBEDDED, generateWithDeepSeek } from '../bridge.js?v=1.7.0';

let built = false;
let lastEm = null;   // 最近一次情绪数据（导出复盘/日历用）
let calRender = null; // 复盘日历渲染句柄（refresh 时重绘）

const MATRIX = [
  { phase: '冰点期', color: 'blue', range: '0≤T<20', pos: '0-2成', tip: '低暴露场景 · 等修复证据' },
  { phase: '修复期', color: 'cyan', range: '20≤T<40', pos: '2-4成', tip: '验证场景 · 观察核心反馈' },
  { phase: '发酵期', color: 'amber', range: '40≤T<60', pos: '5-8成', tip: '扩散场景 · 核对主线持续性' },
  { phase: '高潮期', color: 'red', range: '60≤T<80', pos: '5-7成', tip: '分歧场景 · 核对兑现压力' },
  { phase: '亢奋期', color: 'violet', range: '80≤T≤100', pos: '≤3成', tip: '过热场景 · 观察退潮信号' },
];

const TEMPLATE = `【复盘模板 · 情绪周期版】
1. 今日温度与阶段：____°（冰点/修复/发酵/高潮/亢奋），与昨日相比：升温/降温 ____°
2. 核心数据：涨停__家、跌停__家、炸板率__%、最高__连板、昨日涨停指数__%
3. 主线题材：____（涨停最多的行业），龙头是谁，梯队是否完整？
4. 今日操作：买入/卖出/持有/空仓，是否符合当前阶段的标准打法？
5. 情绪归因：是什么事件或资金行为驱动了今天的情绪变化？
6. 明日验证：温度若升/若降，风险暴露假设如何变化？需验证哪些板块或标的？
`;

/** Harness 内优先使用当前会话生成并回填；独立版或 Harness 失败时使用本地大脑接口。 */
async function generateReviewBody(prompt, intent) {
  let harnessError = '';
  if (EMBEDDED) {
    const generated = await generateWithDeepSeek({ question: prompt, context: { intent } });
    if (generated && generated.ok && generated.reply) {
      return { reply: generated.reply.trim(), source: 'harness' };
    }
    harnessError = (generated && generated.error) || 'Harness 未返回正文';
  }

  try {
    const local = await api.chat([{ role: 'user', content: prompt }]);
    if (local && local.mode === 'llm' && local.reply) {
      return { reply: local.reply.trim(), source: 'local' };
    }
  } catch (error) {
    if (!harnessError) throw error;
  }
  throw new Error(harnessError || '尚未配置可用的 DeepSeek 大脑，请先在 Harness 打开会话或配置独立版 API');
}

export function init(container) {
  if (built) return;
  built = true;
  container.innerHTML = `
    <div class="grid g12">
      <div class="card span-8">
        <div class="card-head"><div class="card-title">今日情绪诊断</div><div class="card-sub" id="st-diag-date">--</div></div>
        <div id="st-diag"></div>
      </div>

      <div class="card span-4">
        <div class="card-head"><div class="card-title">模型风险暴露矩阵</div><div class="card-sub">情景研究参考，不是用户仓位建议</div></div>
        <div class="pos-matrix" id="st-matrix"></div>
        <div style="margin-top:12px;font-size:11.5px;color:var(--text-3);line-height:1.8">
          数值区间仅描述模型在不同情绪阶段下的风险暴露假设；实际决策仍需结合个人约束、
          价格位置与反证条件，不由工作台替用户执行。
        </div>
      </div>

      <div class="card span-6">
        <div class="card-head"><div class="card-title">风险清单</div><div class="card-sub">引擎自动扫描</div></div>
        <div id="st-risks"></div>
      </div>

      <div class="card span-6">
        <div class="card-head"><div class="card-title">打分贡献榜</div><div class="card-sub">谁在推高/拖累情绪</div></div>
        <div id="st-contrib"></div>
      </div>

      <div class="card span-12">
        <div class="card-head"><div class="card-title">⚙️ 引擎调教</div><div class="card-sub">先在草稿中预览温度影响，确认后才应用到引擎</div></div>
        <div class="tune-grid" id="st-tune"></div>
        <div style="margin-top:10px;display:flex;gap:8px;align-items:center">
          <button class="btn sm primary" id="st-tune-apply" disabled>应用权重</button>
          <button class="btn sm" id="st-tune-discard" disabled>放弃修改</button>
          <button class="btn sm ghost" id="st-tune-reset">载入默认草稿</button>
          <span style="font-size:11px;color:var(--text-3)" id="st-tune-hint"></span>
        </div>
      </div>

      <div class="card span-12">
        <div class="card-head"><div class="card-title">📅 复盘日历</div><div class="card-sub">格子色=当日情绪阶段 · 📔=已写复盘 · 点击任意日期补写</div></div>
        <div class="grid g2">
          <div>
            <div class="cal-nav">
              <button class="btn sm ghost" id="st-cal-prev">‹ 上月</button>
              <span class="cal-title" id="st-cal-title">--</span>
              <button class="btn sm ghost" id="st-cal-next">下月 ›</button>
            </div>
            <div class="cal-grid" id="st-cal"></div>
          </div>
          <div class="cal-panel" id="st-cal-panel">
            <div class="cal-panel-head">
              <span id="st-cal-day-label">选择一天</span>
              <span id="st-cal-day-badge"></span>
            </div>
            <textarea id="st-cal-text" placeholder="该日的复盘内容…"></textarea>
            <div style="display:flex;gap:8px;margin-top:8px;flex-wrap:wrap">
              <button class="btn sm primary" id="st-cal-save">保存</button>
              <button class="btn sm" id="st-cal-ai">🤖 AI 生成该日复盘</button>
              <button class="btn sm ghost" id="st-cal-del">删除</button>
            </div>
            <div style="font-size:11px;color:var(--text-3);margin-top:8px;line-height:1.7" id="st-cal-note">
              复盘日历把「情绪周期 × 你的复盘」织在一起：格子颜色取自每日情绪快照，📔 标记你写过的日子。
            </div>
          </div>
        </div>
      </div>

      <div class="card span-12">
        <div class="card-head"><div class="card-title">复盘与情绪日记</div><div class="card-sub">我的记忆 · 保存在本机并跨端共享</div></div>
        <div class="grid g2">
          <div class="journal-box">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;gap:8px;flex-wrap:wrap">
              <span style="font-size:12px;color:var(--text-2)" id="st-jdate">今日复盘</span>
              <div style="display:flex;gap:8px">
                <button class="btn sm" id="st-ai-review">🤖 让 DeepSeek 生成复盘</button>
                <button class="btn sm" id="st-export-md">📤 导出 Markdown</button>
                <button class="btn sm primary" id="st-save">保存日记</button>
              </div>
            </div>
            <textarea id="st-jtext" placeholder="写下今天的复盘…"></textarea>
          </div>
          <div id="st-jlist" style="max-height:300px;overflow-y:auto"></div>
        </div>
      </div>
    </div>
  `;

  container.querySelector('#st-matrix').innerHTML = MATRIX.map(m => `
    <div class="pos-cell" data-phase="${m.phase}">
      <div class="pc-phase" style="color:${PHASE_COLORS[m.color]}">${m.phase}</div>
      <div class="pc-range num" style="color:${PHASE_COLORS[m.color]}">${m.pos}</div>
      <div class="pc-temp">${m.range}</div>
      <div class="pc-temp">${esc(m.tip)}</div>
    </div>`).join('');

  const jd = todayStr();
  container.querySelector('#st-jdate').textContent = jd + ' 复盘';
  const todayEntry = loadJournal().find(e => e.date === jd);
  container.querySelector('#st-jtext').value = todayEntry ? todayEntry.text : TEMPLATE;

  container.querySelector('#st-save').addEventListener('click', () => {
    const text = container.querySelector('#st-jtext').value.trim();
    if (!text) { toast('先写点什么再保存', 'err'); return; }
    saveJournalEntry(jd, text);
    renderJournal(container);
    toast('已存入我的情绪日记');
  });

  // 导出复盘 Markdown（引擎数据 + 风险 + 日记合订）
  container.querySelector('#st-export-md').addEventListener('click', async () => {
    let em = lastEm;
    if (!em) { try { em = await api.emotion(); } catch { /* 忽略 */ } }
    if (!em || !em.engine) { toast('暂无情绪数据，稍后再试', 'err'); return; }
    const en = em.engine || {};
    const raw = en.raw || {};
    const date = em.date || todayStr();
    const journal = loadJournal().find(e => e.date === date);
    const lines = [
      `# 深脉复盘 · ${date}`,
      '',
      '## 情绪概况',
      `- 情绪温度 **${en.temp ?? '--'}°**（${en.phase ?? '--'}）`,
      `- 涨停 ${raw.zt ?? '--'} 家 · 跌停 ${raw.dt ?? '--'} 家 · 炸板率 ${raw.zb_rate != null ? (raw.zb_rate * 100).toFixed(1) : '--'}%`,
      `- 最高 ${raw.height ?? '--'} 连板 · 连板 ${raw.lb_count ?? '--'} 家`,
      `- 昨日涨停指数 ${raw.zt_idx_pct != null ? raw.zt_idx_pct.toFixed(2) : '--'}% · 昨日连板指数 ${raw.lb_idx_pct != null ? raw.lb_idx_pct.toFixed(2) : '--'}%`,
      `- 涨跌 ${raw.up ?? '--'} : ${raw.down ?? '--'} · 成交 ${raw.turnover_yi ?? '--'} 亿 · 主力净流入 ${raw.flow_yi != null ? raw.flow_yi.toFixed(1) : '--'} 亿`,
      '',
      '## 引擎诊断',
      (en.narrative || '').replace(/<br>/g, '\n'),
      '',
      '## 风险清单',
      ...((en.risks || []).map(r => `- ${r}`)),
      '',
      '## 我的复盘',
      journal ? journal.text : '（当日未写日记）',
      '',
      '> 由深脉 DeepPulse 自动整理 · 仅供研究参考，不构成投资建议',
    ];
    downloadText(`深脉复盘_${date}.md`, lines.join('\n'), 'text/markdown');
    toast('复盘已导出');
  });

  // 让 DeepSeek 生成复盘（基于今日市场上下文 + 引擎数据）
  container.querySelector('#st-ai-review').addEventListener('click', async () => {
    const btn = container.querySelector('#st-ai-review');
    const box = container.querySelector('#st-jtext');
    btn.disabled = true;
    const prev = btn.textContent;
    btn.textContent = '生成中，完成后自动回填…';
    try {
      const PROMPT = '请基于今日市场上下文，为我生成一份结构化的 A 股情绪周期复盘，包含：'
        + '①今日情绪概况（温度/阶段/关键数据）②主线与梯队 ③风险点 ④明日策略与仓位。'
        + '用 Markdown，300 字以内，直接输出复盘正文。';
      const generated = await generateReviewBody(PROMPT, 'strategy-today-review-fill');
      box.value = generated.reply;
      toast(generated.source === 'harness'
        ? 'DeepSeek 已回填复盘，检查后可保存'
        : '复盘已由独立版大脑生成，检查后可保存');
    } catch (e) {
      toast('生成失败：' + e.message, 'err');
    } finally {
      btn.disabled = false;
      btn.textContent = prev;
    }
  });

  container.querySelector('#st-jlist').addEventListener('click', e => {
    const btn = e.target.closest('[data-del]');
    if (btn) {
      deleteJournalEntry(btn.dataset.date);
      renderJournal(container);
      toast('已删除该日记');
    }
  });

  // 引擎调教：草稿预览 → 明确应用，避免误触后立即改变全局引擎。
  const tuneEl = container.querySelector('#st-tune');
  const tuneHint = container.querySelector('#st-tune-hint');
  const applyBtn = container.querySelector('#st-tune-apply');
  const discardBtn = container.querySelector('#st-tune-discard');
  const INDICATOR_NAMES = {
    zt: '涨停家数', dt: '跌停家数', zb: '炸板率', height: '最高连板', lb_count: '连板家数',
    zt_idx: '昨日涨停指数', lb_idx: '昨日连板指数', breadth: '上涨家数占比',
    volume: '量能比(20日)', flow: '主力净流入', trend: '上证vs MA20',
  };
  let tuneData = null;
  const isTuneDirty = () => tuneData && tuneData.order.some(k =>
    Math.abs((tuneData.draft[k] ?? 0) - (tuneData.active[k] ?? 0)) > 0.001);
  const previewTemperature = () => {
    const signals = (lastEm && lastEm.engine && lastEm.engine.signals) || [];
    let weighted = 0;
    let totalWeight = 0;
    signals.forEach(signal => {
      const key = signal.key;
      const score = Number(signal.score);
      const weight = Number(tuneData && tuneData.draft[key]);
      if ((signal.avail === false || signal.available === false) || !Number.isFinite(score) || !Number.isFinite(weight) || weight <= 0) return;
      weighted += score * weight;
      totalWeight += weight;
    });
    if (!totalWeight) return null;
    return Math.max(0, Math.min(100, 50 + 2.5 * weighted / totalWeight));
  };
  const updateTuneState = () => {
    const dirty = isTuneDirty();
    applyBtn.disabled = !dirty;
    discardBtn.disabled = !dirty;
    const preview = previewTemperature();
    const current = lastEm && lastEm.engine && lastEm.engine.temp;
    tuneHint.textContent = dirty
      ? `草稿预览：${preview == null ? '--' : preview.toFixed(1)}°（当前 ${current ?? '--'}°），尚未应用`
      : '当前为已应用权重';
  };
  const renderTune = () => {
    if (!tuneData) { tuneEl.innerHTML = '<div class="empty" style="padding:14px">权重数据加载中…</div>'; return; }
    const { draft, defaults, order } = tuneData;
    tuneEl.innerHTML = order.map(k => {
      const w = draft[k] ?? defaults[k];
      const d = defaults[k];
      const changed = Math.abs(w - d) > 0.001;
      return `<div class="tune-row ${changed ? 'changed' : ''}">
        <span class="tr-name">${esc(INDICATOR_NAMES[k] || k)}</span>
        <input type="range" min="0" max="3" step="0.1" value="${w}" data-key="${k}" class="tr-slider">
        <span class="tr-val num">${w.toFixed(1)}</span>
        <span class="tr-default">默认 ${d.toFixed(1)}</span>
      </div>`;
    }).join('');
    updateTuneState();
  };
  tuneEl.addEventListener('input', e => {
    const slider = e.target.closest('.tr-slider');
    if (!slider || !tuneData) return;
    const k = slider.dataset.key;
    tuneData.draft[k] = parseFloat(slider.value);
    slider.closest('.tune-row').querySelector('.tr-val').textContent = slider.value;
    slider.closest('.tune-row').classList.toggle('changed', Math.abs(parseFloat(slider.value) - tuneData.defaults[k]) > 0.001);
    updateTuneState();
  });
  applyBtn.addEventListener('click', async () => {
    try {
      applyBtn.disabled = true;
      await api.saveWeights(tuneData.draft);
      tuneData.active = { ...tuneData.draft };
      renderTune();
      tuneHint.textContent = '✓ 权重已应用；刷新数据后可查看正式温度';
    } catch (e) { tuneHint.textContent = '应用失败：' + e.message; updateTuneState(); }
  });
  discardBtn.addEventListener('click', () => {
    tuneData.draft = { ...tuneData.active };
    renderTune();
  });
  container.querySelector('#st-tune-reset').addEventListener('click', () => {
    tuneData.draft = { ...tuneData.defaults };
    renderTune();
  });
  api.weights().then(d => {
    tuneData = { ...d, active: { ...d.weights }, draft: { ...d.weights } };
    renderTune();
  }).catch(() => {
    tuneEl.innerHTML = '<div class="empty" style="padding:14px">权重接口暂不可用</div>';
  });

  bus.addEventListener('journal', () => renderJournal(container));
  renderJournal(container);

  // ---- 复盘日历 ----
  const calEl = container.querySelector('#st-cal');
  const calTitle = container.querySelector('#st-cal-title');
  const calLabel = container.querySelector('#st-cal-day-label');
  const calBadge = container.querySelector('#st-cal-day-badge');
  const calText = container.querySelector('#st-cal-text');
  const calNote = container.querySelector('#st-cal-note');
  const calMonth = new Date();
  let selDate = jd;

  const renderCal = () => {
    const y = calMonth.getFullYear(), m = calMonth.getMonth();
    calTitle.textContent = `${y} 年 ${m + 1} 月`;
    const startDow = new Date(y, m, 1).getDay();
    const days = new Date(y, m + 1, 0).getDate();
    const snapByDate = {};
    ((lastEm && lastEm.history) || []).forEach(s => { snapByDate[s.date] = s; });
    const jByDate = {};
    loadJournal().forEach(j => { jByDate[j.date] = true; });
    const cells = [];
    for (let i = 0; i < startDow; i++) cells.push('<div class="cal-cell empty"></div>');
    for (let d = 1; d <= days; d++) {
      const dstr = `${y}-${String(m + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
      const snap = snapByDate[dstr];
      const cls = ['cal-cell'];
      if (snap && snap.color) cls.push('ph-' + snap.color);
      if (jByDate[dstr]) cls.push('has-journal');
      if (dstr === selDate) cls.push('sel');
      if (dstr === jd) cls.push('today');
      cells.push(`<div class="${cls.join(' ')}" data-date="${dstr}">
        <span class="cal-d">${d}</span>
        ${snap ? `<span class="cal-temp num">${snap.temp}°</span>` : ''}
        ${jByDate[dstr] ? '<span class="cal-dot">📔</span>' : ''}
      </div>`);
    }
    calEl.innerHTML = cells.join('');
    loadDayPanel();
  };
  const loadDayPanel = () => {
    const snapByDate = {};
    ((lastEm && lastEm.history) || []).forEach(s => { snapByDate[s.date] = s; });
    const snap = snapByDate[selDate];
    const entry = loadJournal().find(e => e.date === selDate);
    const dow = '日一二三四五六'[new Date(selDate + 'T00:00:00').getDay()];
    calLabel.textContent = `${selDate} 周${dow}`;
    calBadge.innerHTML = snap
      ? `<span class="badge ${esc(snap.color || 'gray')}">${snap.temp ?? '--'}° · ${esc(snap.phase || '--')}</span>`
      : '<span class="badge gray">无情绪快照</span>';
    calText.value = entry ? entry.text : '';
  };
  calEl.addEventListener('click', e => {
    const cell = e.target.closest('.cal-cell[data-date]');
    if (!cell) return;
    selDate = cell.dataset.date;
    renderCal();
  });
  container.querySelector('#st-cal-prev').addEventListener('click', () => {
    calMonth.setMonth(calMonth.getMonth() - 1);
    renderCal();
  });
  container.querySelector('#st-cal-next').addEventListener('click', () => {
    calMonth.setMonth(calMonth.getMonth() + 1);
    renderCal();
  });
  container.querySelector('#st-cal-save').addEventListener('click', () => {
    const text = calText.value.trim();
    if (!text) { toast('先写点什么再保存', 'err'); return; }
    saveJournalEntry(selDate, text);
    renderCal();
    toast(`已保存 ${selDate} 的复盘`);
  });
  container.querySelector('#st-cal-del').addEventListener('click', () => {
    deleteJournalEntry(selDate);
    calText.value = '';
    renderCal();
    toast('已删除该日复盘');
  });
  container.querySelector('#st-cal-ai').addEventListener('click', async () => {
    const btn = container.querySelector('#st-cal-ai');
    const snapByDate = {};
    ((lastEm && lastEm.history) || []).forEach(s => { snapByDate[s.date] = s; });
    const snap = snapByDate[selDate];
    if (!snap) { toast('该日无情绪快照（收盘后自动记录），无法生成', 'err'); return; }
    const r = snap.raw || {};
    btn.disabled = true;
    const prev = btn.textContent;
    btn.textContent = '生成中，完成后自动回填…';
    try {
      const prompt = `请基于以下 ${selDate} 的情绪快照数据，生成一份结构化的 A 股情绪周期复盘`
        + `（①概况 ②主线与梯队 ③风险 ④次日策略，Markdown，250字内，直接输出正文）：`
        + `温度 ${snap.temp}°（${snap.phase}），涨停 ${r.zt} 家，跌停 ${r.dt} 家，`
        + `炸板率 ${r.zb_rate != null ? (r.zb_rate * 100).toFixed(1) : '-'}%，最高 ${r.height} 连板，连板 ${r.lb_count} 家，`
        + `昨涨停指数 ${r.zt_idx_pct ?? '-'}%，昨连板指数 ${r.lb_idx_pct ?? '-'}%，`
        + `上涨 ${r.up} / 下跌 ${r.down}，成交 ${r.turnover_yi ?? '-'} 亿，主力净流入 ${r.flow_yi ?? '-'} 亿，`
        + `上证 vs MA20 ${r.trend_pct ?? '-'}%，研究仓位区间 ${(snap.advice || {}).position || '-'}`;
      const generated = await generateReviewBody(prompt, 'strategy-calendar-review-fill');
      calText.value = generated.reply;
      toast(generated.source === 'harness'
        ? 'DeepSeek 已回填该日复盘，检查后可保存'
        : '复盘已由独立版大脑生成，检查后可保存');
    } catch (e) {
      toast('生成失败：' + e.message, 'err');
    } finally {
      btn.disabled = false;
      btn.textContent = prev;
    }
  });
  calNote.textContent = '格子颜色 = 当日情绪阶段（蓝冰点/青修复/金发酵/红高潮/紫亢奋），📔 = 你写过的复盘。点击任意一天，可补写或让 DeepSeek 基于当日快照生成复盘；生成期间可查看会话，正文完整后会自动回填。';
  calRender = renderCal;
  renderCal();
}

function todayStr() {
  const d = new Date();
  const p = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

function renderJournal(container) {
  const list = loadJournal();
  const el = container.querySelector('#st-jlist');
  if (list.length) {
    el.innerHTML = list.map(e => `
      <div class="journal-entry">
        <div class="je-head"><span>${esc(e.date)}</span><button class="btn sm ghost" data-del="${esc(e.date)}" style="height:22px;font-size:10.5px">删除</button></div>
        <div class="je-body">${esc(e.text)}</div>
      </div>`).join('');
  } else {
    emptyState(el, '📔', '还没有日记', '收盘后用模板写复盘，或点「🤖 让 DeepSeek 生成复盘」——让记忆长出年轮。');
  }
}

export async function refresh(container, data) {
  init(container);
  const em = data.emotion;
  if (!em) return;
  lastEm = em;
  if (calRender) calRender();
  const engine = em.engine || {};
  const raw = engine.raw || {};
  const adv = engine.advice || {};

  container.querySelector('#st-diag-date').textContent = em.date || '--';
  const color = PHASE_COLORS[engine.color] || '#e9eef8';
  container.querySelector('#st-diag').innerHTML = `
    <div style="display:flex;gap:18px;align-items:center;flex-wrap:wrap;margin-bottom:12px">
      <div style="font-size:44px;font-weight:800;line-height:1;color:${color};text-shadow:0 0 28px ${color}55" class="num">${engine.temp ?? '--'}<span style="font-size:16px;color:var(--text-3)">°</span></div>
      <div>
        <div><span class="badge lg ${esc(engine.color || 'gray')}">${esc(engine.phase || '--')}</span></div>
        <div style="font-size:12px;color:var(--text-2);margin-top:5px">研究仓位区间 <b style="font-size:17px;color:${color}">${esc(adv.position || '--')}</b> · ${esc(adv.style || '--')}</div>
        <div style="font-size:11px;color:var(--text-3);margin-top:5px">数据覆盖 ${engine.coverage ?? 0}% · 数据质量分 ${engine.confidence ?? 0} · 信号一致度 ${engine.consensus ?? 0}%</div>
      </div>
    </div>
    <div class="advice-card">
      <div class="advice-title">引擎诊断</div>
      <div class="advice-desc">${esc(engine.narrative || '--')}</div>
    </div>
    <div style="margin-top:10px;font-size:11.5px;color:var(--text-3);line-height:1.7">
      ${esc(adv.phase_desc || '')}
    </div>
    <div style="margin-top:10px;display:flex;gap:7px;flex-wrap:wrap">
      ${(adv.scenarios || []).map(s => `<span class="badge ${s.active ? 'amber' : 'gray'}" title="${esc(s.condition)} · ${esc(s.action)}">${s.active ? '● ' : ''}${esc(s.name)}</span>`).join('')}
    </div>`;

  // 仓位矩阵高亮
  container.querySelectorAll('#st-matrix .pos-cell').forEach(c => {
    c.classList.toggle('hit', c.dataset.phase === engine.phase);
  });

  // 风险清单
  container.querySelector('#st-risks').innerHTML = (engine.risks || []).map(r =>
    `<div class="flag warn"><span class="f-dot"></span><span>${esc(r)}</span></div>`).join('') ||
    '<div class="empty">无风险信号</div>';

  // 贡献榜
  const sig = (engine.signals || []).filter(s => s.avail).sort((a, b) => b.contribution - a.contribution);
  container.querySelector('#st-contrib').innerHTML = sig.slice(0, 6).map(s => `
    <div style="display:flex;align-items:center;gap:10px;padding:7px 2px;border-bottom:1px solid rgba(148,163,184,.06)">
      <span style="font-size:12px;color:var(--text-2);flex:0 0 108px">${esc(s.name)}</span>
      <div style="flex:1;height:7px;border-radius:4px;background:rgba(148,163,184,.1);position:relative">
        <div style="position:absolute;top:0;bottom:0;left:50%;width:1px;background:rgba(148,163,184,.3)"></div>
        <div style="position:absolute;top:0;bottom:0;${s.contribution >= 0 ? 'left:50%' : 'right:50%'};width:${Math.min(50, Math.abs(s.contribution) / 40 * 50)}%;border-radius:4px;background:${s.contribution >= 0 ? 'linear-gradient(90deg,#f0b90b,#f6465d)' : 'linear-gradient(90deg,#4f8cff,#22d3ee)'}"></div>
      </div>
      <span class="num" style="flex:0 0 46px;text-align:right;font-weight:700;color:${s.contribution >= 0 ? 'var(--amber)' : 'var(--accent)'}">${s.contribution > 0 ? '+' : ''}${s.contribution}</span>
    </div>`).join('');
}
