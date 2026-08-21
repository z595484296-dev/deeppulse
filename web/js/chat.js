/* ============================================================
   深脉 DeepPulse — 蚂小财 · DeepSeek 版
   AI 金融助手：本地智能大脑（意图识别 + 真实数据问答 + 全局调度）
   配置 DeepSeek API Key（data/config.json）后自动升级为云端大脑。
   ============================================================ */

import { api } from './api.js?v=1.18.0';
import { addWatch, removeWatch, loadWatch, persistChatHistory } from './store.js?v=1.18.0';
import { esc, fmtPct, fmtPrice, fmtBig, pctClass, fmtSeal, toast, PHASE_COLORS } from './util.js?v=1.18.0';
import { EMBEDDED, askDeepSeek } from './bridge.js?v=1.18.0';

export const BOT_NAME = '蚂小财';
const HISTORY_KEY = 'dp_chat_v1';

/* ============================================================
   纯函数部分：意图识别与实体解析（可被 Node 单测）
   ============================================================ */

const INDEX_MAP = [
  ['上证指数', '000001'], ['大盘', '000001'], ['沪指', '000001'],
  ['深证成指', '399001'], ['深成指', '399001'],
  ['创业板指', '399006'], ['创业板', '399006'],
  ['科创50', '000688'], ['北证50', '899050'],
];

const PAGE_MAP = [
  ['总览', 'overview'], ['首页', 'overview'], ['情绪', 'emotion'], ['周期', 'emotion'],
  ['行情', 'market'], ['梯队', 'ladder'], ['涨停', 'ladder'],
  ['自选', 'watch'], ['策略', 'strategy'], ['墨水屏', 'epaper'], ['硬件', 'epaper'], ['数据', 'datasrc'],
  ['数据源', 'datasrc'], ['关于', 'about'],
];

export function extractCode(text) {
  const m = text.match(/(\d{6})/);
  return m ? m[1] : null;
}

export function matchIndex(text) {
  for (const [kw, code] of INDEX_MAP) {
    if (text.includes(kw)) return { code, name: kw === '大盘' || kw === '沪指' ? '上证指数' : kw };
  }
  return null;
}

export function matchPage(text) {
  for (const [kw, page] of PAGE_MAP) {
    if (text.includes(kw)) return page;
  }
  return null;
}

const NAME_STOPWORDS = [
  '帮我', '帮我看看', '帮我看下', '帮我查', '看看', '看下', '查一下', '查查', '查',
  '打开', '去', '切换到', '前往', '进入', '请问', '问下', '我想', '想了解', '了解',
  '怎么样', '如何', '行情', '价格', '走势', '分析', '什么', '多少', '现在',
  '今天', '今日', '目前', '有没有', '能不能', '能买', '该买', '值得',
  '贵不贵', '涨了', '跌了', '涨停', '连板', 'k线', 'K线', 'macd', 'MACD', '技术面',
  '加自选', '加入自选', '关注', '收藏', '删自选', '删除自选', '移除自选', '取消关注',
  '情绪', '周期', '温度', '阶段', '仓位', '建议', '风险', '资金', '主力',
  '题材', '主线', '热点', '板块', '赛道', '方向', '风口', '龙头', '梯队', '炸板',
  '龙虎榜', '游资', '上榜', '净买', '榜单', '涨停池', '跌停池', '炸板池',
  '宽度', '涨跌', '家数', '榜', '页面', '页', '界面', '市场', '盘面',
  '。', '，', '？', '?', '!', '！', ' ', '、', '的', '了', '呢', '吗', '啊', '呀',
];

function cleanName(text) {
  let t = text;
  for (const w of NAME_STOPWORDS) t = t.replaceAll(w, '');
  // 保留 2-6 个连续汉字
  const m = t.match(/[\u4e00-\u9fa5]{2,6}/);
  return m ? m[0] : '';
}

/** 从文本解析股票：优先6位代码，其次指数别名，再试名称搜索 */
export async function resolveStock(text) {
  const code = extractCode(text);
  if (code) {
    try { const q = await api.quote(code); return { code, name: q.name || code }; }
    catch { return { code, name: code }; }
  }
  const ix = matchIndex(text);
  if (ix) return ix;
  const cand = cleanName(text);
  if (cand) {
    try {
      const hits = await api.search(cand);
      if (hits && hits.length) {
        const hit = hits.find(h => h.name.includes(cand)) || hits[0];
        return { code: hit.code, name: hit.name };
      }
    } catch { /* 继续 */ }
  }
  return null;
}

/** 意图分类（实体优先，其次规则） */
const STOCK_KW = /怎么样|行情|价格|走势|多少|现价|k\s*线|macd|帮我|看看|看下|查一下|查查|涨停了|跌停了|涨了|跌了|能不能|该不该|值得|贵不贵|加自选|加入自选|删.*自选|移除自选|取消关注|关注一下|收藏|席位|谁在买/i;

