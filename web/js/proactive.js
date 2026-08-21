/* 深脉 DeepPulse — 主动简报规则层
   只把已取得的市场事实整理成优先级和研究任务；不生成买卖指令。 */

const finite = value => {
  if (value === null || value === undefined || value === '') return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
};

function compact(text, max = 72) {
  const value = String(text || '').replace(/\s+/g, ' ').trim();
  return value.length > max ? value.slice(0, max - 1) + '…' : value;
}

function localDateOf(date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function minutesOfDay(date) {
  return date.getHours() * 60 + date.getMinutes();
}

function freshnessPolicy(marketState, now, dataDate, asOfMs) {
  if (!Number.isFinite(asOfMs) || asOfMs > now.getTime() + 60_000) {
    return { stale: true, label: '更新时点无效' };
  }
  const ageMs = now.getTime() - asOfMs;
  const sameDay = dataDate === localDateOf(now);
  if (marketState === 'open') {
    return { stale: !sameDay || ageMs > 3 * 60 * 1000, label: '盘中 3 分钟' };
  }
  if (marketState === 'break') {
    return { stale: !sameDay || ageMs > 2 * 60 * 60 * 1000, label: '午间快照' };
  }
  const weekday = now.getDay() >= 1 && now.getDay() <= 5;
  const afterClose = weekday && minutesOfDay(now) >= 15 * 60;
  if (marketState === 'closed' && afterClose) {
    return { stale: !sameDay || ageMs > 18 * 60 * 60 * 1000, label: '当日收盘快照' };
  }
  // 盘前、夜间、周末与节假日允许展示最近交易日快照，并明确其性质。
  return { stale: ageMs > 4 * 24 * 60 * 60 * 1000, label: sameDay ? '最近快照' : '上一交易日快照' };
}

function hashText(text) {
  let hash = 0x811c9dc5;
  for (let index = 0; index < text.length; index += 1) {
    hash ^= text.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193);
  }
  return (hash >>> 0).toString(36);
}

function uniqueTexts(items) {
  return [...new Set((items || []).map(item => compact(
    typeof item === 'string' ? item : item && item.text, 96,
  )).filter(Boolean))];
}

function meaningfulSignal(text) {
  const value = compact(text, 96);
  return value && !/(未触发.*风险|无显著风险|暂无.*风险|无异常|正常运行)/.test(value);
}

function periodLabel(marketState) {
  return ({ open: '盘中', pre: '开盘前', break: '午间', closed: '收盘后' })[marketState] || '当前';
}

function action(id, tone, title, detail, page, label) {
  return { id, tone, title, detail: compact(detail, 92), page, label };
}

/**
 * 构建无需云端模型也能工作的主动简报。返回值可直接用于页面渲染和 DeepSeek 上下文。
 */
