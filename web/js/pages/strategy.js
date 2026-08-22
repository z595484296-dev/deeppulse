/* 深脉 DeepPulse — 策略页（情绪周期策略引擎 · 复盘与日记） */

import { api } from '../api.js?v=1.35.0';
import { loadJournal, saveJournalEntry, deleteJournalEntry, bus, state } from '../store.js?v=1.35.0';
import { esc, toast, PHASE_COLORS, emptyState, downloadText } from '../util.js?v=1.35.0';
import { EMBEDDED, generateWithDeepSeek } from '../bridge.js?v=1.35.0';

let built = false;
let lastEm = null;   // 最近一次情绪数据（导出复盘/日历用）
let calRender = null; // 复盘日历渲染句柄（refresh 时重绘）
let hypothesisData = null;
let researchMemoryData = null;
let researchWorkflowData = null;
let researchWorkflowPreview = null;
let activeWorkflowTemplate = null;
let activeWorkflowOrigin = null;
let researchSuggestionData = null;
let activeResearchSuggestion = null;
let suggestionDraftBackup = null;
let pendingWorkflowFocus = '';
let researchWatchPreview = null;
let researchWatchTarget = null;

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

      <section class="card span-12 research-suggestion-inbox" aria-labelledby="st-suggestion-title">
        <div class="card-head research-suggestion-head">
          <div><div class="card-title" id="st-suggestion-title">📥 主动研究建议</div><div class="card-sub">基于你的明确关注与研究记录 · 默认只进入这里</div></div>
          <div class="research-suggestion-summary"><span id="st-suggestion-summary">正在整理…</span><button type="button" class="btn sm ghost" id="st-suggestion-refresh">重新整理</button></div>
        </div>
        <p class="hypothesis-boundary">建议只提出值得验证的问题，不代表看多、看空或买卖判断。载入只填写草稿，不会访问来源、预览权限、创建或运行流程。</p>
        <div class="research-suggestion-layout">
          <div class="research-suggestion-list" id="st-suggestion-list"><div class="empty compact">正在读取主动研究建议…</div></div>
          <article class="research-suggestion-detail" id="st-suggestion-detail" tabindex="-1"><div class="empty compact">选择一条建议，查看它为什么出现、还缺什么证据。</div></article>
        </div>
      </section>

      <section class="card span-12 workflow-studio" aria-labelledby="st-workflow-title">
        <div class="card-head workflow-head">
          <div><div class="card-title" id="st-workflow-title">🧭 研究流程</div><div class="card-sub">先预览范围与权限 · 再创建、执行和复盘</div></div>
          <div class="workflow-summary" id="st-workflow-summary">正在读取…</div>
        </div>
        <p class="hypothesis-boundary">把研究对象、问题、证据源、复盘时点和输出方式组合成一个可控任务。预览不会访问外部数据；DeepSeek 只能建议拆解，不能替你确认权限。</p>
        <div class="workflow-origin-banner" id="st-wf-origin-banner" hidden></div>
        <div class="workflow-builder">
          <form id="st-workflow-form" class="workflow-form" autocomplete="off">
            <div class="workflow-field workflow-title-field"><label for="st-wf-title">流程名称</label><input id="st-wf-title" maxlength="120" placeholder="例如：工业富联算力热点验证"></div>
            <div class="workflow-field"><label for="st-wf-target-type">研究对象</label><div class="workflow-inline"><select id="st-wf-target-type"><option value="stock">股票</option><option value="market">市场</option><option value="theme">主题</option><option value="custom">自定义</option></select><input id="st-wf-code" maxlength="6" inputmode="numeric" placeholder="601138"><input id="st-wf-name" maxlength="80" placeholder="工业富联"></div></div>
            <div class="workflow-field"><label for="st-wf-question">我想验证的问题</label><textarea id="st-wf-question" maxlength="1200" placeholder="例如：近期算力热点是否获得公司公告、行情结构和宏观背景的共同支持？"></textarea></div>
            <fieldset class="workflow-options"><legend>证据来源（创建后仍需手动执行）</legend>
              <label><input type="checkbox" name="wf-source" value="official_disclosures" checked><span><b>官方披露</b><small>巨潮公告原文</small></span></label>
              <label><input type="checkbox" name="wf-source" value="market_quote" checked><span><b>公开行情</b><small>通达信优先、双备援</small></span></label>
              <label><input type="checkbox" name="wf-source" value="tdx_local"><span><b>通达信复核</b><small>本机只读</small></span></label>
              <label><input type="checkbox" name="wf-source" value="akshare_macro" checked><span><b>AKShare</b><small>宏观与跨市场背景</small></span></label>
              <label><input type="checkbox" name="wf-source" value="event_news"><span><b>事件快讯</b><small>需已开启事件雷达</small></span></label>
            </fieldset>
            <fieldset class="workflow-options compact"><legend>输出方式</legend>
              <label><input type="checkbox" name="wf-output" value="dashboard_card" checked><span><b>研究卡片</b></span></label>
              <label><input type="checkbox" name="wf-output" value="review_note" checked><span><b>到期复盘</b></span></label>
              <label><input type="checkbox" name="wf-output" value="deepseek_brief" checked><span><b>DeepSeek 简报</b></span></label>
            </fieldset>
            <div class="workflow-settings">
              <label>类型<select id="st-wf-kind"><option value="one_off">一次性任务</option><option value="template">可复用模板</option></select></label>
              <label>复盘窗口<select id="st-wf-days"><option value="1">1 个工作日</option><option value="3">3 个工作日</option><option value="5" selected>5 个工作日</option><option value="10">10 个工作日</option><option value="20">20 个工作日</option></select></label>
              <label class="workflow-check"><input type="checkbox" id="st-wf-reminder" checked><span>到期写入本机提醒</span></label>
            </div>
            <div class="workflow-actions"><button type="button" class="btn primary" id="st-wf-preview">预览流程与权限</button><button type="button" class="btn" id="st-wf-decompose">让 DeepSeek 帮我拆解</button><button type="button" class="btn ghost" id="st-wf-clear">清空草稿</button></div>
            <div class="workflow-ai-suggestion" id="st-wf-suggestion" hidden></div>
          </form>
          <div class="workflow-preview" id="st-wf-preview-panel"><div class="empty compact">填写研究问题后先预览。这里会显示执行步骤、数据源状态和每项待确认权限。</div></div>
        </div>
        <div class="workflow-list-head"><b>已创建流程</b><span>执行只读取已冻结的来源；暂停和复制不会丢失历史</span></div>
        <div id="st-workflow-list"><div class="empty">正在读取研究流程…</div></div>
      </section>

      <dialog class="research-watch-dialog" id="st-watch-dialog" aria-labelledby="st-watch-dialog-title">
        <div class="research-watch-dialog-head"><div><h2 id="st-watch-dialog-title">开启研究值守</h2><p>仅重复读取这条流程已经确认的来源；无实质变化不提醒。</p></div><button type="button" class="icon-btn" data-watch-close aria-label="关闭研究值守设置">×</button></div>
        <div class="research-watch-dialog-body">
          <div class="research-watch-scope" id="st-watch-scope"></div>
          <div class="research-watch-settings">
            <label>检查频率<select id="st-watch-frequency"><option value="close">每个交易日收盘后</option><option value="daily">每个工作日一次</option></select></label>
            <label>自动结束日期<input type="date" id="st-watch-expires"></label>
            <label>变化提醒<select id="st-watch-delivery"><option value="center_only">只进入提醒中心</option><option value="digest">摘要并按已授权终端送达</option></select></label>
          </div>
          <p class="research-watch-boundary">值守默认关闭，按流程单独授权；不会新增来源、自动调用 DeepSeek、判断利好利空、修改研究结论或连接交易账户。</p>
          <div class="research-watch-preview" id="st-watch-preview"><div class="empty compact">先预览持续访问范围和到期时间，再逐项确认。</div></div>
        </div>
        <div class="research-watch-dialog-actions"><button type="button" class="btn ghost" data-watch-close>取消</button><button type="button" class="btn primary" id="st-watch-preview-btn">预览值守权限</button></div>
      </dialog>

      <section class="card span-12 hypothesis-lab" aria-labelledby="st-hyp-title">
        <div class="card-head">
          <div><div class="card-title" id="st-hyp-title">🧪 研究假设</div><div class="card-sub">保存当时判断 · 预设反证 · 到期主动复盘</div></div>
          <div class="hypothesis-summary" id="st-hyp-summary">正在读取…</div>
        </div>
        <p class="hypothesis-boundary">假设保存后不会随行情自动改写。到期提醒只检查本机时间，不会扩大事件数据授权；最终结论必须由你确认。</p>
        <div id="st-hyp-list"><div class="empty">正在读取研究假设…</div></div>
      </section>

      <section class="card span-12 research-memory-lab" aria-labelledby="st-memory-title">
        <div class="card-head research-memory-head">
          <div><div class="card-title" id="st-memory-title">🧠 研究记忆</div><div class="card-sub">只回看你明确确认的结论 · 相似结构提醒可随时关闭</div></div>
          <label class="memory-switch"><input type="checkbox" id="st-memory-enabled"><span>在新任务中提示相似经验</span></label>
        </div>
        <div class="research-memory-summary" id="st-memory-summary">正在读取…</div>
        <div class="research-memory-patterns" id="st-memory-patterns"></div>
        <div id="st-memory-list"><div class="empty">正在整理研究记忆…</div></div>
        <p class="hypothesis-boundary" id="st-memory-boundary">不统计交易胜率，不根据收益倒推因果，不会自动修改策略。</p>
      </section>

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

  const workflowForm = container.querySelector('#st-workflow-form');
  const workflowPreviewPanel = container.querySelector('#st-wf-preview-panel');
  container.querySelector('#st-suggestion-refresh').addEventListener('click', async e => {
    const button = e.currentTarget;
    button.disabled = true;
    try {
      const result = await api.mutateResearchSuggestion('refresh');
      researchSuggestionData = result.suggestions;
      state.researchSuggestions = researchSuggestionData;
      renderResearchSuggestions(container);
      toast('主动研究建议已按当前记录重新整理');
    } catch (error) { toast(error.message || '研究建议整理失败', 'err'); }
    finally { button.disabled = false; }
  });
  container.querySelector('#st-suggestion-list').addEventListener('click', e => {
    const button = e.target.closest('[data-suggestion-id]');
    if (!button) return;
    renderResearchSuggestionDetail(container, button.dataset.suggestionId);
  });
  container.querySelector('#st-suggestion-detail').addEventListener('click', async e => {
    const button = e.target.closest('[data-suggestion-action]');
    if (!button) return;
    const suggestionId = button.dataset.suggestionId;
    const action = button.dataset.suggestionAction;
    button.disabled = true;
    try {
      if (action === 'prepare') {
        const result = await api.mutateResearchSuggestion('prepare', { suggestionId });
        applyPreparedSuggestion(container, result);
        toast('建议已载入草稿；尚未预览、授权、创建或执行');
        return;
      }
      if (action === 'open-workflow') {
        focusResearchWorkflow(container, button.dataset.workflowId);
        return;
      }
      const result = await api.mutateResearchSuggestion(action, { suggestionId });
      researchSuggestionData = result.suggestions;
      state.researchSuggestions = researchSuggestionData;
      renderResearchSuggestions(container);
      toast(action === 'dismiss' ? '这条建议已忽略，7 天后若依据仍存在可再次出现' : '建议已恢复到待处理');
    } catch (error) { toast(error.message || '研究建议操作失败', 'err'); }
    finally { button.disabled = false; }
  });
  container.querySelector('#st-wf-origin-banner').addEventListener('click', e => {
    if (!e.target.closest('[data-suggestion-undo]') || !suggestionDraftBackup) return;
    fillSuggestionDraft(container, suggestionDraftBackup);
    suggestionDraftBackup = null;
    activeResearchSuggestion = null;
    researchWorkflowPreview = null;
    renderWorkflowPreview(container);
    renderWorkflowOrigin(container);
    toast('已恢复载入建议前的研究草稿');
  });
  workflowForm.addEventListener('input', e => {
    if (!e.isTrusted || !activeResearchSuggestion) return;
    activeResearchSuggestion = null;
    renderWorkflowOrigin(container, true);
  });
  const syncWorkflowTargetFields = () => {
    const stock = container.querySelector('#st-wf-target-type').value === 'stock';
    const code = container.querySelector('#st-wf-code');
    code.disabled = !stock;
    code.placeholder = stock ? '601138' : '非股票对象无需代码';
  };
  container.querySelector('#st-wf-target-type').addEventListener('change', syncWorkflowTargetFields);
  ['#st-wf-code', '#st-wf-name'].forEach(selector => {
    container.querySelector(selector).addEventListener('input', () => renderWorkflowTemplateParameters(container));
  });
  ['#st-wf-title', '#st-wf-question'].forEach(selector => {
    container.querySelector(selector).addEventListener('input', () => { activeWorkflowTemplate = null; });
  });
  syncWorkflowTargetFields();
  container.querySelector('#st-wf-preview').addEventListener('click', async e => {
    const button = e.currentTarget;
    button.disabled = true;
    try {
      const result = await api.mutateResearchWorkflow('preview', {
        draft: workflowDraft(container), suggestionId: activeResearchSuggestion?.id || '',
      });
      researchWorkflowPreview = result.preview;
      if (result.suggestions) {
        researchSuggestionData = result.suggestions;
        state.researchSuggestions = researchSuggestionData;
        activeResearchSuggestion = (researchSuggestionData.items || [])
          .find(row => row.id === activeResearchSuggestion?.id) || activeResearchSuggestion;
        renderResearchSuggestions(container);
        renderWorkflowOrigin(container);
      }
      renderWorkflowPreview(container);
      toast(researchWorkflowPreview.ready ? '流程预览已生成；请逐项确认权限' : '请先解决预览中的缺失项', researchWorkflowPreview.ready ? 'ok' : 'err');
    } catch (error) {
      toast(error.message || '流程预览失败', 'err');
    } finally { button.disabled = false; }
  });
  container.querySelector('#st-wf-decompose').addEventListener('click', async e => {
    const button = e.currentTarget;
    const suggestion = container.querySelector('#st-wf-suggestion');
    const draft = workflowDraft(container);
    if ((draft.question || '').trim().length < 4) { toast('先写下想验证的问题', 'err'); return; }
    button.disabled = true;
    const previous = button.textContent;
    button.textContent = 'DeepSeek 正在拆解…';
    try {
      const prompt = '请把下面的研究草稿拆成 3-5 个可验证子问题，并指出每个子问题需要什么证据、哪些结论不能从现有来源推出。只提供建议，不要替用户新增来源、确认权限、创建提醒或给出买卖指令。草稿：' + JSON.stringify(draft);
      const generated = await generateReviewBody(prompt, 'research-workflow-decompose');
      suggestion.hidden = false;
      suggestion.innerHTML = `<b>DeepSeek 拆解建议（不会自动改草稿）</b><pre>${esc(generated.reply)}</pre>`;
      toast('拆解建议已返回；你决定是否修改草稿');
    } catch (error) {
      toast('拆解失败：' + error.message, 'err');
    } finally { button.disabled = false; button.textContent = previous; }
  });
  container.querySelector('#st-wf-clear').addEventListener('click', () => {
    workflowForm.reset();
    container.querySelector('#st-wf-days').value = '5';
    container.querySelector('#st-wf-kind').value = 'one_off';
    researchWorkflowPreview = null;
    activeWorkflowTemplate = null;
    activeWorkflowOrigin = null;
    activeResearchSuggestion = null;
    suggestionDraftBackup = null;
    workflowPreviewPanel.innerHTML = '<div class="empty compact">草稿已清空。重新填写后再预览，不会影响已创建流程。</div>';
    container.querySelector('#st-wf-suggestion').hidden = true;
    renderWorkflowOrigin(container);
    syncWorkflowTargetFields();
  });
  workflowPreviewPanel.addEventListener('change', () => updateWorkflowConfirmState(container));
  workflowPreviewPanel.addEventListener('click', async e => {
    const button = e.target.closest('[data-workflow-confirm]');
    if (!button || !researchWorkflowPreview) return;
    const confirmations = [...workflowPreviewPanel.querySelectorAll('[data-wf-permission]:checked')].map(input => input.value);
    if (workflowPreviewPanel.querySelector('[data-wf-confirm-create]:checked')) confirmations.push('confirm:create');
    button.disabled = true;
    try {
      const result = await api.mutateResearchWorkflow('confirm', {
        draft: workflowDraft(container), previewId: researchWorkflowPreview.previewId,
        confirmations,
        originWorkflowId: activeWorkflowOrigin?.workflowId || '',
        originKind: activeWorkflowOrigin?.kind || '',
        suggestionId: activeResearchSuggestion?.id || '',
      });
      researchWorkflowData = result.workflows;
      state.researchWorkflows = researchWorkflowData;
      researchWorkflowPreview = null;
      activeWorkflowOrigin = null;
      activeResearchSuggestion = null;
      suggestionDraftBackup = null;
      if (result.suggestions) {
        researchSuggestionData = result.suggestions;
        state.researchSuggestions = researchSuggestionData;
        renderResearchSuggestions(container);
      }
      renderWorkflowOrigin(container);
      renderWorkflowPreview(container);
      renderResearchWorkflows(container);
      bus.dispatchEvent(new CustomEvent('research-workflows', { detail: researchWorkflowData }));
      toast(result.created.kind === 'template' ? '研究模板已创建' : '研究流程已创建；来源尚未执行');
    } catch (error) {
      button.disabled = false;
      toast(error.message || '创建研究流程失败', 'err');
    }
  });
  container.querySelector('#st-workflow-list').addEventListener('click', async e => {
    const button = e.target.closest('[data-wf-action]');
    if (!button) return;
    const workflow = (researchWorkflowData?.items || []).find(row => row.id === button.dataset.wfId);
    if (!workflow) return;
    const action = button.dataset.wfAction;
    if (action === 'watch-setup') {
      openResearchWatchDialog(container, workflow);
      return;
    }
    if (action === 'watch-change') {
      const comparison = button.closest('.workflow-item')?.querySelector('.workflow-comparison');
      if (comparison) comparison.scrollIntoView({ behavior: 'smooth', block: 'center' });
      return;
    }
    if (action === 'ask') {
      document.dispatchEvent(new CustomEvent('ask-research-workflow', { detail: { workflow } }));
      return;
    }
    if (action === 'copy') {
      fillWorkflowDraft(container, workflow, workflow.kind === 'template');
      researchWorkflowPreview = null;
      renderWorkflowPreview(container);
      container.querySelector('#st-workflow-title').scrollIntoView({ behavior: 'smooth', block: 'start' });
      toast(workflow.kind === 'template' ? '模板方法已载入；请填写新标的并重新预览授权，旧证据不会继承' : '已复制为新草稿；原流程不变');
      return;
    }
    if (action === 'review-draft') {
      const card = workflow.latestRun?.resultCard || (workflow.runs || []).slice(-1)[0]?.resultCard;
      if (!card?.reviewDraft) return;
      const calendarText = container.querySelector('#st-cal-text');
      const journalText = container.querySelector('#st-jtext');
      if (calendarText) calendarText.value = card.reviewDraft;
      if (journalText) journalText.value = card.reviewDraft;
      container.querySelector('#st-cal-panel')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      toast('研究结果已填入复盘草稿；检查并点击保存后才会写入');
      return;
    }
    button.disabled = true;
    const oldText = button.textContent;
    if (['run', 'watch_check'].includes(action)) button.textContent = '正在读取来源…';
    try {
      const result = await api.mutateResearchWorkflow(action, { workflowId: workflow.id });
      researchWorkflowData = result.workflows;
      state.researchWorkflows = researchWorkflowData;
      renderResearchWorkflows(container);
      bus.dispatchEvent(new CustomEvent('research-workflows', { detail: researchWorkflowData }));
      const notices = {
        run: '本次证据读取已记录；系统没有自动生成结论', pause: '流程已暂停', resume: '流程已继续',
        watch_check: result.published ? '检查完成：发现实质变化，已写入提醒中心' : '检查完成：没有实质变化，不会打扰你',
        watch_pause: '研究值守已暂停，来源授权仍保留', watch_resume: '研究值守已恢复',
        watch_stop: '研究值守已结束；重新开启需要再次预览授权', complete: '流程已完成',
      };
      toast(notices[action] || '研究流程已更新', result.published ? 'ok' : undefined);
    } catch (error) {
      button.disabled = false;
      button.textContent = oldText;
      toast(error.message || '研究流程操作失败', 'err');
    }
  });
  const watchDialog = container.querySelector('#st-watch-dialog');
  watchDialog.addEventListener('click', e => {
    if (e.target.closest('[data-watch-close]')) watchDialog.close();
  });
  watchDialog.addEventListener('close', () => {
    researchWatchPreview = null;
    researchWatchTarget = null;
  });
  ['#st-watch-frequency', '#st-watch-expires', '#st-watch-delivery'].forEach(selector => {
    container.querySelector(selector).addEventListener('change', () => {
      researchWatchPreview = null;
      renderResearchWatchPreview(container);
    });
  });
  container.querySelector('#st-watch-preview-btn').addEventListener('click', async e => {
    if (!researchWatchTarget) return;
    const button = e.currentTarget;
    button.disabled = true;
    try {
      const result = await api.mutateResearchWorkflow('watch_preview', {
        workflowId: researchWatchTarget.id, options: researchWatchOptions(container),
      });
      researchWatchPreview = result.preview;
      renderResearchWatchPreview(container);
    } catch (error) { toast(error.message || '值守权限预览失败', 'err'); }
    finally { button.disabled = false; }
  });
  container.querySelector('#st-watch-preview').addEventListener('change', () => updateResearchWatchConfirm(container));
  container.querySelector('#st-watch-preview').addEventListener('click', async e => {
    const button = e.target.closest('[data-watch-confirm]');
    if (!button || !researchWatchPreview || !researchWatchTarget) return;
    const permissions = [...container.querySelectorAll('[data-watch-permission]:checked')].map(input => input.value);
    if (container.querySelector('[data-watch-final]:checked')) permissions.push('confirm:watch');
    button.disabled = true;
    try {
      const result = await api.mutateResearchWorkflow('watch_confirm', {
        workflowId: researchWatchTarget.id, options: researchWatchOptions(container),
        previewId: researchWatchPreview.previewId, confirmations: permissions,
      });
      researchWorkflowData = result.workflows;
      state.researchWorkflows = researchWorkflowData;
      renderResearchWorkflows(container);
      watchDialog.close();
      toast('研究值守已开启；首次检查只建立基线，无变化不会提醒', 'ok');
    } catch (error) { button.disabled = false; toast(error.message || '开启研究值守失败', 'err'); }
  });
  bus.addEventListener('research-workflows', e => {
    researchWorkflowData = e.detail;
    state.researchWorkflows = researchWorkflowData;
    renderResearchWorkflows(container);
  });
  bus.addEventListener('research-suggestions', e => {
    researchSuggestionData = e.detail;
    state.researchSuggestions = researchSuggestionData;
    renderResearchSuggestions(container);
  });
  document.addEventListener('research-suggestion-prepare', e => {
    if (e.detail?.suggestion && e.detail?.draft) applyPreparedSuggestion(container, e.detail);
  });
  document.addEventListener('research-workflow-open', e => {
    focusResearchWorkflow(container, e.detail?.workflowId);
  });
  refreshResearchWorkflows(container);
  refreshResearchSuggestions(container);

  container.querySelector('#st-hyp-list').addEventListener('click', async e => {
    const button = e.target.closest('[data-hyp-action]');
    if (!button) return;
    const hypothesis = (hypothesisData?.items || []).find(row => row.id === button.dataset.hypId);
    if (!hypothesis) return;
    const action = button.dataset.hypAction;
    if (action === 'ask') {
      document.dispatchEvent(new CustomEvent('ask-research-hypothesis', { detail: { hypothesis } }));
      return;
    }
    button.disabled = true;
    try {
      if (action === 'review') {
        const card = button.closest('.hypothesis-item');
        const outcome = card.querySelector('[data-hyp-outcome]').value;
        const note = card.querySelector('[data-hyp-note]').value.trim();
        const falsifierHits = [...card.querySelectorAll('[data-hyp-falsifier]:checked')].map(input => input.value);
        const dataGaps = card.querySelector('[data-hyp-gaps]').value.split(/[\n；]+/).map(value => value.trim()).filter(Boolean);
        if (!note) throw new Error('请先写下你观察到的证据或反证');
        const result = await api.mutateResearchHypothesis('review', { id: hypothesis.id, outcome, note, falsifierHits, dataGaps });
        hypothesisData = result.hypotheses;
        toast('复盘结论已保存；原始假设仍完整保留');
        await refreshResearchMemory(container);
      } else if (action === 'evidence') {
        const result = await api.mutateResearchHypothesis('refresh_evidence', { id: hypothesis.id });
        hypothesisData = result.hypotheses;
        toast(result.added ? `新增 ${result.added} 条候选证据` : '已更新证据时间线；系统不会自动修改结论');
      } else if (action === 'archive') {
        const result = await api.mutateResearchHypothesis('archive', { id: hypothesis.id });
        hypothesisData = result.hypotheses;
        toast('研究假设已归档');
      }
      renderHypotheses(container);
      bus.dispatchEvent(new CustomEvent('research-hypotheses', { detail: hypothesisData }));
    } catch (error) {
      button.disabled = false;
      toast(error.message || '研究假设操作失败', 'err');
    }
  });
  bus.addEventListener('research-hypotheses', e => {
    hypothesisData = e.detail;
    renderHypotheses(container);
  });
  refreshHypotheses(container);

  container.querySelector('#st-memory-enabled').addEventListener('change', async e => {
    e.target.disabled = true;
    try {
      const result = await api.mutateResearchMemory('set_enabled', { enabled: e.target.checked });
      researchMemoryData = result.memory;
      state.researchMemory = researchMemoryData;
      renderResearchMemory(container);
      bus.dispatchEvent(new CustomEvent('research-memory', { detail: researchMemoryData }));
      toast(e.target.checked ? '相似研究结构提醒已开启' : '相似研究结构提醒已关闭；历史记录仍保留');
    } catch (error) {
      e.target.checked = !e.target.checked;
      toast(error.message || '研究记忆设置失败', 'err');
    } finally { e.target.disabled = false; }
  });
  container.querySelector('#st-memory-list').addEventListener('click', async e => {
    const button = e.target.closest('[data-memory-action]');
    if (!button) return;
    const memory = (researchMemoryData?.items || []).find(row => row.id === button.dataset.memoryId);
    if (!memory) return;
    const action = button.dataset.memoryAction;
    if (action === 'ask') {
      document.dispatchEvent(new CustomEvent('ask-research-memory', { detail: { memory } }));
      return;
    }
    button.disabled = true;
    try {
      const payload = { memoryId: memory.id };
      if (action === 'update_lesson') {
        payload.lesson = button.closest('.research-memory-item').querySelector('[data-memory-lesson]').value.trim();
      }
      const result = await api.mutateResearchMemory(action, payload);
      researchMemoryData = result.memory;
      state.researchMemory = researchMemoryData;
      renderResearchMemory(container);
      bus.dispatchEvent(new CustomEvent('research-memory', { detail: researchMemoryData }));
      toast(action === 'hide' ? '这条记忆已隐藏；原始复盘仍保留' : action === 'restore' ? '研究记忆已恢复' : '方法改进已保存');
    } catch (error) {
      button.disabled = false;
      toast(error.message || '研究记忆操作失败', 'err');
    }
  });
  bus.addEventListener('research-memory', e => {
    researchMemoryData = e.detail;
    state.researchMemory = researchMemoryData;
    renderResearchMemory(container);
  });
  refreshResearchMemory(container);

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