export function classify(text) {
  const t = text.trim();
  const code = extractCode(t);
  const idxKw = matchIndex(t);
  const cand = cleanName(t);
  const hasStockKw = STOCK_KW.test(t);
  const hasStockish = !!(code || idxKw || (cand && hasStockKw));
  const pageKw = matchPage(t);

  // 页面调度：需页面关键词 + 调度动词（避免“涨停梯队如何”被误判为跳页）
  if (pageKw && !hasStockish && /打开|去|切换到|前往|进入|跳转|帮我打开/.test(t)) {
    return { intent: 'nav', page: pageKw };
  }
  // 个股相关
  if (hasStockish) {
    if (/k\s*线|走势图|技术面|macd|均线/i.test(t)) return { intent: 'stock_kline' };
    if (/席位|谁在买|游资.*买|买入席/.test(t)) return { intent: 'dragon_seats' };
    if (/加自选|加入自选|关注一下|收藏/.test(t) && !/取消|删|移除/.test(t)) return { intent: 'watch_add' };
    if (/删.*自选|移除自选|取消关注|自选.*(删|移除|去掉)/.test(t)) return { intent: 'watch_remove' };
    return { intent: 'stock_quote' };
  }

  const rules = [
    [/^(你好|您好|hi|hello|嗨|哈喽|在吗|早|早上好|中午好|下午好|晚上好)/i, 'greet'],
    [/你是谁|你叫什么|介绍一下你|认识一下|蚂小财是什么|你是ai|你是AI/, 'intro'],
    [/帮助|能做什么|会什么|怎么用|什么功能|功能列表|指令|菜单|使用说明|你能/, 'help'],
    [/谢谢|感谢|辛苦|多谢|thx|thanks/, 'thanks'],
    [/再见|拜拜|晚安|回头见|下次聊|bye|退出|离开/, 'bye'],
    [/亏了|亏钱|亏麻|赔了|被套|套牢|割肉|大面|绿了|心态崩|难受|跌麻|扛不住|止损/, 'comfort'],
    [/赚了|吃肉|涨停了|红盘|翻倍|发财/, 'cheer'],
    [/加自选|加入自选|关注一下|收藏/, 'watch_add'],
    [/删.*自选|移除自选|取消关注|自选.*(删|移除|去掉)/, 'watch_remove'],
    [/自选|我的股票|持仓|关注的|自选股|watchlist/, 'watch_show'],
    [/日记|写复盘|复盘记录|复盘模板/, 'journal'],
    [/炸板率|打板溢价|连板晋级|晋级率/, 'emotion'],
    [/涨停|连板|梯队|龙头|打板|接力|高度板|炸板|空间板/, 'ladder'],
    [/情绪|温度|周期|阶段|冰点|发酵|高潮|退潮|修复|亢奋|复盘|盘面怎么样|市场怎么样|今天怎么样|市场如何/, 'emotion'],
    [/涨跌家数|市场宽度|多少家涨|多少家跌|普涨|普跌|宽度/, 'breadth'],
    [/资金|主力|北向|流入|流出/, 'flow'],
    [/风险|危险|预警|要跑吗|减仓|防守/, 'risk'],
    [/建议|仓位|该买|该卖|买什么|卖什么|怎么操作|策略|操作建议|布局/, 'advice'],
    [/涨幅榜|涨得最多|领涨|涨停榜/, 'rank'],
    [/龙虎榜|游资|上榜/, 'dragon'],
    [/题材|板块|主线|热点|赛道|方向|风口/, 'sector'],
    [/记录.*快照|快照|存档|保存.*情绪/, 'record'],
  ];
  for (const [re, intent] of rules) {
    if (re.test(t)) return { intent };
  }
  return { intent: 'fallback' };
}

/* ============================================================
   数据问答（本地大脑，全部基于真实数据）
   ============================================================ */

async function getEmotion() {
  const em = await api.emotion();
  if (!em || !em.engine) throw new Error('情绪数据暂不可用');
  return em;
}

async function emotionSummary(em) {
  const en = em.engine, raw = en.raw || {}, adv = en.advice || {};
  const dyn = en.dynamics || {}, trans = en.transition || {};
  const c = PHASE_COLORS[en.color] || '#e9eef8';
  const lines = [];
  lines.push(`今天的市场体温是 <b style="color:${c}">${en.temp}°</b>，处于 <b>${en.phase}</b>${en.phase === '发酵期' ? ' 🔥' : ''}。`);
  lines.push(`涨停 <b>${raw.zt ?? '--'}</b> 家 · 跌停 <b>${raw.dt ?? '--'}</b> 家 · 炸板率 <b>${raw.zb_rate != null ? (raw.zb_rate * 100).toFixed(0) + '%' : '--'}</b> · 最高 <b>${raw.height ?? '--'}</b> 连板`);
  lines.push(`昨日涨停指数 <b>${raw.zt_idx_pct != null ? fmtPct(raw.zt_idx_pct) : '--'}</b> · 昨日连板指数 <b>${raw.lb_idx_pct != null ? fmtPct(raw.lb_idx_pct) : '--'}</b>`);
  lines.push(`涨跌 <b>${raw.up ?? '--'} : ${raw.down ?? '--'}</b> · 主力净流入 <b class="${(raw.flow_yi ?? 0) >= 0 ? 'up' : 'down'}">${raw.flow_yi != null ? fmtBig(raw.flow_yi * 1e8) : '--'}</b>`);
  lines.push(`方向 <b>${dyn.arrow || '·'} ${dyn.direction || '待积累'}</b>${dyn.delta1 == null ? '' : `（Δ1 ${dyn.delta1 > 0 ? '+' : ''}${dyn.delta1}°）`} · 数据覆盖 <b>${en.coverage ?? 0}%</b> · 数据质量分 <b>${en.confidence ?? 0}</b>`);
  lines.push(`状态倾向（未校准）：升阶 ${trans.upgrade ?? '--'} · 维持 ${trans.stay ?? '--'} · 降阶 ${trans.downgrade ?? '--'}`);
  lines.push(`📌 风险暴露参考区间：<b>${adv.position || '--'}</b>，${adv.style || '--'}。`);
  if (en.risks && en.risks.length) lines.push(`⚠️ 风险：${en.risks[0]}`);
  return lines.join('<br>');
}

