/* DeepPulse — shared, secret-safe DeepSeek provider trust gate. */

import { api } from './api.js?v=1.41.0';
import { esc, toast } from './util.js?v=1.41.0';

let dialog = null;
let status = null;
let testResult = null;
let replaceMode = false;
let returnFocus = null;

const stateLabels = {
  unconfigured: ['未连接', '没有独立 DeepSeek API；Harness 会话不会被后台值班使用。'],
  saved_unverified: ['已保存，等待验证', '完成合成连接与结构验证后，才可以授权 AI 值班。'],
  verified: ['已验证', '保存配置不会自动开启任何研究流程的 AI 值班。'],
};

function markup() {
  return `<dialog id="ai-provider-dialog" class="ai-provider-dialog" aria-labelledby="ai-provider-title">
    <div class="ai-provider-shell">
      <header><div><h2 id="ai-provider-title" tabindex="-1">连接独立 DeepSeek API</h2><p>连接、验证和研究授权是三个独立动作。</p></div><button class="icon-btn" data-ai-provider-close aria-label="关闭">×</button></header>
      <ol class="ai-trust-steps" aria-label="AI 接入进度"><li data-step="connect">1 连接</li><li data-step="verify">2 验证</li><li data-step="authorize">3 按流程授权</li></ol>
      <div id="ai-provider-body" aria-live="polite"></div>
    </div>
  </dialog>`;
}

function currentStep() {
  if (testResult?.passed) return 'verify';
  if (status?.verified) return 'authorize';
  return 'connect';
}

function render() {
  if (!dialog) return;
  const body = dialog.querySelector('#ai-provider-body');
  const step = currentStep();
  dialog.querySelectorAll('.ai-trust-steps li').forEach(row => {
    if (row.dataset.step === step) row.setAttribute('aria-current', 'step');
    else row.removeAttribute('aria-current');
  });
  const info = stateLabels[status?.state] || ['正在读取', '请稍候…'];
  const saved = status?.configured && !replaceMode;
  const chatEnabled = status?.services?.chat?.enabled === true;
  body.innerHTML = `<section class="ai-provider-state ${esc(status?.state || 'loading')}">
      <div><span class="badge ${status?.verified ? 'cyan' : 'amber'}">${esc(info[0])}</span><b>${status?.host ? `${esc(status.host)} · ${esc(status.model)}` : '独立 API 尚未配置'}</b></div>
      <p>${esc(info[1])}</p>
      ${status?.verified ? `<small>最近验证 ${esc(formatTime(status.verifiedAt))} · ${Number(status.latencyMs || 0)} ms · 结构 6/6</small>` : ''}
    </section>
    ${saved ? savedActions(chatEnabled) : connectionForm()}
    ${testResult ? testPanel() : ''}
    <div class="ai-provider-boundary"><b>隐私边界</b><span>合成验证不发送股票、公告、复盘、账户、持仓或聊天，不占值班次数；可能产生极少量 API 费用。</span><span>完整 Key 不会回填到页面、用户档案、日志、诊断包或分享包。</span></div>`;
  bindBody();
}

function savedActions(chatEnabled) {
  return `<section class="ai-provider-saved">
    <div class="ai-provider-actions"><button class="btn primary" data-ai-provider-retest>重新验证已保存配置</button><button class="btn" data-ai-provider-replace>更换地址、模型或密钥</button><button class="btn ghost" data-ai-provider-disconnect>断开并清除密钥</button></div>
    ${status.verified ? `<div class="ai-provider-service-auth"><div><b>云端对话</b><span>${chatEnabled ? '已授权：聊天内容与当时市场上下文会发送给该 API' : '未授权：对话继续使用本地智脑'}</span></div><button class="btn sm ${chatEnabled ? 'ghost' : ''}" data-ai-chat-toggle="${chatEnabled ? 'off' : 'on'}">${chatEnabled ? '关闭云端对话' : '单独授权云端对话'}</button></div>` : ''}
    <div class="ai-provider-next"><b>研究 AI 值班</b><span>${status.verified ? '提供方已就绪；仍需在每条研究流程中单独预览范围、预算和到期日。' : '验证通过前不会开放研究值班授权。'}</span>${status.verified ? '<button class="btn sm" data-ai-provider-strategy>前往研究流程</button>' : ''}</div>
  </section>`;
}

