/* 深脉 DeepPulse — 与壳层（深脉主应用）的双向桥。
   工作台既可独立运行，也可作为主应用的一级视图（/deeppulse/）运行。
   桥协议（window.postMessage）：
     工作台 → 壳层： {type:'dp-exit'} 返回会话视图
                    {type:'dp-ask', version:2, requestId, question, context}
                    {type:'dp-generate', version:3, requestId, question, context}
     壳层 → 工作台： {type:'dp-ask-result', requestId, ok, error?}
                    {type:'dp-generate-result', requestId, ok, reply?, error?}
                    {type:'dp-nav', page?, code?, name?} 跳转页面/个股 */

import { applyChartTheme } from './charts.js?v=1.41.0';

export const EMBEDDED = (() => {
  try {
    if (typeof window === 'undefined') return false;
    if (window.self === window.top) return false;
    return location.pathname.startsWith('/deeppulse/') || location.port === '3080';
  } catch { return false; }
})();

let contextProvider = () => ({});
const pendingGenerations = new Map();
const GENERATION_TIMEOUT_MS = 190000;

/** 注册当前页面上下文提供器；发送时读取，避免把轮询数据复制到桥状态。 */
export function setBridgeContextProvider(provider) {
  contextProvider = typeof provider === 'function' ? provider : () => ({});
}

function requestId() {
  try { return crypto.randomUUID(); } catch { return `dp-${Date.now()}-${Math.random().toString(16).slice(2)}`; }
}

function trimHypothesisEvidence(item, limit = 8) {
  return item ? { ...item, evidenceCandidates: (item.evidenceCandidates || []).slice(0, limit) } : item;
}

function trimResultCard(card) {
  if (!card || typeof card !== 'object') return null;
  return {
    modelVersion: card.modelVersion,
    workflowId: card.workflowId,
    runId: card.runId,
    generatedAt: card.generatedAt,
    title: card.title,
    question: card.question,
    target: card.target,
    summary: card.summary,
    sources: (card.sources || []).slice(0, 5).map(source => ({
      sourceId: source.sourceId,
      status: source.status,
      summary: source.summary,
      upstream: source.upstream,
      fetchedAt: source.fetchedAt,
      evidenceCount: source.evidenceCount,
      lineageGroups: (source.lineageGroups || []).slice(0, 8),
      evidenceDates: (source.evidenceDates || []).slice(0, 5),
      freshness: source.freshness,
    })),
    gaps: (card.gaps || []).slice(0, 12),
    sameUpstream: (card.sameUpstream || []).slice(0, 8),
    reviewState: card.reviewState,
    automaticConclusion: false,
    automaticTradingAction: false,
    boundary: card.boundary,
  };
}

function trimRunComparison(comparison) {
  if (!comparison || typeof comparison !== 'object') return null;
  return {
    modelVersion: comparison.modelVersion,
    previousRunId: comparison.previousRunId,
    currentRunId: comparison.currentRunId,
    previousRanAt: comparison.previousRanAt,
    currentRanAt: comparison.currentRanAt,
    deltas: comparison.deltas,
    sourceChanges: (comparison.sourceChanges || []).slice(0, 10),
    changedSourceCount: comparison.changedSourceCount,
    automaticConclusion: false,
    automaticTradingAction: false,
    boundary: comparison.boundary,
  };
}

function trimWorkflowLineage(lineage) {
  if (!lineage || typeof lineage !== 'object') return null;
  return {
    modelVersion: lineage.modelVersion,
    familyId: lineage.familyId,
    methodVersion: lineage.methodVersion,
    originKind: lineage.originKind,
    originWorkflowId: lineage.originWorkflowId,
    originMethodVersion: lineage.originMethodVersion,
    originCreatedAt: lineage.originCreatedAt,
    changeSummary: (lineage.changeSummary || []).slice(0, 8),
    historyImmutable: lineage.historyImmutable === true,
    automaticConclusion: false,
  };
}

function trimEvidenceTimeline(timeline) {
  if (!timeline || typeof timeline !== 'object') return null;
  return {
    modelVersion: timeline.modelVersion,
    summary: timeline.summary,
    items: (timeline.items || []).slice(0, 20).map(row => ({
      id: row.id, runId: row.runId, sourceId: row.sourceId,
      observedAt: row.observedAt, fetchedAt: row.fetchedAt, dataAt: row.dataAt,
      status: row.status, label: row.label, upstream: row.upstream,
      independentGroup: row.independentGroup,
    })),
    historyImmutable: timeline.historyImmutable === true,
    automaticConclusion: false,
    boundary: timeline.boundary,
  };
}