async function answerEmotion() {
  const em = await getEmotion();
  const html = await emotionSummary(em);
  return { html, actions: [{ type: 'nav', page: 'emotion' }], chipText: '打开情绪周期页' };
}

async function answerAdvice() {
  const em = await getEmotion();
  const en = em.engine, adv = en.advice || {};
  const html = [
    `当前阶段 <b>${en.phase}</b>，研究框架如下：`,
    `💰 风险暴露参考区间 <b>${adv.position}</b> — ${adv.style}`,
    `📊 数据覆盖 ${en.coverage ?? 0}% · 数据质量分 ${en.confidence ?? 0}${adv.actionable ? '' : '（区间参考已暂停）'}`,
    `📋 ${adv.plan || ''}`,
    `💡 主线打法：${adv.zhuXian || '跟随涨停题材热度榜的主线'}。`,
  ].join('<br>');
  return { html, actions: [{ type: 'nav', page: 'strategy' }], chipText: '打开策略页' };
}

async function answerRisk() {
  const em = await getEmotion();
  const en = em.engine;
  const risks = en.risks || [];
  const flags = (en.flags || []).filter(f => f.type === 'warn');
  const html = (risks.length
    ? `⚠️ 当前风险扫描：<br>· ${risks.join('<br>· ')}`
    : '✅ 当前没有明显风险信号。盘中重点跟踪<b>炸板率</b>与<b>昨日涨停指数</b>的变化。')
    + (flags.length ? `<br><br>信号提醒：<br>· ${flags.map(f => f.text).join('<br>· ')}` : '');
  return { html, actions: [{ type: 'nav', page: 'overview' }], chipText: '回总览看详情' };
}

async function answerLadder() {
  const res = await api.ladder('ZT');
  const pool = res.pool || [];
  if (!pool.length) return { html: '今天涨停池还是空的，市场可能处于冰点 ❄️。' };
  const heights = pool.map(it => it.lbc || 1);
  const maxH = Math.max(...heights);
  const tops = pool.filter(it => (it.lbc || 1) === maxH).slice(0, 3);
  const lbCount = heights.filter(h => h >= 2).length;
  const agg = {};
  pool.forEach(it => { const k = it.hybk || '其他'; agg[k] = (agg[k] || 0) + 1; });
  const sectors = Object.entries(agg).sort((a, b) => b[1] - a[1]).slice(0, 3);
  const lines = [];
  lines.push(`今日涨停 <b>${pool.length}</b> 家，最高 <b>${maxH} 连板</b>${tops.length ? `：${tops.map(t => `<b>${esc(t.name)}</b>${t.zbc ? '（炸过' + t.zbc + '次）' : ''}`).join('、')}` : ''}。`);
  lines.push(`连板 <b>${lbCount}</b> 家，梯队${lbCount >= 10 ? '厚实，集团作战' : lbCount >= 3 ? '成型' : '单薄，接力需谨慎'}。`);
  if (sectors.length) lines.push(`主线题材：${sectors.map(([k, v]) => `<b>${esc(k)}</b>×${v}`).join(' · ')}`);
  const em = await getEmotion().catch(() => null);
  if (em) lines.push(`配合当前情绪（${em.engine.temp}° ${em.engine.phase}），${em.engine.advice.style}。`);
  return { html: lines.join('<br>'), actions: [{ type: 'nav', page: 'ladder' }], chipText: '打开涨停梯队' };
}

async function answerStock(text) {
  const stk = await resolveStock(text);
  if (!stk) return { html: '没听清你想看哪只股票 🤔 可以试试：<b>“帮我看看贵州茅台”</b> 或直接输入代码 <b>600519</b>。' };
  const [q, em, k] = await Promise.all([
    api.quote(stk.code).catch(() => null),
    api.emotion().catch(() => null),
    api.kline(stk.code, 101, 1, 60).catch(() => null),
  ]);
  if (!q) return { html: `${esc(stk.name)} 的行情暂时取不到（可能代码有误或上游限流），稍后再试。` };
  const cls = pctClass(q.pct);
  const color = cls === 'up' ? 'var(--up)' : cls === 'down' ? 'var(--down)' : 'var(--text)';
  const lines = [];
  lines.push(`<b>${esc(q.name)}</b> <span style="color:var(--text-3)">${esc(q.code)}</span>`);
  lines.push(`现价 <b style="color:${color};font-size:16px">${fmtPrice(q.price)}</b> <b style="color:${color}">${fmtPct(q.pct)}</b>${q.chg ? `（${q.chg > 0 ? '+' : ''}${q.chg}）` : ''}`);
  lines.push(`今开 ${fmtPrice(q.open)} · 最高 ${fmtPrice(q.high)} · 最低 ${fmtPrice(q.low)} · 换手 ${q.turnover ?? '--'}% · 量比 ${q.vol_ratio || '--'}`);
  // 情绪标签
  let tags = [];
  if (em && em.pools) {
    const zt = em.pools.ZT.pool.find(it => it.code === stk.code);
    const dt = em.pools.DT.pool.find(it => it.code === stk.code);
    const zb = em.pools.ZB.pool.find(it => it.code === stk.code);
    if (zt) tags.push(zt.lbc >= 2 ? `${zt.lbc}连板 🔥` : '涨停');
    if (dt) tags.push('跌停 ❄️');
    if (zb) tags.push('炸板');
  }
  if (tags.length) lines.push(`情绪标签：${tags.map(t => `<b>${t}</b>`).join(' · ')}`);
  // 技术位
  if (k && k.rows && k.rows.length >= 21) {
    const closes = k.rows.map(r => r.close);
    const ma20 = closes.slice(-20).reduce((a, b) => a + b, 0) / 20;
    const ma60 = closes.length >= 60 ? closes.slice(-60).reduce((a, b) => a + b, 0) / 60 : null;
    const c20 = q.price / ma20 - 1;
    const p5 = closes.length >= 6 ? (closes[closes.length - 1] / closes[closes.length - 6] - 1) * 100 : null;
    lines.push(`MA20 ${c20 >= 0 ? '上方' : '下方'} <b>${Math.abs(c20 * 100).toFixed(1)}%</b>${ma60 ? ` · MA60 ${q.price >= ma60 ? '上方' : '下方'}` : ''}${p5 != null ? ` · 近5日 <b class="${p5 >= 0 ? 'up' : 'down'}">${fmtPct(p5)}</b>` : ''}`);
  }
  lines.push(`PE ${q.pe > 0 ? q.pe.toFixed(1) : '亏损'} · PB ${q.pb > 0 ? q.pb.toFixed(1) : '--'} · 市值 ${fmtBig(q.mktcap)}`);
  return { html: lines.join('<br>'), actions: [{ type: 'quote', code: stk.code, name: q.name }], chipText: '打开K线图' };
}