function applyPreparedSuggestion(container, result) {
  if (!result?.suggestion || !result?.draft) return;
  suggestionDraftBackup = workflowDraft(container);
  fillSuggestionDraft(container, result.draft);
  activeResearchSuggestion = result.suggestion;
  if (result.suggestions) {
    researchSuggestionData = result.suggestions;
    state.researchSuggestions = researchSuggestionData;
    renderResearchSuggestions(container);
  }
  researchWorkflowPreview = null;
  renderWorkflowPreview(container);
  renderWorkflowOrigin(container);
  container.querySelector('#st-workflow-title')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  container.querySelector('#st-wf-title')?.focus({ preventScroll: true });
}

function fillSuggestionDraft(container, draft) {
  const value = draft || {};
  const target = value.target || {};
  activeWorkflowTemplate = null;
  activeWorkflowOrigin = null;
  container.querySelector('#st-wf-kind').value = value.kind || 'one_off';
  container.querySelector('#st-wf-title').value = value.title || '';
  container.querySelector('#st-wf-target-type').value = target.type || 'stock';
  container.querySelector('#st-wf-code').value = target.code || '';
  container.querySelector('#st-wf-name').value = target.name || '';
  container.querySelector('#st-wf-question').value = value.question || '';
  container.querySelector('#st-wf-days').value = String(value.reviewDays || 5);
  container.querySelector('#st-wf-reminder').checked = value.reminderEnabled === true;
  const sources = new Set(value.sources || []);
  container.querySelectorAll('[name="wf-source"]').forEach(input => { input.checked = sources.has(input.value); });
  const outputs = new Set(value.outputs || []);
  container.querySelectorAll('[name="wf-output"]').forEach(input => { input.checked = outputs.has(input.value); });
  container.querySelector('#st-wf-target-type').dispatchEvent(new Event('change'));
  container.querySelector('#st-wf-suggestion').hidden = true;
}