function trimWorkflowEvidence(item, resultLimit = 5, evidenceLimit = 5) {
  if (!item) return item;
  const runs = (item.runs || (item.latestRun ? [item.latestRun] : [])).slice(-1).map(run => ({
    ...run,
    resultCard: trimResultCard(run.resultCard),
    results: (run.results || []).slice(0, resultLimit).map(result => ({
      ...result, evidence: (result.evidence || []).slice(0, evidenceLimit),
    })),
  }));
  return {
    ...item,
    templateSpec: item.templateSpec ? {
      modelVersion: item.templateSpec.modelVersion,
      parameters: (item.templateSpec.parameters || []).slice(0, 4),
      titleTemplate: item.templateSpec.titleTemplate,
      questionTemplate: item.templateSpec.questionTemplate,
      originalTargetType: item.templateSpec.originalTargetType,
      requiresFreshPreview: item.templateSpec.requiresFreshPreview === true,
      inheritsRuns: false,
      inheritsResultCard: false,
      inheritsConclusion: false,
      boundary: item.templateSpec.boundary,
    } : null,
    runComparison: trimRunComparison(item.runComparison),
    lineage: trimWorkflowLineage(item.lineage),
    evidenceTimeline: trimEvidenceTimeline(item.evidenceTimeline),
    runs,
    latestRun: runs[0] || null,
  };
}

