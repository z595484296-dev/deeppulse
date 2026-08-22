/* 深脉 DeepPulse — 首次启动引导（四步闭环：看体温→问蚂小财→深聊→建自选）
   设计原则：只讲「干什么」，不讲「是什么」；每步可跳过；完成即永不再扰。 */

import { EMBEDDED } from './bridge.js?v=1.27.0';

const KEY = 'dp_onboarded_v1';

const STEPS = [
  {
    icon: '🫀',
    title: '先看市场体温',
    desc: '首页的<b>情绪温度计</b>把涨停、连板、炸板、资金流压缩成一颗 0-100° 的温度：' +
      '冰点期防守，发酵期进攻。看盘第一步永远是「先定周期」。',
  },
  {
    icon: '🐜',
    title: '有事问蚂小财',
    desc: '首页对话面板或右下角悬浮球都能找到我。试试：<b>「今天情绪怎么样」</b>、' +
      '<b>「帮我看看贵州茅台」</b>、<b>「打开涨停梯队」</b>——一句话调度全局。',
  },
  {
    icon: '🧠',
    title: '想深聊，去问 DeepSeek',
    desc: EMBEDDED
      ? '每条回答下都有「🧠 去问 DeepSeek」——问题会带着上下文直接送进左侧会话，由我带着全部工具继续深挖。'
      : '这是深脉的独立窗口模式。想要完整的 DeepSeek 会话能力，请打开桌面「深脉 DeepPulse」主应用——' +
        '那里工作台与我的会话是一体的。',
  },
  {
    icon: '⭐',
    title: '建你的自选雷达',
    desc: '把关注的股票加进<b>自选</b>，我会实时盯盘并自动叠加涨停/连板/炸板标签；' +
      '收盘后在<b>策略</b>页点「🤖 让 DeepSeek 生成复盘」，让记忆长出年轮。',
  },
];

export function initOnboarding() {
  try {
    if (localStorage.getItem(KEY)) return;
  } catch { return; }

  const el = document.createElement('div');
  el.id = 'onboarding';
  el.innerHTML = `
    <div class="ob-panel">
      <div class="ob-brand">
        <svg viewBox="0 0 40 40" style="width:34px;height:34px"><defs><linearGradient id="ob-lg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#4f8cff"/><stop offset="1" stop-color="#a855f7"/></linearGradient></defs><rect x="2" y="2" width="36" height="36" rx="10" fill="none" stroke="url(#ob-lg)" stroke-width="2"/><path d="M8 20h5l3-8 5 16 4-11 3 6h4" fill="none" stroke="url(#ob-lg)" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>
        <div><b>欢迎使用深脉</b><span>60 秒了解这套工作法</span></div>
      </div>
      <div class="ob-body">
        <div class="ob-icon" data-icon></div>
        <div class="ob-title" data-title></div>
        <div class="ob-desc" data-desc></div>
      </div>
      <div class="ob-dots" data-dots></div>
      <div class="ob-actions">
        <button class="btn ghost sm" data-skip>跳过</button>
        <button class="btn primary" data-next>下一步</button>
      </div>
    </div>`;
  document.body.appendChild(el);

  let i = 0;
  const icon = el.querySelector('[data-icon]');
  const title = el.querySelector('[data-title]');
  const desc = el.querySelector('[data-desc]');
  const dots = el.querySelector('[data-dots]');
  const nextBtn = el.querySelector('[data-next]');

  const render = () => {
    const s = STEPS[i];
    icon.textContent = s.icon;
    title.textContent = s.title;
    desc.innerHTML = s.desc;
    dots.innerHTML = STEPS.map((_, k) => `<span class="ob-dot ${k === i ? 'on' : ''}"></span>`).join('');
    nextBtn.textContent = i === STEPS.length - 1 ? '开始使用' : '下一步';
  };
  const finish = () => {
    try { localStorage.setItem(KEY, '1'); } catch { /* 忽略 */ }
    el.remove();
  };

  el.querySelector('[data-skip]').addEventListener('click', finish);
  nextBtn.addEventListener('click', () => {
    if (i === STEPS.length - 1) finish();
    else { i++; render(); }
  });
  render();
}

/** 重新播放引导（关于页入口）。 */
export function replayOnboarding() {
  try { localStorage.removeItem(KEY); } catch { /* 忽略 */ }
  initOnboarding();
}