function renderWorkflowOrigin(container, edited = false) {
  const root = container.querySelector('#st-wf-origin-banner');
  if (!root) return;
  if (!activeResearchSuggestion && !suggestionDraftBackup) {
    root.hidden = true;
    root.innerHTML = '';
    return;
  }
  root.hidden = false;
  const title = activeResearchSuggestion?.title || '已载入的主动研究建议';
  const stage = activeResearchSuggestion?.journey?.stage;
  const stageLabel = stage === 'previewed' ? '已完成范围预览，尚未确认创建'
    : stage === 'drafted' ? '草稿已载入，尚未预览或授权' : '尚未预览或授权';
  root.innerHTML = `<div><b>${edited ? '已编辑为独立草稿' : '研究接力进行中'}</b><span>${esc(title)} · ${edited ? '创建后不会自动关闭原建议' : stageLabel}</span></div><button type="button" class="btn sm ghost" data-suggestion-undo>撤销载入</button>`;
}

async function refreshResearchSuggestions(container) {
  try {
    researchSuggestionData = await api.researchSuggestions();
    state.researchSuggestions = researchSuggestionData;
    renderResearchSuggestions(container);
  } catch {
    container.querySelector('#st-suggestion-list').innerHTML = '<div class="empty compact">主动建议暂时不可用，不影响你手动创建研究流程。</div>';
  }
}