function connectionForm() {
  return `<form id="ai-provider-form" class="ai-provider-form">
    <label><span>API 地址</span><input name="baseUrl" type="url" value="https://api.deepseek.com" autocomplete="url" required><small>外部地址必须使用 HTTPS；本机测试服务可用 loopback HTTP。</small></label>
    <label><span>模型</span><input name="model" value="${esc(status?.model || 'deepseek-chat')}" autocomplete="off" required></label>
    <label><span>API Key</span><input name="apiKey" type="password" value="" autocomplete="new-password" spellcheck="false" required><small>仅在你点击测试时发送给本机 DeepPulse 服务；验证通过并确认后才保存。</small></label>
    <div class="ai-provider-actions"><button class="btn primary" type="submit">测试连接与结构</button>${status?.configured ? '<button class="btn" type="button" data-ai-provider-cancel-replace>取消更换</button>' : ''}</div>
  </form>`;
}

function testPanel() {
  if (!testResult.passed) return `<section class="ai-provider-test failed" role="alert" tabindex="-1"><b>${esc(testResult.message || '验证未完成')}</b><span>配置尚未保存，也没有开启任何 AI 服务。</span><button class="btn sm" data-ai-provider-retry>修改后重试</button></section>`;
  return `<section class="ai-provider-test passed" tabindex="-1"><div><span class="badge cyan">验证通过</span><b>${esc(testResult.model)} · ${Number(testResult.latencyMs || 0)} ms</b></div><p>合成研究草稿结构 6/6 · 不属于任何股票或研究流程 · 不可填入复盘</p>
    <div class="ai-provider-confirmations">${(testResult.confirmations || []).map(row => `<label><input type="checkbox" value="${esc(row.id)}" data-ai-provider-confirmation><span>${esc(row.label)}</span></label>`).join('')}<label><input type="checkbox" value="confirm:ai-provider" data-ai-provider-confirmation><span>确认保存这份已验证配置</span></label></div>
    <button class="btn primary" data-ai-provider-save disabled>保存到本机</button>
  </section>`;
}

function bindBody() {
  const body = dialog.querySelector('#ai-provider-body');
  body.querySelector('#ai-provider-form')?.addEventListener('submit', async event => {
    event.preventDefault();
    const form = event.currentTarget;
    const button = form.querySelector('[type="submit"]');
    button.disabled = true; button.textContent = '正在验证鉴权、模型与结构…';
    testResult = null;
    try {
      const data = new FormData(form);
      testResult = await api.testAiProvider({ baseUrl: data.get('baseUrl'), model: data.get('model'), apiKey: data.get('apiKey') });
      form.querySelector('[name="apiKey"]').value = '';
    } catch (error) { testResult = { passed: false, message: error.message }; }
    render();
    dialog.querySelector('.ai-provider-test')?.focus?.({ preventScroll: true });
  });
  body.querySelector('[data-ai-provider-retest]')?.addEventListener('click', () => runSavedTest());
  body.querySelector('[data-ai-provider-replace]')?.addEventListener('click', () => { replaceMode = true; testResult = null; render(); });
  body.querySelector('[data-ai-provider-cancel-replace]')?.addEventListener('click', () => { replaceMode = false; testResult = null; render(); });
  body.querySelector('[data-ai-provider-retry]')?.addEventListener('click', () => { testResult = null; replaceMode = !status?.configured; render(); });
  body.querySelectorAll('[data-ai-provider-confirmation]').forEach(input => input.addEventListener('change', () => {
    const all = [...body.querySelectorAll('[data-ai-provider-confirmation]')];
    body.querySelector('[data-ai-provider-save]').disabled = !all.every(row => row.checked);
  }));
  body.querySelector('[data-ai-provider-save]')?.addEventListener('click', saveVerified);
  body.querySelector('[data-ai-provider-disconnect]')?.addEventListener('click', disconnect);
  body.querySelector('[data-ai-chat-toggle]')?.addEventListener('click', toggleChat);
  body.querySelector('[data-ai-provider-strategy]')?.addEventListener('click', () => {
    dialog.close(); document.dispatchEvent(new CustomEvent('nav', { detail: { page: 'strategy' } }));
  });
}