async function answerBreadth() {
  const em = await getEmotion();
  const b = em.breadth || {}, raw = em.engine.raw || {};
  const html = [
    `今日市场宽度：上涨 <b class="up">${b.up ?? '--'}</b> 家 / 下跌 <b class="down">${b.down ?? '--'}</b> 家 / 平盘 ${b.flat ?? '--'} 家。`,
    `涨停 <b>${raw.zt ?? '--'}</b> 家、跌停 <b>${raw.dt ?? '--'}</b> 家。`,
    `情绪温度 ${em.engine.temp}°（${em.engine.phase}）。`,
  ].join('<br>');
  return { html, actions: [{ type: 'nav', page: 'emotion' }], chipText: '看情绪周期' };
}

async function answerFlow() {
  const em = await getEmotion();
  const flows = em.flows || {};
  const raw = em.engine.raw || {};
  if (!flows.sh || !flows.sz) return { html: '资金数据暂时取不到，稍后再试。' };
  const last = flows.sh[flows.sh.length - 1];
  const recent = flows.sh.slice(-5).map(r => r.main);
  const trend = recent.length > 1 ? (recent[recent.length - 1] - recent[0]) / 1e8 : 0;
  const html = [
    `今日两市主力净流入 <b class="${raw.flow_yi >= 0 ? 'up' : 'down'}">${fmtBig(raw.flow_yi * 1e8)}</b>（沪 ${fmtBig(flows.sh[flows.sh.length - 1].main)} / 深 ${fmtBig(flows.sz[flows.sz.length - 1].main)}）。`,
    `近5日资金趋势：${trend >= 0 ? '净流入扩大' : '净流出扩大'}约 <b>${fmtBig(trend * 1e8)}</b>。`,
    `大资金${raw.flow_yi >= 0 ? '进场，情绪有支撑' : '撤退，短期需防守'}。`,
  ].join('<br>');
  return { html, actions: [{ type: 'nav', page: 'overview' }], chipText: '回总览看资金图' };
}

async function answerRank() {
  const rank = await api.rank('up');
  const top = rank.slice(0, 5);
  const html = '今日涨幅榜前五：<br>' + top.map((r, i) =>
    `${i + 1}. <b>${esc(r.name)}</b> <span class="up">${fmtPct(r.pct)}</span> <span style="color:var(--text-3)">换手${r.turnover ?? '--'}%</span>`).join('<br>');
  return { html, actions: [{ type: 'nav', page: 'overview' }], chipText: '看完整榜单' };
}

async function answerDragon() {
  const d = await api.dragon();
  if (!d || !d.list || !d.list.length) return { html: '今天没有龙虎榜数据（休市日显示最近交易日）。' };
  const s = d.stats || {};
  const top = d.list.slice(0, 5);
  const html = [
    `今日龙虎榜 <b>${s.count}</b> 家上榜，总净买 <b class="${(s.total_net ?? 0) >= 0 ? 'up' : 'down'}">${(s.total_net ?? 0) > 0 ? '+' : ''}${s.total_net ?? '--'} 亿</b>。`,
    `净买前列：${top.map((t, i) => `${i + 1}. <b>${esc(t.name)}</b> <span class="${t.net >= 0 ? 'up' : 'down'}">${t.net > 0 ? '+' : ''}${t.net}亿</span>`).join('　')}`,
    `💡 游资动向是情绪周期的风向标：净买集中说明资金有共识，散乱则多是试错。`,
  ].join('<br>');
  return { html, actions: [{ type: 'nav', page: 'ladder' }], chipText: '打开龙虎榜' };
}