export function boundedContext(value) {
  try {
    const plain = JSON.parse(JSON.stringify(value || {}));
    if (JSON.stringify(plain).length <= 16000) return plain;
    // 渐进压缩：优先保留分析事实，先缩短历史和指标解读，避免超限时把整个市场上下文清空。
    const emotion = plain.emotionAnalysis || {};
    const reduced = {
      ...plain,
      eventImpact: plain.eventImpact ? {
        ...plain.eventImpact,
        items: (plain.eventImpact.items || []).slice(0, 4),
      } : null,
      researchHypotheses: plain.researchHypotheses ? {
        ...plain.researchHypotheses,
        items: (plain.researchHypotheses.items || []).slice(0, 5).map(item => trimHypothesisEvidence(item, 8)),
      } : null,
      researchWorkflows: plain.researchWorkflows ? {
        ...plain.researchWorkflows,
        items: (plain.researchWorkflows.items || []).slice(0, 8).map(item => trimWorkflowEvidence(item)),
      } : null,
      researchSuggestions: plain.researchSuggestions ? {
        ...plain.researchSuggestions,
        items: (plain.researchSuggestions.items || []).slice(0, 5),
      } : null,
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
      intent: reduced.intent,
      proactiveBrief: reduced.proactiveBrief ? {
        ...reduced.proactiveBrief,
        facts: (reduced.proactiveBrief.facts || []).slice(0, 4),
        actions: (reduced.proactiveBrief.actions || []).slice(0, 3),
        evidence: (reduced.proactiveBrief.evidence || []).slice(0, 6),
      } : null,
      eventImpact: reduced.eventImpact ? {
        enabled: reduced.eventImpact.enabled,
        generatedAt: reduced.eventImpact.generatedAt,
        summary: reduced.eventImpact.summary,
        authorization: reduced.eventImpact.authorization,
        method: reduced.eventImpact.method,
        items: (reduced.eventImpact.items || []).slice(0, 2),
      } : null,
      researchHypotheses: reduced.researchHypotheses ? {
        modelVersion: reduced.researchHypotheses.modelVersion,
        summary: reduced.researchHypotheses.summary,
        boundary: reduced.researchHypotheses.boundary,
        items: (reduced.researchHypotheses.items || []).slice(0, 2).map(item => trimHypothesisEvidence(item, 5)),
      } : null,
      researchHypothesis: trimHypothesisEvidence(reduced.researchHypothesis, 12) || null,
      researchCockpit: reduced.researchCockpit ? {
        generatedAt: reduced.researchCockpit.generatedAt,
        summary: reduced.researchCockpit.summary,
        map: reduced.researchCockpit.map,
        focus: (reduced.researchCockpit.focus || []).slice(0, 3),
        method: reduced.researchCockpit.method,
        boundary: reduced.researchCockpit.boundary,
        automaticGoalInference: reduced.researchCockpit.automaticGoalInference,
        automaticTradingActions: reduced.researchCockpit.automaticTradingActions,
      } : null,
      researchCockpitItem: reduced.researchCockpitItem || null,
      researchMemory: reduced.researchMemory ? {
        modelVersion: reduced.researchMemory.modelVersion,
        summary: reduced.researchMemory.summary,
        preferences: reduced.researchMemory.preferences,
        items: (reduced.researchMemory.items || []).slice(0, 6),
        patterns: reduced.researchMemory.patterns,
        boundary: reduced.researchMemory.boundary,
        automaticCausalInference: reduced.researchMemory.automaticCausalInference,
        automaticStrategyChange: reduced.researchMemory.automaticStrategyChange,
        automaticTradingAction: reduced.researchMemory.automaticTradingAction,
      } : null,
      researchMemoryItem: reduced.researchMemoryItem || null,
      researchWorkflows: reduced.researchWorkflows ? {
        modelVersion: reduced.researchWorkflows.modelVersion,
        summary: reduced.researchWorkflows.summary,
        items: (reduced.researchWorkflows.items || []).slice(0, 4).map(item => trimWorkflowEvidence(item, 4, 3)),
        boundary: reduced.researchWorkflows.boundary,
        permissions: reduced.researchWorkflows.permissions,
      } : null,
      researchWorkflow: trimWorkflowEvidence(reduced.researchWorkflow, 5, 5) || null,
      researchSuggestions: reduced.researchSuggestions ? {
        modelVersion: reduced.researchSuggestions.modelVersion,
        generatedAt: reduced.researchSuggestions.generatedAt,
        summary: reduced.researchSuggestions.summary,
        items: (reduced.researchSuggestions.items || []).slice(0, 3),
        boundary: reduced.researchSuggestions.boundary,
        contract: reduced.researchSuggestions.contract,
      } : null,
      akshareResearch: reduced.akshareResearch || null,
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
      contextTruncated: { value: true, sections: ['history', 'signals', 'tdxFields', 'workflowEvidence'] },
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

/**
 * 请求 Harness 在当前会话中完成一轮生成，并把最终正文回传给工作台。
 * 调用方只负责把正文放入编辑框；保存仍由用户明确确认。
 */
export function generateWithDeepSeek(input, options = {}) {
  if (!EMBEDDED) {
    return Promise.resolve({ ok: false, error: '当前为独立运行模式，未连接 DeepSeek Harness' });
  }
  const spec = typeof input === 'string' ? { question: input } : (input || {});
  const question = String(spec.question ?? spec.text ?? '').trim().slice(0, 2000);
  if (!question) return Promise.resolve({ ok: false, error: '生成内容不能为空' });
  let provided = {};
  try { provided = contextProvider() || {}; } catch { /* 提供器失败不阻断发送 */ }
  const id = requestId();
  const timeoutMs = Math.max(1000, Number(options.timeoutMs) || GENERATION_TIMEOUT_MS);
  return new Promise(resolve => {
    const timer = setTimeout(() => {
      pendingGenerations.delete(id);
      resolve({ ok: false, error: '等待 Harness 回填超时，请稍后重试' });
    }, timeoutMs);
    pendingGenerations.set(id, { resolve, timer });
    post({
      type: 'dp-generate', version: 3, requestId: id, question,
      context: boundedContext({ ...provided, ...(spec.context || {}) }),
    });
  });
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
    if (d.type === 'dp-generate-result') {
      const id = String(d.requestId || '');
      const pending = pendingGenerations.get(id);
      if (!pending) return;
      pendingGenerations.delete(id);
      clearTimeout(pending.timer);
      const reply = typeof d.reply === 'string' ? d.reply.trim().slice(0, 16000) : '';
      const error = typeof d.error === 'string' ? d.error.trim().slice(0, 500) : '';
      pending.resolve(d.ok === true && reply
        ? { ok: true, reply }
        : { ok: false, error: error || 'DeepSeek 没有返回可回填的正文' });
      document.dispatchEvent(new CustomEvent('harness-generate-result', { detail: d }));
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
      if (d.attentionId) {
        const entityTypes = new Set(['research_workflow', 'research_hypothesis', 'research_suggestion', 'security', 'data_component', 'service_recommendation', 'observation_rule', 'review_day', 'attention']);
        const views = new Set(['latest_change', 'latest_result', 'review', 'detail', 'alert', 'context', 'diagnostics', 'service_manager', 'last_trigger', 'calendar', 'evidence']);
        const safeId = value => {
          const text = String(value || '').trim().slice(0, 160);
          return text && !/[<>\\]/.test(text) && !text.includes('://') ? text : '';
        };
        const target = {
          page: safeId(d.page), entityType: safeId(d.entityType), entityId: safeId(d.entityId),
          view: safeId(d.view), fingerprint: safeId(d.fingerprint), runId: safeId(d.runId),
        };
        const precise = entityTypes.has(target.entityType) && views.has(target.view)
          && target.entityId && /^[a-f0-9]{24,64}$/i.test(target.fingerprint);
        setTimeout(() => document.dispatchEvent(new CustomEvent(precise ? 'attention-target-open' : 'attention-open', {
          detail: precise
            ? { id: safeId(d.attentionId), target }
            : { id: safeId(d.attentionId) },
        })), 80);
      }
    } catch { /* 忽略 */ }
  });
}