function renderResearchSuggestions(container) {
  const root = container.querySelector('#st-suggestion-list');
  const detail = container.querySelector('#st-suggestion-detail');
  if (!root || !researchSuggestionData) return;
  const summary = researchSuggestionData.summary || {};
  container.querySelector('#st-suggestion-summary').textContent = `待处理 ${summary.pending || 0} · 已忽略 ${summary.dismissed || 0} · 已转流程 ${summary.accepted || 0}`;
  const pending = (researchSuggestionData.items || []).filter(row => row.state === 'pending').slice(0, 5);
  const dismissed = (researchSuggestionData.items || []).filter(row => row.state === 'dismissed').slice(0, 2);
  const accepted = (researchSuggestionData.items || []).filter(row => row.state === 'accepted').slice(0, 3);
  const rows = [...pending, ...accepted, ...dismissed];
  if (!rows.length) {
    root.innerHTML = '<div class="empty compact">暂时没有值得打扰你的研究建议。深脉会继续观察，不会为了显得聪明而凑数。</div>';
    detail.innerHTML = `<div class="empty compact">${esc(researchSuggestionData.boundary || '')}</div>`;
    return;
  }
  root.innerHTML = rows.map(row => `<button type="button" class="research-suggestion-row" data-suggestion-id="${esc(row.id)}" data-state="${esc(row.state)}"><span><em>${esc(row.role || '研究')}</em><time>${row.state === 'accepted' ? '已转为流程' : `${esc(formatHypothesisTime(row.expiresAt))} 前有效`}</time></span><b>${esc(row.title)}</b><small>${esc(row.reason)}</small><i>${esc(row.journey?.label || (row.state === 'dismissed' ? '已忽略 · 可恢复' : '待你决定'))}</i></button>`).join('');
  renderResearchSuggestionDetail(container, pending[0]?.id || accepted[0]?.id || dismissed[0]?.id);
}

function renderResearchSuggestionDetail(container, suggestionId) {
  const detail = container.querySelector('#st-suggestion-detail');
  const item = (researchSuggestionData?.items || []).find(row => row.id === suggestionId);
  if (!detail || !item) return;
  container.querySelectorAll('[data-suggestion-id]').forEach(row => row.setAttribute('aria-current', row.dataset.suggestionId === suggestionId ? 'true' : 'false'));
  const draft = item.proposedDraft || {};
  const gaps = (item.evidenceGaps || []).map(row => `<li>${esc(row)}</li>`).join('');
  const sourceLabels = { official_disclosures: '官方披露', market_quote: '公开行情' };
  const sources = (draft.sources || []).map(id => `<span>${esc(sourceLabels[id] || id)} · 创建前需确认</span>`).join('');
  const action = item.state === 'dismissed'
    ? `<button type="button" class="btn" data-suggestion-action="restore" data-suggestion-id="${esc(item.id)}">恢复建议</button>`
    : item.state === 'accepted'
      ? `<button type="button" class="btn primary" data-suggestion-action="open-workflow" data-workflow-id="${esc(item.workflowId || '')}" data-suggestion-id="${esc(item.id)}">查看研究流程</button>`
    : `<button type="button" class="btn primary" data-suggestion-action="prepare" data-suggestion-id="${esc(item.id)}">载入为研究草稿</button><button type="button" class="btn ghost" data-suggestion-action="dismiss" data-suggestion-id="${esc(item.id)}">不需要</button>`;
  detail.innerHTML = `<header><span class="badge violet">${esc(item.role || '研究建议')}</span><time>${esc(item.journey?.label || '待你决定')}</time></header><h3>${esc(item.title)}</h3><section><h4>为什么现在提示你</h4><p>${esc(item.reason)}</p></section><section><h4>建议验证的问题</h4><p>${esc(draft.question || '')}</p></section><section><h4>拟使用来源</h4><div class="research-suggestion-sources">${sources}</div></section>${gaps ? `<details open><summary>仍需补齐的证据</summary><ul>${gaps}</ul></details>` : ''}<p class="research-suggestion-boundary">当前阶段：${esc(item.journey?.label || '待你决定')}。载入、预览、创建和运行分别记录，任何一步都不会替你执行下一步。</p><div class="research-suggestion-actions">${action}</div>`;
  detail.focus({ preventScroll: true });
}