async function answerSector() {
  const [sectors, ladder] = await Promise.all([api.sectors().catch(() => []), api.ladder('ZT').catch(() => null)]);
  const lines = [];
  if (sectors.length) lines.push('领涨行业：' + sectors.slice(0, 4).map(s => `<b>${esc(s.name)}</b> <span class="${s.pct >= 0 ? 'up' : 'down'}">${fmtPct(s.pct)}</span>`).join(' · '));
  if (ladder && ladder.pool && ladder.pool.length) {
    const agg = {};
    ladder.pool.forEach(it => { const k = it.hybk || '其他'; agg[k] = (agg[k] || 0) + 1; });
    const top = Object.entries(agg).sort((a, b) => b[1] - a[1]).slice(0, 3);
    lines.push('涨停主线：' + top.map(([k, v]) => `<b>${esc(k)}</b>×${v}`).join(' · '));
  }
  lines.push('💡 主线是市场用真金白银投票出来的，跟随而非发明。');
  return { html: lines.join('<br>'), actions: [{ type: 'nav', page: 'ladder' }], chipText: '去梯队看题材' };
}

async function answerWatchShow() {
  const list = loadWatch();
  if (!list.length) return { html: '你的自选还是空的。对我说<b>“加自选 贵州茅台”</b>，我来帮你盯盘 👀。', actions: [{ type: 'nav', page: 'watch' }], chipText: '打开自选页' };
  const quotes = await Promise.all(list.slice(0, 8).map(async w => {
    try { const q = await api.quote(w.code); return { w, q }; } catch { return null; }
  }));
  const lines = [`你自选了 <b>${list.length}</b> 只：`];
  quotes.forEach(({ w, q }) => {
    if (!q) { lines.push(`· <b>${esc(w.name || w.code)}</b> <span style="color:var(--text-3)">行情暂不可用</span>`); return; }
    const cls = pctClass(q.pct);
    lines.push(`· <b>${esc(q.name)}</b> ${fmtPrice(q.price)} <b class="${cls}">${fmtPct(q.pct)}</b>${w.note ? ` <span style="color:var(--text-3)">「${esc(w.note.slice(0, 14))}」</span>` : ''}`);
  });
  return { html: lines.join('<br>'), actions: [{ type: 'nav', page: 'watch' }], chipText: '打开自选页' };
}

async function answerWatchAdd(text) {
  const stk = await resolveStock(text);
  if (!stk) return { html: '要加哪只？试试：<b>“加自选 600519”</b> 或 <b>“加自选 贵州茅台”</b>。' };
  const ok = addWatch({ code: stk.code, name: stk.name });
  if (ok) return { html: `✅ 已把 <b>${esc(stk.name)}</b>（${stk.code}）加入自选，我会帮你盯着它。`, actions: [{ type: 'nav', page: 'watch' }], chipText: '打开自选页' };
  return { html: `<b>${esc(stk.name)}</b> 早就在你的自选里啦～` };
}

async function answerWatchRemove(text) {
  const stk = await resolveStock(text);
  if (!stk) return { html: '要移除哪只？说说代码或名字。' };
  removeWatch(stk.code);
  return { html: `已把 <b>${esc(stk.name)}</b> 移出自选。` };
}

async function answerRecord() {
  try {
    const r = await api.recordSnapshot();
    if (r && r.ok) return { html: '📝 已把今天的情绪快照写进历史记忆，周期曲线又长了一格。' };
    return { html: '快照记录失败，稍后再试。' };
  } catch (e) {
    return { html: '快照记录失败（' + esc(e.message) + '）。' };
  }
}

async function answerStockKline(text) {
  const stk = await resolveStock(text);
  if (!stk) return { html: '想看谁的K线？输入代码或名称，比如 <b>“宁德时代 K线”</b>。' };
  return { html: `已为你调出 <b>${esc(stk.name)}</b> 的K线（MA + MACD + 成交量）📈，在行情页慢慢看。`, actions: [{ type: 'quote', code: stk.code, name: stk.name }], chipText: '打开行情页' };
}

async function answerDragonSeats(text) {
  const stk = await resolveStock(text);
  if (!stk) return { html: '想知道谁的席位？说说代码或名称，比如 <b>“网宿科技的席位”</b>。' };
  try {
    const res = await api.dragonSeats(stk.code);
    const buy = res.buy || [];
    if (!buy.length) return { html: `<b>${esc(stk.name)}</b> 今日没有龙虎榜席位数据。` };
    const top = buy.slice(0, 3);
    const html = [
      `<b>${esc(stk.name)}</b> 龙虎榜买入席位：`,
      top.map((s, i) => `${i + 1}. <b>${esc(s.dept)}</b> 买入 <b class="up">${s.buy}亿</b>${s.net ? `（净 <b class="${s.net >= 0 ? 'up' : 'down'}">${s.net > 0 ? '+' : ''}${s.net}亿</b>）` : ''}${s.win3 != null ? ` · 3日胜率 ${s.win3.toFixed(1)}%` : ''}`).join('<br>'),
      '💡 席位胜率是游资质量的照妖镜：高胜率席位聚集 = 强共识。',
    ].join('<br>');
    return { html, actions: [{ type: 'nav', page: 'ladder' }], chipText: '打开龙虎榜' };
  } catch (e) {
    return { html: '席位数据取不到（' + esc(e.message) + '），稍后再试。' };
  }
}

async function answerComfort() {
  const em = await getEmotion().catch(() => null);
  const lines = [];
  lines.push('抱抱你 🤗 账户绿的时候最考验的不是技术，是纪律。');
  lines.push('记住三件事：<b>周期提供背景</b>、<b>模型风险暴露由温度×方向×数据质量共同约束</b>、<b>冰点后的修复仍需溢价与广度确认</b>。');
  if (em) {
    lines.push(`此刻市场温度 <b>${em.engine.temp}°</b>（${em.engine.phase}），模型给出的风险暴露参考区间为 <b>${em.engine.advice.position}</b>。`);
    lines.push('要不要去「策略页」写一篇复盘日记？把今天记下来，比独自难受有用。');
  }
  return { html: lines.join('<br>'), actions: [{ type: 'nav', page: 'strategy' }], chipText: '写复盘日记' };
}