export function buildProactiveBrief(input = {}) {
  const emotion = input.emotion || {};
  const engine = emotion.engine || {};
  const raw = engine.raw || {};
  const dynamics = engine.dynamics || {};
  const watchlist = Array.isArray(input.watchlist) ? input.watchlist : [];
  const alerts = Array.isArray(input.alerts) ? input.alerts : [];
  const journal = Array.isArray(input.journal) ? input.journal : [];
  const indices = (Array.isArray(input.indices) ? input.indices : [])
    .filter(row => row && finite(row.pct) !== null);
  const dataDate = emotion.date || engine.date || '';
  const marketState = input.marketState || 'closed';
  const period = periodLabel(marketState);
  const now = input.now instanceof Date ? input.now : new Date(input.now || Date.now());
  const asOfMs = typeof input.asOf === 'number' ? input.asOf : Date.parse(String(input.asOf || ''));
  const freshness = freshnessPolicy(marketState, now, dataDate, asOfMs);
  const stale = freshness.stale;
  const confidence = finite(engine.confidence);
  const coverage = finite(engine.coverage);
  const temp = finite(engine.temp);
  const delta1 = finite(dynamics.delta1);
  const missingCore = temp === null || coverage === null || confidence === null || typeof engine.actionable !== 'boolean';
  const degraded = !dataDate || stale || missingCore || engine.degraded === true || engine.actionable === false
    || (coverage !== null && coverage < 60) || (confidence !== null && confidence < 60);
  const riskFlags = (engine.flags || []).filter(item => item && (item.type === 'warn' || item.type === 'risk'));
  const risks = uniqueTexts([...(engine.risks || []), ...riskFlags]).filter(meaningfulSignal);
  const divergences = uniqueTexts(engine.divergences || []).filter(meaningfulSignal);
  const missing = uniqueTexts(engine.missing || []).slice(0, 8);
  const pendingAlerts = alerts.filter(item => item && !item.triggered);
  const hasJournal = !!dataDate && journal.some(item => item && item.date === dataDate && String(item.text || '').trim());
  const hasSnapshot = !!dataDate && (emotion.history || []).some(item => item && item.date === dataDate);

  let headline = '市场信息正在汇总，先确认数据是否就绪';
  let summary = '深脉会把可信数据、风险信号和你的关注项整理成不超过三件待处理事项。';
  let tone = 'neutral';
  let triggerReason = '打开总览，生成当前研究清单';

  if (degraded) {
    headline = '当前数据不足以支持完整判断，先修复数据链路';
    const reason = stale ? '数据时点已过期或交易日不匹配' : missingCore ? '核心质量字段缺失' : '覆盖率或数据质量分不足';
    summary = `${reason}；覆盖率 ${coverage ?? '--'}% · 数据质量分 ${confidence ?? '--'}。简报已暂停方向性结论，只保留数据修复与核验任务。`;
    tone = 'warn';
    triggerReason = reason;
  } else if (dataDate) {
    const phase = engine.phase || '阶段待确认';
    const direction = delta1 === null ? '' : delta1 >= 3 ? '升温' : delta1 <= -3 ? '降温' : '震荡';
    if (divergences.length || risks.length) {
      headline = `情绪${direction ? direction + '至' : '处于'}${phase}，先核对结构风险`;
      summary = compact(divergences[0] || risks[0], 110);
      tone = 'risk';
      triggerReason = `发现${divergences.length ? '结构背离' : '风险信号'}`;
    } else {
      headline = `情绪${direction ? direction + '至' : '处于'}${phase}，按当前阶段验证持续性`;
      summary = engine.phase_desc || engine.narrative || '暂无需要立即关注的新变化，继续观察关键指标是否延续。';
      tone = direction === '升温' ? 'positive' : direction === '降温' ? 'warn' : 'neutral';
      triggerReason = delta1 === null ? '生成阶段研究清单' : `情绪${direction || '状态'}变化`;
    }
  }

  const actions = [];
  if (degraded) {
    actions.push(action('repair-data', 'warn', '先修复数据', '检查缺失项、来源状态和本地增强链路，数据恢复前不扩大结论。', 'datasrc', '查看数据源'));
  } else {
    const primaryRisk = divergences[0] || risks[0];
    if (primaryRisk) {
      actions.push(action('verify-risk', 'risk', '核对风险与反证', primaryRisk, 'emotion', '查看依据'));
    }
    if (marketState === 'closed' && hasSnapshot && !hasJournal) {
      actions.push(action('close-review', 'accent', '完成今日复盘', '快照已经记录，但今日复盘尚未保存；补充判断、反证和明日观察点。', 'strategy', '去复盘'));
    }
    if (pendingAlerts.length) {
      actions.push(action('pending-alerts', 'neutral', `检查 ${pendingAlerts.length} 个等待中提醒`, '确认触发条件仍然有效；过时提醒应删除或重新设置。', 'watch', '管理提醒'));
    } else if (watchlist.length) {
      actions.push(action('review-watch', 'neutral', `检查 ${watchlist.length} 只关注股`, '结合当前情绪阶段，核对关注逻辑、公司公告与失效条件。', 'watch', '查看自选'));
    }
    if (!actions.length) {
      actions.push(action('inspect-market', 'neutral', marketState === 'open' ? '观察盘中结构' : '建立下一步观察点',
        marketState === 'open' ? '查看市场宽度、涨跌停与主线梯队是否同步。' : '从情绪、行情或自选中选择一个需要持续验证的问题。',
        marketState === 'open' ? 'emotion' : 'watch', marketState === 'open' ? '查看情绪' : '设置关注'));
    }
  }

  const sortedIndices = [...indices].sort((a, b) => finite(b.pct) - finite(a.pct));
  const leader = sortedIndices[0];
  const laggard = sortedIndices[sortedIndices.length - 1];
  const facts = [];
  if (temp !== null) facts.push({ label: '情绪', value: `${Math.round(temp)}° ${engine.phase || '--'}` });
  if (delta1 !== null) facts.push({ label: '变化', value: `${delta1 > 0 ? '+' : ''}${delta1.toFixed(1)}°` });
  if (confidence !== null) facts.push({ label: '数据质量分', value: `${Math.round(confidence)}` });
  if (leader && laggard) facts.push({
    label: '指数结构',
    value: `${leader.name || leader.code} ${finite(leader.pct) >= 0 ? '+' : ''}${finite(leader.pct).toFixed(2)}% / ${laggard.name || laggard.code} ${finite(laggard.pct) >= 0 ? '+' : ''}${finite(laggard.pct).toFixed(2)}%`,
  });
  if (finite(raw.up) !== null && finite(raw.down) !== null) facts.push({ label: '涨跌家数', value: `${raw.up} : ${raw.down}` });

  const topActions = actions.slice(0, 3);
  const evidence = [
    `触发 ${triggerReason}`,
    dataDate ? `数据日 ${dataDate}` : '数据日待确认',
    `${period}生成`,
    Number.isFinite(asOfMs) ? `更新 ${new Date(asOfMs).toLocaleTimeString('zh-CN', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' })}` : '更新时点待确认',
    stale ? '有效期 已失效' : `有效期 ${freshness.label}`,
    `覆盖 ${coverage ?? '--'}%`,
    `数据质量分 ${confidence ?? '--'}`,
    missing.length ? `缺失 ${missing.join('、')}` : '缺失项 无',
    '数据质量分衡量覆盖与来源质量，不代表预测准确率',
  ];
  const identity = JSON.stringify({ schema: 1, dataDate, status: degraded ? 'degraded' : 'ready', headline, summary, triggerReason, missing, facts, actions: topActions });
  const contentHash = hashText(identity);
  // 注意力身份只随阶段、风险、降级与任务变化；普通行情数值刷新不会反复打断已读状态。
  const attentionIdentity = JSON.stringify({
    schema: 2, dataDate, status: degraded ? 'degraded' : 'ready', phase: engine.phase || '',
    risks, divergences, missing, actions: topActions.map(item => item.id),
  });
  const attentionHash = hashText(attentionIdentity);
  const id = `v2:${dataDate || 'pending'}:${attentionHash}`;
  const prompt = [
    `请展开这份深脉${period}主动简报。`,
    `标题：${headline}`,
    `事实摘要：${summary}`,
    `待处理：${topActions.map(item => `${item.title}（${item.detail}）`).join('；')}`,
    '请严格区分事实与推断，解释优先级，给出反证条件和下一步应查的数据；不要给出确定性买卖指令。',
  ].join('\n');

  return {
    id, contentHash, attentionHash, period, tone, degraded, stale, dataDate, headline, summary, triggerReason, missing,
    status: degraded ? '数据受限' : '数据状态：完整',
    facts: facts.slice(0, 4), actions: topActions, evidence, prompt,
  };
}