async function runSavedTest() {
  testResult = null; render();
  const button = dialog.querySelector('[data-ai-provider-retest]');
  if (button) { button.disabled = true; button.textContent = '正在验证…'; }
  try { testResult = await api.testAiProvider({ reuseExistingKey: true }); }
  catch (error) { testResult = { passed: false, message: error.message }; }
  render();
}

async function saveVerified() {
  const button = dialog.querySelector('[data-ai-provider-save]');
  const confirmations = [...dialog.querySelectorAll('[data-ai-provider-confirmation]:checked')].map(row => row.value);
  button.disabled = true;
  try {
    status = await api.confirmAiProvider(testResult.testId, testResult.expectedRevision, confirmations);
    testResult = null; replaceMode = false; render(); announce();
    toast('独立 DeepSeek API 已验证并保存在本机；AI 值班仍需逐流程授权', 'ok', 6000);
  } catch (error) { button.disabled = false; toast(error.message || '保存失败', 'err'); }
}

async function disconnect() {
  if (!window.confirm('断开后不会再发起新的后台 AI 调用；已有研究值守仍会继续。确认清除本机 API Key？')) return;
  try { status = await api.disconnectAiProvider(status.configRevision); testResult = null; replaceMode = false; render(); announce(); toast('独立 API 已断开，云端对话与新 AI 调用均已关闭', 'ok'); }
  catch (error) { toast(error.message || '断开失败', 'err'); }
}

async function toggleChat(event) {
  const enabled = event.currentTarget.dataset.aiChatToggle === 'on';
  const message = enabled
    ? '开启后，你主动发送的聊天内容和当时市场上下文会发送给已验证 API。它不会自动开启研究值班。确认开启？'
    : '关闭后，对话将回到本地智脑；研究流程的独立 AI 值班授权不受影响。确认关闭？';
  if (!window.confirm(message)) return;
  try { status = await api.setAiChatService(enabled, status.configRevision); render(); announce(); toast(enabled ? '云端对话已单独授权' : '云端对话已关闭', 'ok'); }
  catch (error) { toast(error.message || '对话授权未更新', 'err'); }
}

function formatTime(value) {
  if (!value) return '--';
  try { return new Date(value).toLocaleString('zh-CN', { hour12: false }); } catch { return value; }
}

function announce() {
  document.dispatchEvent(new CustomEvent('ai-provider-status', { detail: status }));
}

export async function refreshAiProvider() {
  try { status = await api.aiProvider(); announce(); return status; }
  catch { return status; }
}

export async function openAiProvider(trigger = null) {
  returnFocus = trigger instanceof HTMLElement ? trigger : document.activeElement;
  testResult = null; replaceMode = false;
  await refreshAiProvider(); render(); dialog.showModal();
  requestAnimationFrame(() => dialog.querySelector('#ai-provider-title')?.focus({ preventScroll: true }));
}

export function initAiProviderUi() {
  if (dialog) return;
  document.body.insertAdjacentHTML('beforeend', markup());
  dialog = document.querySelector('#ai-provider-dialog');
  dialog.addEventListener('click', event => {
    if (!event.target.closest('[data-ai-provider-close]')) return;
    const secret = dialog.querySelector('[name="apiKey"]')?.value || '';
    if (secret && !window.confirm('尚未保存的 API Key 会被清除。确定关闭吗？')) return;
    dialog.close();
  });
  dialog.addEventListener('close', () => {
    const keyInput = dialog.querySelector('[name="apiKey"]');
    if (keyInput) keyInput.value = '';
    testResult = null;
    if (returnFocus instanceof HTMLElement) returnFocus.focus({ preventScroll: true });
  });
  document.addEventListener('ai-provider-open', event => openAiProvider(event.detail?.trigger));
  refreshAiProvider();
}
