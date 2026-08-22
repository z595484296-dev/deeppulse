/* 深脉 DeepPulse — 助手回答时点与失效判定（纯规则层） */

const MARKET_TERMS = [
  '今天', '当前', '现在', '情绪', '温度', '风险', '仓位', '行情', '股价', '价格',
  '涨停', '跌停', '炸板', '连板', '梯队', '主线', '板块', '资金', '主力', '宽度',
  '涨幅', '龙虎榜', '席位', '谁在买', '数据质量', '公告', '帮我看看',
];

export function marketSensitiveQuestion(value) {
  const text = String(value || '').trim();
  return !!text && MARKET_TERMS.some(term => text.includes(term));
}

export function marketSnapshotFromState(appState = {}, now = Date.now()) {
  const emotion = appState?.emotion || {};
  const engine = emotion?.engine || {};
  const dataDate = String(emotion?.date || '').trim();
  const rawTemp = engine?.temp;
  const temp = rawTemp === null || rawTemp === undefined || rawTemp === '' ? null : Number(rawTemp);
  const phase = String(engine?.phase || '').trim();
  if (!dataDate && !Number.isFinite(temp) && !phase) return null;
  const asOf = Number(appState?.lastUpdate);
  return {
    dataDate: dataDate || null,
    temp: Number.isFinite(temp) ? temp : null,
    phase: phase || null,
    asOf: Number.isFinite(asOf) && asOf > 0 ? asOf : Number(now),
  };
}

export function classifyMessageFreshness(message = {}, currentSnapshot = null, now = Date.now()) {
  if (message.role !== 'bot' || !marketSensitiveQuestion(message.sourceQuestion)) {
    return { status: 'timeless', stale: false };
  }
  const recorded = message.marketSnapshot;
  if (!recorded || typeof recorded !== 'object') {
    return { status: 'unknown', stale: true, reason: 'missing_snapshot' };
  }
  if (!currentSnapshot || typeof currentSnapshot !== 'object') {
    return { status: 'recorded', stale: false, recorded };
  }
  const recordedTemp = recorded.temp === null || recorded.temp === undefined || recorded.temp === ''
    ? null : Number(recorded.temp);
  const currentTemp = currentSnapshot.temp === null || currentSnapshot.temp === undefined || currentSnapshot.temp === ''
    ? null : Number(currentSnapshot.temp);
  const changed = (recorded.dataDate && currentSnapshot.dataDate
      && recorded.dataDate !== currentSnapshot.dataDate)
    || (Number.isFinite(recordedTemp) && Number.isFinite(currentTemp) && recordedTemp !== currentTemp)
    || (recorded.phase && currentSnapshot.phase && recorded.phase !== currentSnapshot.phase);
  if (changed) return { status: 'stale', stale: true, reason: 'market_changed', recorded, current: currentSnapshot };
  const createdAt = Number(message.createdAt || recorded.asOf || 0);
  if (createdAt > 0 && Number(now) - createdAt > 15 * 60 * 1000) {
    return { status: 'stale', stale: true, reason: 'time_elapsed', recorded, current: currentSnapshot };
  }
  return { status: 'current', stale: false, recorded, current: currentSnapshot };
}

export function historyForCurrentMarket(messages = [], currentSnapshot = null, now = Date.now()) {
  return (Array.isArray(messages) ? messages : []).filter(message => {
    if (!message || message.role !== 'bot') return !!message;
    return !classifyMessageFreshness(message, currentSnapshot, now).stale;
  });
}