async function answerCheer() {
  const lines = [];
  lines.push('🎉 恭喜吃肉！情绪高潮期最珍贵的操作是<b>兑现</b>——把利润装进口袋的才是利润。');
  lines.push('可以参考今天的风险清单，去弱留强，别让煮熟的鸭子飞了。');
  return { html: lines.join('<br>'), actions: [{ type: 'nav', page: 'overview' }], chipText: '看风险清单' };
}

const HELP_HTML = [
  '我是 <b>蚂小财（DeepSeek 版）</b> 🧠 能读取工作台真实数据，也能直接<b>调度整个工作台</b>。标题下方会明确显示当前使用的是 DeepSeek 云端模型还是本地规则引擎。',
  '💬 <b>问数据</b>：今天情绪怎么样 / 涨停梯队如何 / 主力资金流向 / 题材主线是什么',
  '📈 <b>看个股</b>：帮我看看贵州茅台 / 600519 走势 / 宁德时代 K线',
  '🎯 <b>问策略</b>：模型风险暴露 / 有什么风险 / 当前数据质量',
  '🕹️ <b>调度全局</b>：打开涨停梯队 / 加自选茅台 / 记录今日快照 / 刷新数据',
  '🛟 网络异常或 API 不可用时，我会自动切换到内置本地智脑兜底，保证随时在线。',
].join('<br>');

const INTRO_HTML = [
  '我是 <b>蚂小财</b>，深脉工作台里的金融助手 🐜 配置 DeepSeek 官方 API 后使用云端模型，未配置时使用本地规则引擎；当前模式会显示在助手标题下方。',
  '我的日常：读<b>情绪周期</b>、盯<b>涨停梯队</b>、看<b>个股行情</b>、算<b>主力资金</b>，还能一句话调度整个工作台。',
  '想试试吗？对我说：<b>“今天情绪怎么样”</b> 或 <b>“帮我看看贵州茅台”</b>。',
].join('<br>');

const GREET_FALLBACK = '你好，我是<b>蚂小财（DeepSeek 版）</b> 🐜 这台工作台的大脑就是我本人。问我行情、情绪、策略，或直接说<b>“打开涨停梯队”</b>，我帮你操作。';

let _greeted = false;

/** 首次打开时推送问候（附今日情绪一句话，数据可达时） */
export async function ensureGreeting() {
  if (_greeted || chatStore.messages.length) return;
  _greeted = true;
  chatStore.push('bot', GREET_FALLBACK, [], true);
  try {
    const em = await api.emotion();
    if (em && em.engine && em.engine.temp != null) {
      const en = em.engine;
      const c = PHASE_COLORS[en.color] || '#e9eef8';
      chatStore.push('bot', `顺便播报一下：今天市场温度 <b style="color:${c}">${en.temp}°</b>（${esc(en.phase)}），风险暴露参考区间为 ${esc(en.advice ? en.advice.position : '--')}。`, [], true);
    }
  } catch { /* 数据不可达就只问候 */ }
}

/* ============================================================
   动作执行（全局调度）
   ============================================================ */

export function runAction(a) {
  if (!a) return;
  try {
    switch (a.type) {
      case 'nav':
        document.dispatchEvent(new CustomEvent('nav', { detail: { page: a.page } }));
        break;
      case 'quote': {
        document.dispatchEvent(new CustomEvent('nav', { detail: { page: 'market' } }));
        setTimeout(() => document.dispatchEvent(new CustomEvent('open-quote', {
          detail: { code: a.code, name: a.name || a.code },
        })), 80);
        break;
      }
      case 'watch_add': {
        const ok = addWatch({ code: a.code, name: a.name || a.code });
        toast(ok ? `蚂小财：已加入自选 ${a.name || a.code}` : '该股票已在自选中', ok ? 'ok' : 'err');
        break;
      }
      case 'watch_remove':
        removeWatch(a.code);
        toast('蚂小财：已移出自选 ' + (a.name || a.code));
        break;
      case 'refresh':
        document.dispatchEvent(new CustomEvent('refresh-all'));
        toast('蚂小财：正在刷新全部数据…');
        break;
      case 'record':
        api.recordSnapshot().then(r => toast(r && r.ok ? '情绪快照已记录' : '记录失败', r && r.ok ? 'ok' : 'err')).catch(() => {});
        break;
      default:
        break;
    }
  } catch (e) { /* 动作失败不影响对话 */ }
}

/* ============================================================
   对话主入口
   ============================================================ */

