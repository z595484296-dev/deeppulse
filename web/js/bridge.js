/* 深脉 DeepPulse — 与壳层（深脉主应用）的双向桥。
   工作台既可独立运行，也可作为主应用的一级视图（/deeppulse/）运行。
   桥协议（window.postMessage）：
     工作台 → 壳层： {type:'dp-exit'} 返回会话视图
                    {type:'dp-ask', version:2, requestId, question, context}
     壳层 → 工作台： {type:'dp-ask-result', requestId, ok, error?}
                    {type:'dp-nav', page?, code?, name?} 跳转页面/个股 */

import { applyChartTheme } from './charts.js?v=1.4.2';

export const EMBEDDED = (() => {
  try {
    if (typeof window === 'undefined') return false;
    if (window.self === window.top) return false;
    return location.pathname.startsWith('/deeppulse/') || location.port === '3080';
  } catch { return false; }
})();

let contextProvider = () => ({});

/** 注册当前页面上下文提供器；发送时读取，避免把轮询数据复制到桥状态。 */
export function setBridgeContextProvider(provider) {
  contextProvider = typeof provider === 'function' ? provider : () => ({});
}

function requestId() {
  try { return crypto.randomUUID(); } catch { return `dp-${Date.now()}-${Math.random().toString(16).slice(2)}`; }
}

export function boundedContext(value) {
  try {
    const plain = JSON.parse(JSON.stringify(value || {}));
    if (JSON.stringify(plain).length <= 16000) return plain;
    // 渐进压缩：优先保留分析事实，先缩短历史和指标解读，避免超限时把整个市场上下文清空。
    const emotion = plain.emotionAnalysis || {};
    const reduced = {
      ...plain,
      emotionAnalysis: {
        ...emotion,
        history: (emotion.history || []).slice(-8),
        signals: (emotion.signals || []).map(s => ({
          key: s.key, name: s.name, value: s.value, unit: s.unit,
          score: s.score, weight: s.weight, contribution: s.contribution,
          available: s.available,
        })),
      },
      contextTruncated: { value: true, sections: ['history:8', 'signalNotes'] },
    };
    if (JSON.stringify(reduced).length <= 16000) return reduced;
    const market = reduced.market || {};
    const sourceVerification = market.sourceVerification || {};
    const tdxLocal = sourceVerification.tdxLocal || {};
    return {
      page: reduced.page, pageTitle: reduced.pageTitle, asOf: reduced.asOf,
      selectedSecurity: reduced.selectedSecurity,
      market: {
        ...market,
        sourceVerification: {
          ...sourceVerification,
          tdxLocal: { ...tdxLocal, fields: [] },
        },
      },
      emotionAnalysis: {
        modelVersion: emotion.modelVersion, formula: emotion.formula,
        scoreRange: emotion.scoreRange, phaseThresholds: emotion.phaseThresholds,
        positionNature: emotion.positionNature, transitionCalibrated: emotion.transitionCalibrated,
        raw: emotion.raw, missing: emotion.missing,
      },
      indices: reduced.indices, sources: reduced.sources, disclaimer: reduced.disclaimer,
      contextTruncated: { value: true, sections: ['history', 'signals', 'tdxFields'] },
    };
  } catch { return {}; }
}

/** 应用主题并通知图表层重渲染。 */
export function applyTheme(light) {
  try {
    document.body.classList.toggle('light', !!light);
    applyChartTheme(!!light);
    document.dispatchEvent(new CustomEvent('theme-changed'));
  } catch { /* 忽略 */ }
}

function post(msg) {
  if (!EMBEDDED) return;
  try { window.parent.postMessage(msg, '*'); } catch { /* 忽略 */ }
}

/** 返回主应用会话视图。 */
export function exitToSession() {
  post({ type: 'dp-exit' });
}

/** 把问题和可信来源上下文送入主应用当前会话；壳层确认接收后才切回会话。 */
export function askDeepSeek(input) {
  if (!EMBEDDED) return null;
  const spec = typeof input === 'string' ? { question: input } : (input || {});
  const question = String(spec.question ?? spec.text ?? '').trim().slice(0, 2000);
  if (!question) return null;
  let provided = {};
  try { provided = contextProvider() || {}; } catch { /* 提供器失败不阻断发送 */ }
  const id = requestId();
  post({
    type: 'dp-ask', version: 2, requestId: id, question,
    context: boundedContext({ ...provided, ...(spec.context || {}) }),
  });
  return id;
}

/** 壳层导航指令（主应用深链 → 工作台页面）与主题同步。 */
export function initBridge() {
  window.addEventListener('message', (e) => {
    const d = (e.data ?? {});
    if (!d) return;
    if (EMBEDDED && e.source !== window.parent) return;
    if (d.type === 'dp-theme') {
      // 主题跟随主应用（K线红绿语义不变，轴文字/tooltip 随主题）
      applyTheme(d.theme === 'light');
      return;
    }
    if (d.type === 'dp-ask-result') {
      document.dispatchEvent(new CustomEvent('harness-ask-result', { detail: d }));
      return;
    }
    if (d.type !== 'dp-nav') return;
    try {
      if (d.page) document.dispatchEvent(new CustomEvent('nav', { detail: { page: d.page } }));
      if (d.code) {
        setTimeout(() => document.dispatchEvent(new CustomEvent('open-quote', {
          detail: { code: d.code, name: d.name || d.code },
        })), 60);
      }
    } catch { /* 忽略 */ }
  });
}