const WORKFLOW_STATUS = {
  active: ['运行中', 'cyan'], review_due: ['待复盘', 'amber'], paused: ['已暂停', 'gray'],
  template: ['模板', 'violet'], completed: ['已完成', 'cyan'], invalid: ['需检查', 'red'],
};

function workflowDraft(container) {
  const targetType = container.querySelector('#st-wf-target-type').value;
  return {
    kind: container.querySelector('#st-wf-kind').value,
    title: container.querySelector('#st-wf-title').value.trim(),
    target: {
      type: targetType,
      code: targetType === 'stock' ? container.querySelector('#st-wf-code').value.trim() : '',
      name: container.querySelector('#st-wf-name').value.trim(),
    },
    question: container.querySelector('#st-wf-question').value.trim(),
    sources: [...container.querySelectorAll('[name="wf-source"]:checked')].map(input => input.value),
    reviewDays: Number(container.querySelector('#st-wf-days').value || 5),
    outputs: [...container.querySelectorAll('[name="wf-output"]:checked')].map(input => input.value),
    reminderEnabled: container.querySelector('#st-wf-reminder').checked,
  };
}

function parameterizeLegacyTemplate(text, target) {
  let result = String(text || '');
  if (target?.name) result = result.replaceAll(target.name, '{{target.name}}');
  if (target?.code) result = result.replaceAll(target.code, '{{target.code}}');
  return result;
}

function templateText(value, target) {
  return String(value || '')
    .replaceAll('{{target.name}}', target.name || '【标的名称】')
    .replaceAll('{{target.code}}', target.code || '【证券代码】');
}

function renderWorkflowTemplateParameters(container) {
  if (!activeWorkflowTemplate) return;
  const target = {
    code: container.querySelector('#st-wf-code').value.trim(),
    name: container.querySelector('#st-wf-name').value.trim(),
  };
  container.querySelector('#st-wf-title').value = templateText(activeWorkflowTemplate.titleTemplate, target);
  container.querySelector('#st-wf-question').value = templateText(activeWorkflowTemplate.questionTemplate, target);
}

function fillWorkflowDraft(container, workflow, fromTemplate = false) {
  const target = workflow.target || {};
  activeWorkflowTemplate = null;
  activeWorkflowOrigin = {
    workflowId: workflow.id,
    kind: fromTemplate ? 'template_instance' : 'copy',
  };
  container.querySelector('#st-wf-kind').value = fromTemplate ? 'one_off' : (workflow.kind || 'one_off');
  container.querySelector('#st-wf-target-type').value = target.type || 'stock';
  if (fromTemplate) {
    activeWorkflowTemplate = workflow.templateSpec || {
      titleTemplate: parameterizeLegacyTemplate(workflow.title, target),
      questionTemplate: parameterizeLegacyTemplate(workflow.question, target),
    };
    container.querySelector('#st-wf-code').value = '';
    container.querySelector('#st-wf-name').value = '';
    renderWorkflowTemplateParameters(container);
  } else {
    container.querySelector('#st-wf-title').value = workflow.title ? `${workflow.title} · 副本` : '';
    container.querySelector('#st-wf-code').value = target.code || '';
    container.querySelector('#st-wf-name').value = target.name || '';
    container.querySelector('#st-wf-question').value = workflow.question || '';
  }
  container.querySelector('#st-wf-days').value = String(workflow.reviewDays || 5);
  container.querySelector('#st-wf-reminder').checked = workflow.reminderEnabled === true;
  const sources = new Set(workflow.sources || []);
  container.querySelectorAll('[name="wf-source"]').forEach(input => { input.checked = sources.has(input.value); });
  const outputs = new Set(workflow.outputs || []);
  container.querySelectorAll('[name="wf-output"]').forEach(input => { input.checked = outputs.has(input.value); });
  container.querySelector('#st-wf-target-type').dispatchEvent(new Event('change'));
  container.querySelector('#st-wf-suggestion').hidden = true;
}

function updateWorkflowConfirmState(container) {
  const panel = container.querySelector('#st-wf-preview-panel');
  const button = panel.querySelector('[data-workflow-confirm]');
  if (!button || !researchWorkflowPreview?.ready) return;
  const required = panel.querySelectorAll('[data-wf-permission]').length;
  const checked = panel.querySelectorAll('[data-wf-permission]:checked').length;
  const create = panel.querySelector('[data-wf-confirm-create]')?.checked;
  button.disabled = checked !== required || !create;
}

function renderWorkflowPreview(container) {
  const panel = container.querySelector('#st-wf-preview-panel');
  if (!researchWorkflowPreview) {
    panel.innerHTML = '<div class="empty compact">尚未预览。已创建流程不会因编辑草稿而变化。</div>';
    return;
  }
  const preview = researchWorkflowPreview;
  const blockers = preview.blockers || [];
  if (blockers.length) {
    panel.innerHTML = `<div class="workflow-preview-title"><b>还不能创建</b><span>草稿没有保存</span></div><ul class="workflow-blockers">${blockers.map(row => `<li>${esc(row.message)}</li>`).join('')}</ul>`;
    return;
  }
  const sourceRows = (preview.sources || []).map(source => {
    const environment = source.environment || {};
    const tone = environment.available ? 'ok' : 'warn';
    return `<li><span><b>${esc(source.label)}</b><small>${esc(source.purpose)}</small></span><em class="${tone}">${esc(environment.status || 'unobserved')}</em></li>`;
  }).join('');
  panel.innerHTML = `
    <div class="workflow-preview-title"><b>执行预览</b><span>${esc(preview.previewId)}</span></div>
    <ol class="workflow-steps">${(preview.steps || []).map(step => `<li><span>${Number(step.order)}</span><div><b>${esc(step.label)}</b><small>${step.externalAccess ? '执行时可能访问外部公开来源' : '本机内部步骤'}${step.automatic ? ' · 自动整理' : ' · 需用户动作'}</small></div></li>`).join('')}</ol>
    <div class="workflow-source-preview"><b>来源状态</b><ul>${sourceRows}</ul></div>
    <fieldset class="workflow-permissions"><legend>逐项确认本流程允许的范围</legend>
      ${(preview.permissions || []).map(permission => `<label><input type="checkbox" data-wf-permission value="${esc(permission.id)}"><span><b>${esc(permission.label)}</b><small>${permission.persistent ? '持续到流程完成或暂停' : '仅限这条流程按需执行'}</small></span></label>`).join('')}
      <label class="workflow-final-confirm"><input type="checkbox" data-wf-confirm-create><span><b>我确认按以上草稿创建</b><small>创建不会立即读取来源，也不会生成交易动作</small></span></label>
    </fieldset>
    <button type="button" class="btn primary workflow-confirm-btn" data-workflow-confirm disabled>确认创建流程</button>`;
  updateWorkflowConfirmState(container);
}

function researchWatchOptions(container) {
  const date = container.querySelector('#st-watch-expires').value;
  return {
    frequency: container.querySelector('#st-watch-frequency').value,
    delivery: container.querySelector('#st-watch-delivery').value,
    expiresAt: date ? `${date}T23:59:00+08:00` : '',
  };
}

function openResearchWatchDialog(container, workflow) {
  researchWatchTarget = workflow;
  researchWatchPreview = null;
  const target = workflow.target || {};
  const dueDate = String(workflow.dueAt || '').slice(0, 10);
  const fallback = new Date(Date.now() + 5 * 86400000).toISOString().slice(0, 10);
  container.querySelector('#st-watch-frequency').value = 'close';
  container.querySelector('#st-watch-delivery').value = 'center_only';
  container.querySelector('#st-watch-expires').value = dueDate || fallback;
  container.querySelector('#st-watch-scope').innerHTML = `<b>${esc(workflow.title || '研究流程')}</b><span>${esc(target.name || target.code || target.type || '研究对象')} · ${(workflow.sources || []).length} 个已确认来源</span><p>${esc(workflow.question || '')}</p>`;
  renderResearchWatchPreview(container);
  const dialog = container.querySelector('#st-watch-dialog');
  dialog.showModal();
  container.querySelector('#st-watch-dialog-title').focus?.({ preventScroll: true });
}