export async function answer(text) {
  const { intent } = classify(text);
  try {
    switch (intent) {
      case 'greet': return { html: GREET_HTML, actions: [] };
      case 'intro': return { html: INTRO_HTML, actions: [] };
      case 'help': return { html: HELP_HTML, actions: [] };
      case 'thanks': return { html: '不客气 😊 赚钱了记得回来报喜，亏钱了也欢迎来聊聊。', actions: [] };
      case 'bye': return { html: '再见 👋 交易时间我都在，随时召唤。', actions: [] };
      case 'comfort': return await answerComfort();
      case 'cheer': return await answerCheer();
      case 'emotion': return await answerEmotion();
      case 'advice': return await answerAdvice();
      case 'risk': return await answerRisk();
      case 'ladder': return await answerLadder();
      case 'stock_quote': return await answerStock(text);
      case 'stock_kline': return await answerStockKline(text);
      case 'dragon_seats': return await answerDragonSeats(text);
      case 'breadth': return await answerBreadth();
      case 'flow': return await answerFlow();
      case 'rank': return await answerRank();
      case 'dragon': return await answerDragon();
      case 'sector': return await answerSector();
      case 'watch_show': return await answerWatchShow();
      case 'watch_add': return await answerWatchAdd(text);
      case 'watch_remove': return await answerWatchRemove(text);
      case 'record': return await answerRecord();
      case 'journal': return { html: '复盘模板和情绪日记都在「策略页」📒 收盘后花五分钟写一写，让记忆长出年轮。', actions: [{ type: 'nav', page: 'strategy' }], chipText: '打开策略页' };
      case 'nav': {
        const page = matchPage(text);
        if (page) {
          const names = { overview: '总览', emotion: '情绪周期', market: '行情', ladder: '涨停梯队', watch: '自选', strategy: '策略', epaper: '墨水屏', datasrc: '数据源', about: '关于我' };
          return { html: `收到 🫡 已为你打开<b>${names[page]}</b>。`, actions: [{ type: 'nav', page }] };
        }
        return { html: HELP_HTML, actions: [] };
      }
      default:
        return {
          html: '这个问题我还拿不准 🤔 我是金融工作台里的助理，比较擅长：<br>· <b>情绪周期</b>：今天情绪怎么样 / 当前数据质量如何<br>· <b>个股行情</b>：帮我看看贵州茅台<br>· <b>涨停梯队</b>：龙头是谁 / 主线题材是什么<br>· <b>指挥全局</b>：打开涨停梯队 / 加自选茅台',
          actions: [],
        };
    }
  } catch (e) {
    return { html: '取数据时出了点小状况（' + esc(e.message) + '），上游可能正在限流，稍后再试～' };
  }
}

/** 尝试云端大脑（DeepSeek API），未配置时返回 null */
async function tryCloudBrain(history) {
  try {
    const data = await api.chat(history);
    if (data && data.mode === 'llm' && data.reply) return data;
    return null;
  } catch { return null; }
}

/* ============================================================
   聊天状态与视图（首页面板 + 全局抽屉共用同一对话）
   ============================================================ */

export const chatStore = {
  messages: [],   // {role:'user'|'bot', html, actions?, safe?, sourceQuestion?}
  typing: false,
  listeners: new Set(),
  _load() {
    try {
      const saved = JSON.parse(localStorage.getItem(HISTORY_KEY));
      this.messages = Array.isArray(saved) ? saved.slice(-60) : [];
    } catch { this.messages = []; }
  },
  _save() {
    const messages = this.messages.slice(-60);
    try { localStorage.setItem(HISTORY_KEY, JSON.stringify(messages)); } catch { /* 忽略 */ }
    persistChatHistory(messages);
  },
  push(role, html, actions, safe, sourceQuestion) {
    this.messages.push({ role, html, actions, safe: !!safe, sourceQuestion });
    this._save();
    this.notify();
  },
  setTyping(v) {
    this.typing = v;
    this.notify();
  },
  listen(fn) {
    this.listeners.add(fn);
    fn();
    return () => this.listeners.delete(fn);
  },
  notify() {
    this.listeners.forEach(fn => { try { fn(); } catch { /* 忽略 */ } });
  },
  clear() {
    this.messages = [];
    this._save();
    this.notify();
  },
  reload() {
    this._load();
    this.notify();
  },
};

chatStore._load();
document.addEventListener('profile-synced', () => chatStore.reload());

/** 云端/纯文本内容渲染：转义 + 极简 Markdown */
function mdPlain(s) {
  let t = esc(s);
  t = t.replace(/\*\*(.+?)\*\*/g, '<b>$1</b>');
  t = t.replace(/\n/g, '<br>');
  return t;
}

const ANT_SVG = `<svg viewBox="0 0 48 48" style="width:30px;height:30px"><g fill="none" stroke="#7cb0ff" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M16.5 8.5C15 5 11.5 3.5 9.5 5S8 10 11 11.5M31.5 8.5C33 5 36.5 3.5 38.5 5S40 10 37 11.5"/><circle cx="24" cy="17.5" r="7.2"/><circle cx="24" cy="33" r="9.5"/><path d="M24 24.7v-1M12 29.5c-2.5 2.5-2.5 5.5 0 8M36 29.5c2.5 2.5 2.5 5.5 0 8M12.5 21l-6-3M35.5 21l6-3"/><circle cx="21" cy="16.6" r="1.5" fill="#7cb0ff" stroke="none"/><circle cx="27" cy="16.6" r="1.5" fill="#7cb0ff" stroke="none"/><path d="M21.5 20.8c1.5 1.1 3.5 1.1 5 0" stroke="#7cb0ff"/></g></svg>`;

function botBubble(html, actions, safe, sourceQuestion) {
  const acts = (actions || []).map(a => {
    let label = '';
    if (a.type === 'nav') {
      const names = { overview: '总览', emotion: '情绪周期', market: '行情', ladder: '涨停梯队', watch: '自选', strategy: '策略', epaper: '墨水屏', datasrc: '数据源', about: '关于我' };
      label = '打开' + (names[a.page] || a.page);
    } else if (a.type === 'quote') label = `看${a.name || a.code}K线`;
    else if (a.type === 'watch_add') label = '加自选';
    else if (a.type === 'watch_remove') label = '移出自选';
    else if (a.type === 'refresh') label = '刷新数据';
    else if (a.type === 'record') label = '记录快照';
    if (!label) return '';
    return `<button class="chat-act" data-act='${JSON.stringify(a).replace(/'/g, '&#39;')}'>${esc(label)}</button>`;
  }).join('');
  const body = safe ? html : mdPlain(html);
  // 只转交用户原问题；本地助手回答属于参考材料，不能冒充用户意图。
  const askBtn = EMBEDDED && sourceQuestion
    ? `<button class="chat-act chat-act-ask" data-ask="${esc(String(sourceQuestion).slice(0, 400))}">🧠 让 DeepSeek 深入分析</button>`
    : '';
  const copyBtn = `<button class="chat-act" data-copy="${esc(html.replace(/<[^>]+>/g, ''))}">复制</button>`;
  return `<div class="msg-row bot">
    <div class="bot-avatar">${ANT_SVG.replace('width:30px;height:30px', 'width:26px;height:26px')}</div>
    <div><div class="bubble">${body}</div><div class="chat-acts">${acts}${copyBtn}${askBtn}</div></div>
  </div>`;
}

function userBubble(text) {
  return `<div class="msg-row user"><div class="bubble">${esc(text)}</div></div>`;
}

function typingBubble() {
  return `<div class="msg-row bot">
    <div class="bot-avatar">${ANT_SVG.replace('width:30px;height:30px', 'width:26px;height:26px')}</div>
    <div><div class="bubble typing"><span></span><span></span><span></span></div></div>
  </div>`;
}

export function createChatView(root) {
  const CHIPS = ['今天情绪怎么样', '涨停梯队如何', '当前数据质量', '有什么风险', '主力资金流向', '帮我看看贵州茅台', '加自选 宁德时代', '打开涨停梯队'];
  root.classList.add('chat-view');
  root.innerHTML = `
    <div class="chat-msgs" data-msgs></div>
    <div class="chat-chips" data-chips>
      ${CHIPS.map(c => `<button class="chip-btn">${esc(c)}</button>`).join('')}
      <button class="chip-btn chip-clear" data-clear title="清空对话">🗑 清空</button>
    </div>
    <div class="chat-input-row">
      <input data-input placeholder="对蚂小财说：看行情 / 查情绪 / 调度页面…" maxlength="120" autocomplete="off">
      <button class="chat-send" data-send title="发送">➤</button>
    </div>`;

  const msgsEl = root.querySelector('[data-msgs]');
  const inputEl = root.querySelector('[data-input]');
  const sendBtn = root.querySelector('[data-send]');

  const render = () => {
    msgsEl.innerHTML = chatStore.messages
      .map(m => m.role === 'user' ? userBubble(m.html) : botBubble(m.html, m.actions, m.safe, m.sourceQuestion)).join('')
      + (chatStore.typing ? typingBubble() : '');
    msgsEl.scrollTop = msgsEl.scrollHeight;
  };
  const unsub = chatStore.listen(render);

  async function send(text) {
    const t = text.trim();
    if (!t || chatStore.typing) return;
    chatStore.push('user', t);
    chatStore.setTyping(true);
    const started = Date.now();

    // 云端大脑优先（未配置时后端秒回 local 模式）
    const history = chatStore.messages.slice(-10)
      .filter(m => m.role !== 'typing')
      .map(m => ({ role: m.role === 'user' ? 'user' : 'assistant', content: m.role === 'user' ? m.html : m.html.replace(/<[^>]+>/g, '') }));
    let result = null;
    const cloud = await tryCloudBrain(history);
    if (cloud) {
      result = { html: cloud.reply, actions: cloud.actions || [], safe: false };
    } else {
      const local = await answer(t);
      result = { html: local.html, actions: local.actions || [], safe: true };
    }

    const wait = Math.max(0, 650 - (Date.now() - started));
    await new Promise(r => setTimeout(r, wait));
    chatStore.setTyping(false);
    chatStore.push('bot', result.html, result.actions || [], result.safe, t);
    (result.actions || []).forEach(runAction);
  }

  inputEl.addEventListener('keydown', e => { if (e.key === 'Enter') { send(inputEl.value); inputEl.value = ''; } });
  sendBtn.addEventListener('click', () => { send(inputEl.value); inputEl.value = ''; });
  root.querySelector('[data-chips]').addEventListener('click', e => {
    const b = e.target.closest('.chip-btn');
    if (!b) return;
    if (b.dataset.clear !== undefined) {
      chatStore.clear();
      toast('对话已清空');
      return;
    }
    send(b.textContent);
  });
  msgsEl.addEventListener('click', e => {
    const askBtn = e.target.closest('[data-ask]');
    if (askBtn) {
      const sent = askDeepSeek({
        question: `请继续深入分析这个问题：${askBtn.dataset.ask}`,
        context: { intent: 'continue-deeppulse-question', originalQuestion: askBtn.dataset.ask },
      });
      if (!sent) toast('请在 DeepSeek Harness 中打开深脉后使用', 'err');
      return;
    }
    const copyBtn = e.target.closest('[data-copy]');
    if (copyBtn) {
      navigator.clipboard?.writeText(copyBtn.dataset.copy || '').then(
        () => toast('已复制到剪贴板'),
        () => toast('复制失败', 'err'),
      );
      return;
    }
    const btn = e.target.closest('.chat-act');
    if (btn) {
      try { runAction(JSON.parse(btn.dataset.act)); } catch { /* 忽略 */ }
    }
  });

  return { send, destroy: unsub };
}