function renderResearchWatchPreview(container) {
  const root = container.querySelector('#st-watch-preview');
  if (!researchWatchPreview) {
    root.innerHTML = '<div class="empty compact">先预览持续访问范围和到期时间，再逐项确认。</div>';
    return;
  }
  const preview = researchWatchPreview;
  if ((preview.blockers || []).length) {
    root.innerHTML = `<div class="workflow-preview-title"><b>暂时不能开启</b><span>草稿没有保存</span></div><ul class="workflow-blockers">${preview.blockers.map(text => `<li>${esc(text)}</li>`).join('')}</ul>`;
    return;
  }
  const frequency = preview.frequency === 'daily' ? '每个工作日一次' : '每个交易日收盘后';
  root.innerHTML = `<div class="research-watch-preview-head"><b>持续授权预览</b><span>预计最多 ${Number(preview.estimatedChecks || 0)} 次检查</span></div>
    <dl><div><dt>频率</dt><dd>${frequency}</dd></div><div><dt>自动结束</dt><dd>${esc(formatHypothesisTime(preview.expiresAt))}</dd></div><div><dt>提醒范围</dt><dd>${preview.delivery === 'center_only' ? '只进入提醒中心' : '遵循已授权终端'}</dd></div></dl>
    <fieldset class="workflow-permissions"><legend>逐项确认持续访问范围</legend>
      ${(preview.permissions || []).map(permission => `<label><input type="checkbox" data-watch-permission value="${esc(permission.id)}"><span><b>${esc(permission.label)}</b><small>持续到到期、暂停或手动结束；不会增加新来源</small></span></label>`).join('')}
      <label class="workflow-final-confirm"><input type="checkbox" data-watch-final><span><b>我确认按以上范围开启值守</b><small>首次检查只建立基线，不自动调用 DeepSeek 或形成结论</small></span></label>
    </fieldset><button type="button" class="btn primary workflow-confirm-btn" data-watch-confirm disabled>确认开启至 ${esc(String(preview.expiresAt || '').slice(5, 10))}</button>`;
  updateResearchWatchConfirm(container);
}

function updateResearchWatchConfirm(container) {
  const root = container.querySelector('#st-watch-preview');
  const button = root.querySelector('[data-watch-confirm]');
  if (!button || !researchWatchPreview?.ready) return;
  const required = root.querySelectorAll('[data-watch-permission]').length;
  button.disabled = root.querySelectorAll('[data-watch-permission]:checked').length !== required
    || !root.querySelector('[data-watch-final]:checked');
}

async function refreshResearchWorkflows(container) {
  try {
    researchWorkflowData = await api.researchWorkflows();
    state.researchWorkflows = researchWorkflowData;
    renderResearchWorkflows(container);
  } catch {
    container.querySelector('#st-workflow-list').innerHTML = '<div class="empty">研究流程服务暂不可用，不影响其他策略功能</div>';
  }
}

function renderResearchWorkflows(container) {
  const root = container.querySelector('#st-workflow-list');
  if (!root || !researchWorkflowData) return;
  const summary = researchWorkflowData.summary || {};
  container.querySelector('#st-workflow-summary').innerHTML = `运行 <b>${summary.active || 0}</b> · 待复盘 <b>${summary.review_due || 0}</b> · 暂停 <b>${summary.paused || 0}</b> · 模板 <b>${summary.template || 0}</b>`;
  const items = researchWorkflowData.items || [];
  if (!items.length) {
    root.innerHTML = '<div class="empty">还没有研究流程。可以从一个具体问题开始，先预览再创建。</div>';
    return;
  }
  const sources = researchWorkflowData.sourceDefinitions || {};
  root.innerHTML = items.map(item => {
    const status = WORKFLOW_STATUS[item.effectiveStatus] || WORKFLOW_STATUS.invalid;
    const target = item.target || {};
    const latest = (item.runs || []).slice(-1)[0];
    const card = item.latestRun?.resultCard || latest?.resultCard;
    const results = latest?.results || [];
    const sourceTags = (item.sources || []).map(id => `<span>${esc(sources[id]?.label || id)}</span>`).join('');
    const runRows = results.map(row => `<li data-status="${esc(row.status)}"><b>${esc(sources[row.sourceId]?.label || row.sourceId)}</b><span>${esc(row.summary || row.error || '无摘要')}</span><em>${esc(row.upstream || row.status)}</em></li>`).join('');
    const cardSummary = card?.summary || {};
    const resultCard = card ? `<section class="workflow-result-card" aria-label="研究结果卡">
      <div class="workflow-result-head"><b>研究结果卡</b><span>${Number(cardSummary.usableSources || 0)}/${Number(cardSummary.selectedSources || 0)} 来源可用 · ${Number(cardSummary.evidenceItems || 0)} 条候选证据</span></div>
      <div class="workflow-result-metrics">
        <span><b>${Number(cardSummary.gapCount || 0)}</b>缺口</span>
        <span><b>${Number(cardSummary.staleItems || 0)}</b>陈旧项</span>
        <span><b>${Number(cardSummary.sameUpstreamGroups || 0)}</b>同源组</span>
        <span><b>${Number(cardSummary.degradedSources || 0)}</b>降级源</span>
      </div>
      ${(card.sameUpstream || []).length ? `<div class="workflow-lineage-warn"><b>同源提示</b>${card.sameUpstream.map(row => `<span>${esc(row.group)}：${(row.sourceIds || []).map(id => esc(sources[id]?.label || id)).join(' / ')}</span>`).join('')}</div>` : ''}
      ${(card.gaps || []).length ? `<details class="workflow-gaps"><summary>查看 ${Number(cardSummary.gapCount || 0)} 项待核对缺口</summary><ul>${card.gaps.map(row => `<li><b>${esc(sources[row.sourceId]?.label || row.sourceId)}</b><span>${esc(row.message)}</span></li>`).join('')}</ul></details>` : '<div class="workflow-result-ok">本次所选来源均返回了可展示证据；仍不代表研究问题已经成立。</div>'}
      <small>${esc(card.boundary || '本卡片不自动生成结论。')}</small>
    </section>` : '';
    const comparison = item.runComparison;
    const deltaText = value => {
      const number = Number(value || 0);
      return `${number > 0 ? '+' : ''}${number}`;
    };
    const comparisonCard = comparison ? `<section class="workflow-comparison" aria-label="两次运行对比">
      <div class="workflow-comparison-head"><b>与上次运行相比</b><span>${esc(formatHypothesisTime(comparison.previousRanAt))} → ${esc(formatHypothesisTime(comparison.currentRanAt))}</span></div>
      <div class="workflow-comparison-metrics">
        <span>可用来源 <b>${deltaText(comparison.deltas?.usableSources)}</b></span>
        <span>候选证据 <b>${deltaText(comparison.deltas?.evidenceItems)}</b></span>
        <span>缺口 <b>${deltaText(comparison.deltas?.gapCount)}</b></span>
        <span>陈旧项 <b>${deltaText(comparison.deltas?.staleItems)}</b></span>
      </div>
      ${(comparison.sourceChanges || []).length ? `<details><summary>${Number(comparison.changedSourceCount || 0)} 个来源状态或数量发生变化</summary><ul>${comparison.sourceChanges.map(row => `<li><b>${esc(sources[row.sourceId]?.label || row.sourceId)}</b><span>${esc(row.previousStatus)} → ${esc(row.currentStatus)} · 证据 ${deltaText(row.evidenceDelta)} · 陈旧 ${deltaText(row.staleDelta)}</span></li>`).join('')}</ul></details>` : '<div class="workflow-comparison-steady">来源状态和证据数量没有变化。</div>'}
      <small>${esc(comparison.boundary || '数量变化不代表研究假设增强或减弱。')}</small>
    </section>` : '';
    const lineage = item.lineage || {};
    const originLabels = { new: '首次创建', copy: '复制形成', template_instance: '模板实例化' };
    const lineageCard = lineage.modelVersion ? `<section class="workflow-lineage" aria-label="研究方法版本">
      <div><b>方法 v${Number(lineage.methodVersion || 1)}</b><span>${esc(originLabels[lineage.originKind] || '历史方法')}</span>${lineage.originMethodVersion ? `<span>源自 v${Number(lineage.originMethodVersion)}</span>` : ''}</div>
      <details><summary>查看方法变更</summary><ul>${(lineage.changeSummary || ['未记录变更摘要']).map(text => `<li>${esc(text)}</li>`).join('')}</ul><small>${lineage.historyImmutable === true ? '历史方法只读保留，新版本不会覆盖旧记录。' : '旧版流程的版本信息为兼容视图。'}</small></details>
    </section>` : '';
    const timeline = item.evidenceTimeline || {};
    const timelineCard = (timeline.items || []).length ? `<details class="workflow-evidence-timeline">
      <summary>证据时间轴 · ${Number(timeline.summary?.items || 0)} 条 / ${Number(timeline.summary?.runs || 0)} 次运行</summary>
      <ol>${timeline.items.slice(0, 12).map(row => `<li><time>${esc(formatHypothesisTime(row.observedAt))}</time><div><b>${esc(sources[row.sourceId]?.label || row.sourceId || '未知来源')}</b><span>${esc(row.label || '来源执行记录')}</span><small>数据时点 ${esc(row.dataAt || '未披露')} · 状态 ${esc(row.status || '未知')}${row.upstream ? ` · 上游 ${esc(row.upstream)}` : ''}</small></div></li>`).join('')}</ol>
      <small>${esc(timeline.boundary || '时间轴不自动形成方向结论。')}</small>
    </details>` : '';
    const watch = item.watch || {};
    const watchState = watch.effectiveStatus || (watch.enabled ? 'active' : 'off');
    const watchLabels = {
      active: ['值守中', 'cyan'], paused: ['值守已暂停', 'amber'], expired: ['值守已到期', 'gray'],
      stopped: ['值守已结束', 'gray'], paused_error: ['来源故障已暂停', 'red'],
      workflow_inactive: ['流程停止，值守已停', 'gray'], reauthorization_required: ['需要重新授权', 'amber'],
      invalid: ['值守配置需检查', 'red'], off: ['未开启值守', 'gray'],
    };
    const watchLabel = watchLabels[watchState] || watchLabels.off;
    const watchActions = item.kind !== 'template' && item.status === 'active' ? (watchState === 'active'
      ? `<button class="btn sm primary" data-wf-action="watch_check" data-wf-id="${esc(item.id)}">立即检查</button><button class="btn sm" data-wf-action="watch_pause" data-wf-id="${esc(item.id)}">暂停值守</button><button class="btn sm ghost" data-wf-action="watch_stop" data-wf-id="${esc(item.id)}">结束值守</button>`
      : watchState === 'paused'
        ? `<button class="btn sm primary" data-wf-action="watch_resume" data-wf-id="${esc(item.id)}">恢复值守</button><button class="btn sm ghost" data-wf-action="watch_stop" data-wf-id="${esc(item.id)}">结束值守</button>`
        : `<button class="btn sm" data-wf-action="watch-setup" data-wf-id="${esc(item.id)}">${watchState === 'off' ? '开启值守' : '重新授权值守'}</button>`)
      : '';
    const watchCard = item.kind !== 'template' ? `<section class="workflow-watch" data-state="${esc(watchState)}">
      <div class="workflow-watch-head"><span class="badge ${watchLabel[1]}">${watchLabel[0]}</span><div>${watch.nextCheckAt ? `下次 ${esc(formatHypothesisTime(watch.nextCheckAt))}` : '不会后台读取来源'}${watch.expiresAt ? ` · 至 ${esc(formatHypothesisTime(watch.expiresAt))}` : ''}</div></div>
      <p>${esc(watch.boundary || '逐流程明确授权后，深脉才会持续检查已确认来源；无变化不提醒。')}</p>
      ${watch.lastChangeAt ? `<button class="btn sm ghost" data-wf-action="watch-change" data-wf-id="${esc(item.id)}">查看 ${esc(formatHypothesisTime(watch.lastChangeAt))} 的变化</button>` : ''}
      <div class="workflow-watch-actions">${watchActions}</div>
    </section>` : '';
    const canRun = item.status === 'active';
    return `<article class="workflow-item" data-status="${esc(item.effectiveStatus)}" data-workflow-id="${esc(item.id)}">
      <div class="workflow-item-head"><div><span class="badge ${status[1]}">${status[0]}</span><b>${esc(item.title || '未命名流程')}</b></div><time>${item.dueAt ? `复盘 ${esc(formatHypothesisTime(item.dueAt))}` : '无到期时间'}</time></div>
      <div class="workflow-target"><b>${esc(target.name || target.code || target.type || '研究对象')}</b>${target.code ? `<span>${esc(target.code)}</span>` : ''}<span>${Number(item.reviewDays || 0)} 个工作日</span></div>
      <p>${esc(item.question || '')}</p>
      <div class="workflow-source-tags">${sourceTags}</div>
      ${lineageCard}
      ${watchCard}
      <details class="workflow-run" ${item.effectiveStatus === 'review_due' && latest ? 'open' : ''}><summary>${latest ? `最近执行 ${esc(formatHypothesisTime(latest.ranAt))} · 成功 ${Number(latest.summary?.ok || 0)} / ${Number(latest.summary?.selected || 0)}` : '尚未执行来源读取'}</summary>${runRows ? `<ul>${runRows}</ul>` : '<div class="empty compact">执行后会记录各来源的事实摘要、最终上游和失败原因，但不会自动下结论。</div>'}</details>
      ${resultCard}
      ${comparisonCard}
      ${timelineCard}
      <div class="workflow-item-actions">
        ${canRun ? `<button class="btn sm primary" data-wf-action="run" data-wf-id="${esc(item.id)}">执行一次</button>` : ''}
        ${item.status === 'active' ? `<button class="btn sm" data-wf-action="pause" data-wf-id="${esc(item.id)}">暂停</button>` : ''}
        ${item.status === 'paused' ? `<button class="btn sm" data-wf-action="resume" data-wf-id="${esc(item.id)}">继续</button>` : ''}
        <button class="btn sm" data-wf-action="copy" data-wf-id="${esc(item.id)}">${item.kind === 'template' ? '从模板创建' : '复制为草稿'}</button>
        <button class="btn sm" data-wf-action="ask" data-wf-id="${esc(item.id)}">让 DeepSeek 解读</button>
        ${card && (item.outputs || []).includes('review_note') ? `<button class="btn sm" data-wf-action="review-draft" data-wf-id="${esc(item.id)}">填入复盘草稿</button>` : ''}
        ${['active', 'paused'].includes(item.status) ? `<button class="btn sm ghost" data-wf-action="complete" data-wf-id="${esc(item.id)}">标记完成</button>` : ''}
      </div>
    </article>`;
  }).join('');
  if (pendingWorkflowFocus) requestAnimationFrame(() => focusResearchWorkflow(container, pendingWorkflowFocus));
}

function focusResearchWorkflow(container, workflowId) {
  const id = String(workflowId || '');
  if (!id) return;
  const rows = [...container.querySelectorAll('[data-workflow-id]')];
  const target = rows.find(row => row.dataset.workflowId === id);
  if (!target) {
    pendingWorkflowFocus = id;
    return;
  }
  pendingWorkflowFocus = '';
  rows.forEach(row => row.classList.toggle('workflow-focus', row === target));
  target.scrollIntoView({ behavior: 'smooth', block: 'center' });
  setTimeout(() => target.classList.remove('workflow-focus'), 3200);
}

const HYP_STATUS = {
  observing: ['观察中', 'cyan'], review_due: ['待复盘', 'amber'],
  completed: ['已完成', 'cyan'], archived: ['已归档', 'gray'], invalid: ['需检查', 'red'],
};
const HYP_OUTCOME = {
  supported: '支持', mixed: '混合', not_supported: '不支持', invalid: '事件失效',
};
const EVIDENCE_KIND = {
  official_disclosure: ['官方公告', 'official'],
  relative_performance: ['相对表现', 'market'],
  market_context: ['市场基准', 'control'],
};

function formatHypothesisTime(value) {
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value || '--';
  return d.toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false });
}

async function refreshHypotheses(container) {
  try {
    hypothesisData = await api.researchHypotheses();
    renderHypotheses(container);
  } catch {
    container.querySelector('#st-hyp-list').innerHTML = '<div class="empty">研究假设服务暂不可用，不影响其他策略功能</div>';
  }
}

function renderHypotheses(container) {
  const root = container.querySelector('#st-hyp-list');
  if (!root) return;
  const summary = hypothesisData?.summary || {};
  container.querySelector('#st-hyp-summary').innerHTML = `观察中 <b>${summary.observing || 0}</b> · 待复盘 <b>${summary.review_due || 0}</b> · 已完成 <b>${summary.completed || 0}</b> · 候选证据 <b>${summary.candidateEvidence || 0}</b>`;
  const items = (hypothesisData?.items || []).filter(row => row.effectiveStatus !== 'archived');
  if (!items.length) {
    root.innerHTML = '<div class="empty">还没有研究假设。到总览的事件影响雷达，选择观察窗口并保存一条事件。</div>';
    return;
  }
  root.innerHTML = items.map(item => {
    const baseline = item.baseline || {};
    const status = HYP_STATUS[item.effectiveStatus] || HYP_STATUS.invalid;
    const review = item.review || {};
    const watches = (baseline.watchlist || []).map(row => `<span>${esc(row.name || row.code)}</span>`).join('');
    const sectors = (baseline.sectors || []).slice(0, 6).map(row => `<span>${esc(row)}</span>`).join('');
    const candidates = (item.evidenceCandidates || []).slice(0, 16);
    const evidenceState = item.evidenceState || {};
    const marketBaseline = item.marketBaseline || {};
    const baselineWatch = (marketBaseline.watchlist || []).map(row => `${esc(row.name || row.code)} ${esc(row.price ?? '--')}`).join(' / ');
    const timeline = candidates.map(row => {
      const kind = EVIDENCE_KIND[row.kind] || ['候选事实', 'other'];
      const source = row.source || {};
      return `<li class="hypothesis-evidence-row" data-kind="${esc(kind[1])}">
        <time>${esc(formatHypothesisTime(row.knowableAt))}</time>
        <div><div class="hypothesis-evidence-title"><span>${kind[0]}</span><b>${esc(row.label || '未命名证据')}</b></div>
        <ul>${(row.facts || []).slice(0, 4).map(fact => `<li>${esc(fact)}</li>`).join('')}</ul>
        <small>${esc(source.name || '来源待确认')} · ${esc(source.tier || 'unknown')} · ${esc(row.interpretation || '')}</small></div>
      </li>`;
    }).join('');
    const due = item.effectiveStatus === 'review_due';
    return `<article class="hypothesis-item" data-status="${esc(item.effectiveStatus)}">
      <div class="hypothesis-head">
        <span class="badge ${status[1]}">${status[0]}</span>
        <b>${esc(baseline.title || '未命名研究假设')}</b>
        <time>复盘 ${esc(formatHypothesisTime(item.reviewDueAt))}</time>
      </div>
      <p class="hypothesis-statement">${esc(item.statement)}</p>
      <div class="hypothesis-path"><span>行业</span>${sectors || '<em>待确认</em>'}<i>→</i><span>自选</span>${watches || '<em>未命中</em>'}</div>
      <details><summary>查看创建时事实、观察清单与反证条件</summary>
        <div class="hypothesis-evidence">
          <div><b>创建时来源</b>${(baseline.sources || []).map(row => esc(row.name)).join(' / ') || '待确认'} · 质量 ${esc(baseline.quality?.score ?? '--')}</div>
          <ol>${(item.observationChecklist || []).map(row => `<li>${esc(row.label)}</li>`).join('')}</ol>
          <div class="hypothesis-falsifiers"><b>出现以下情况应削弱或推翻</b><ul>${(item.falsifiers || []).map(row => `<li>${esc(row)}</li>`).join('')}</ul></div>
        </div>
      </details>
      <details class="hypothesis-timeline" ${due && candidates.length ? 'open' : ''}><summary>候选证据时间线（${candidates.length}）· 只收集事实，不自动下结论</summary>
        <div class="hypothesis-baseline"><b>对照基线</b><span>${marketBaseline.capturedAt ? esc(formatHypothesisTime(marketBaseline.capturedAt)) : '尚未建立'}</span><span>${baselineWatch || '暂无自选价格基线'}</span><span>${esc(marketBaseline.benchmark?.name || '市场基准')} ${esc(marketBaseline.benchmark?.price ?? '--')}</span></div>
        ${timeline ? `<ol class="hypothesis-timeline-list">${timeline}</ol>` : '<div class="empty compact">点击“收集候选证据”建立创建时基线；之后按日期更新相对表现与官方公告。</div>'}
        ${evidenceState.errors?.length ? `<div class="hypothesis-evidence-errors">部分来源暂不可用：${evidenceState.errors.slice(0, 3).map(esc).join('；')}</div>` : ''}
      </details>
      ${review.outcome ? `<div class="hypothesis-review-result"><b>你的结论：${esc(HYP_OUTCOME[review.outcome] || review.outcome)}</b><span>${esc(review.note || '')}</span>
        ${(review.falsifierHits || []).length ? `<small>命中反证：${review.falsifierHits.map(esc).join('；')}</small>` : ''}
        ${(review.dataGaps || []).length ? `<small>数据缺口：${review.dataGaps.map(esc).join('；')}</small>` : ''}</div>` : ''}
      ${!review.outcome ? `<details class="hypothesis-review-box" ${due ? 'open' : ''}><summary>${due ? '观察窗口已结束，填写结论' : '事件提前失效或已有充分证据？可提前复盘'}</summary><div class="hypothesis-review-form">
        <select data-hyp-outcome aria-label="假设复盘结论">
          <option value="mixed">混合：部分证据成立</option><option value="supported">支持</option>
          <option value="not_supported">不支持</option><option value="invalid">事件失效</option>
        </select>
        <textarea data-hyp-note placeholder="记录支持证据、反证，以及哪些信息是后来才知道的…"></textarea>
        <fieldset class="hypothesis-falsifier-checks"><legend>本次命中了哪些预设反证？（可多选）</legend>
          ${(item.falsifiers || []).map(row => `<label><input type="checkbox" data-hyp-falsifier value="${esc(row)}"><span>${esc(row)}</span></label>`).join('')}
        </fieldset>
        <textarea data-hyp-gaps placeholder="仍缺少哪些数据？每行一项，例如：官方公告原文、板块成交额对照…"></textarea>
        <button class="btn sm primary" data-hyp-action="review" data-hyp-id="${esc(item.id)}">确认复盘结论</button>
      </div></details>` : ''}
      <div class="hypothesis-actions">
        ${!review.outcome ? `<button class="btn sm" data-hyp-action="evidence" data-hyp-id="${esc(item.id)}">收集候选证据</button>` : ''}
        <button class="btn sm" data-hyp-action="ask" data-hyp-id="${esc(item.id)}">让 DeepSeek 按反证复盘</button>
        <button class="btn sm ghost" data-hyp-action="archive" data-hyp-id="${esc(item.id)}">归档</button>
      </div>
    </article>`;
  }).join('');
}

async function refreshResearchMemory(container) {
  try {
    researchMemoryData = await api.researchMemory();
    state.researchMemory = researchMemoryData;
    renderResearchMemory(container);
  } catch {
    container.querySelector('#st-memory-list').innerHTML = '<div class="empty">研究记忆暂不可用，不影响原始复盘与假设</div>';
  }
}

function renderResearchMemory(container) {
  const root = container.querySelector('#st-memory-list');
  if (!root || !researchMemoryData) return;
  const summary = researchMemoryData.summary || {};
  const prefs = researchMemoryData.preferences || {};
  const toggle = container.querySelector('#st-memory-enabled');
  toggle.checked = prefs.enabled !== false;
  container.querySelector('#st-memory-summary').innerHTML = `已确认记忆 <b>${summary.visible || 0}</b> · 已写方法改进 <b>${summary.withLesson || 0}</b> · 含数据缺口 <b>${summary.withDataGaps || 0}</b>${summary.hidden ? ` · 已隐藏 <b>${summary.hidden}</b>` : ''}`;
  const patterns = researchMemoryData.patterns || {};
  const patternRoot = container.querySelector('#st-memory-patterns');
  const minimum = Number(patterns.minimumSampleForPattern || 3);
  if ((summary.visible || 0) < minimum) {
    patternRoot.innerHTML = `<span>再积累 ${Math.max(0, minimum - Number(summary.visible || 0))} 条确认复盘后，才汇总重复出现的数据缺口与反证结构，避免小样本被包装成规律。</span>`;
  } else {
    const gaps = (patterns.frequentDataGaps || []).slice(0, 4);
    patternRoot.innerHTML = `<b>已确认记录中的重复结构</b><span>反证命中 ${Number(patterns.falsifierHitCount || 0)} 次</span>${gaps.map(row => `<span>${esc(row.label)} × ${Number(row.count || 0)}</span>`).join('') || '<span>暂未出现重复数据缺口</span>'}`;
  }
  container.querySelector('#st-memory-boundary').textContent = researchMemoryData.boundary || '只使用你明确确认的记录。';
  const items = researchMemoryData.items || [];
  if (!items.length) {
    root.innerHTML = '<div class="empty">完成一次研究假设复盘后，这里会形成第一条可追溯记忆。不会从收益结果自动生成经验。</div>';
    return;
  }
  root.innerHTML = items.map(memory => `<article class="research-memory-item ${memory.hidden ? 'is-hidden' : ''}">
    <div class="research-memory-title"><div><span class="badge ${memory.outcome === 'not_supported' || memory.outcome === 'invalid' ? 'amber' : 'cyan'}">${esc(memory.outcomeLabel)}</span><b>${esc(memory.title)}</b></div><time>${esc(formatHypothesisTime(memory.reviewedAt))}</time></div>
    <p>${esc(memory.conclusion || '未填写结论说明')}</p>
    <div class="research-memory-facts">
      ${(memory.falsifierHits || []).length ? `<span><b>命中反证</b>${memory.falsifierHits.map(esc).join('；')}</span>` : '<span><b>命中反证</b>未记录</span>'}
      ${(memory.dataGaps || []).length ? `<span><b>数据缺口</b>${memory.dataGaps.map(esc).join('；')}</span>` : '<span><b>数据缺口</b>未记录</span>'}
    </div>
    <label class="research-memory-lesson"><span>以后遇到相似结构，我要改进什么？</span><textarea data-memory-lesson placeholder="由你确认的方法改进；DeepSeek 可以协助起草，但不会自动保存">${esc(memory.lesson || '')}</textarea></label>
    <div class="hypothesis-actions">
      <button class="btn sm primary" data-memory-action="update_lesson" data-memory-id="${esc(memory.id)}">保存方法改进</button>
      <button class="btn sm" data-memory-action="ask" data-memory-id="${esc(memory.id)}">让 DeepSeek 帮我总结</button>
      <button class="btn sm ghost" data-memory-action="${memory.hidden ? 'restore' : 'hide'}" data-memory-id="${esc(memory.id)}">${memory.hidden ? '恢复显示' : '隐藏记忆'}</button>
    </div>
    <small>来源：你确认的假设复盘 · 原始记录不会被这里的编辑或隐藏操作修改</small>
  </article>`).join('');
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
