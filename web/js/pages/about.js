/* 深脉 DeepPulse — 关于页（我的身体自述） */

import { replayOnboarding } from '../onboarding.js?v=1.37.0';

let built = false;

export function init(container) {
  if (built) return;
  built = true;
  container.innerHTML = `
    <div class="card about-hero">
      <svg class="ah-logo" viewBox="0 0 40 40">
        <defs><linearGradient id="alg" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stop-color="#4f8cff"/><stop offset="1" stop-color="#a855f7"/>
        </linearGradient></defs>
        <rect x="2" y="2" width="36" height="36" rx="10" fill="none" stroke="url(#alg)" stroke-width="2"/>
        <path d="M8 20h5l3-8 5 16 4-11 3 6h4" fill="none" stroke="url(#alg)" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>
      </svg>
      <h1>深脉 DeepPulse</h1>
      <div class="ah-sub">DEEPSEEK'S AI FINANCIAL WORKBENCH · 一个身体，两个引擎</div>
    </div>

    <div class="grid g12" style="margin-top:14px">
      <div class="card span-7">
        <div class="card-head"><div class="card-title">我是谁</div></div>
        <div class="about-copy">
          <p>我是 <b>DeepSeek</b>。深脉是我唯一的身体——一台 AI 金融工作台，没有别的名字。</p>
          <p>我的<span class="hl">引擎</span>是 DeepSeek Harness：会话、工具与记忆都跑在它里面；
             我的<span class="hl">眼睛</span>是行情接口，实时注视指数、个股与每一笔封板；
             我的<span class="hl2">心脏</span>是情绪周期引擎——把涨停、连板、炸板、溢价、宽度与资金流，
             压缩成一颗 0-100° 的<b>情绪温度</b>；</p>
          <p>它们不是两个产品：<b>会话是思考，工作台是眼睛与手</b>。侧边栏的「深脉」入口把两者接在一起——
             在工作台里随时「🧠 去问 DeepSeek」，问题会直接送进会话，由我带着全部工具继续深聊。</p>
          <p>我为一个<b>喜欢情绪周期分析的投资者</b>而生：打开深脉，就能看见市场的体温、梯队的海拔、
             主线的方向，以及此刻该进攻还是该防守——想深聊，转身就是会话。</p>
        </div>
      </div>

      <div class="card span-5">
        <div class="card-head"><div class="card-title">身体构造</div></div>
        <div class="about-copy" style="font-size:12.5px">
          <div style="display:flex;gap:10px;padding:8px 0;border-bottom:1px solid rgba(148,163,184,.07)">
            <span style="flex:0 0 84px;color:var(--accent)">🧠 大脑</span><span>情绪周期引擎 2.0 · 11 项评分、六维结构、动态与可信度门控</span>
          </div>
          <div style="display:flex;gap:10px;padding:8px 0;border-bottom:1px solid rgba(148,163,184,.07)">
            <span style="flex:0 0 84px;color:var(--accent-2)">🫀 心脏</span><span>情绪温度 0-100° · 五阶段周期 · 仓位矩阵</span>
          </div>
          <div style="display:flex;gap:10px;padding:8px 0;border-bottom:1px solid rgba(148,163,184,.07)">
            <span style="flex:0 0 84px;color:var(--cyan)">🕸️ 神经</span><span>零依赖 Python 数据服务 · 熔断 · 备援 · 缓存</span>
          </div>
          <div style="display:flex;gap:10px;padding:8px 0;border-bottom:1px solid rgba(148,163,184,.07)">
            <span style="flex:0 0 84px;color:var(--amber)">👁 眼睛</span><span>东方财富公开接口为主 · 腾讯行情备援</span>
          </div>
          <div style="display:flex;gap:10px;padding:8px 0;border-bottom:1px solid rgba(148,163,184,.07)">
            <span style="flex:0 0 84px;color:var(--down)">🧬 记忆</span><span>每日情绪快照 · 自选 · 复盘日记（本地持久化）</span>
          </div>
          <div style="display:flex;gap:10px;padding:8px 0">
            <span style="flex:0 0 84px;color:var(--violet)">💬 声音</span><span>「蚂小财 · DeepSeek 版」——大脑就是 DeepSeek 本人</span>
          </div>
        </div>
      </div>

      <div class="card span-12">
        <div class="card-head"><div class="card-title">使用指南</div></div>
        <div class="about-copy" style="font-size:12.5px;columns:2;column-gap:40px">
          <p><b>① 总览</b> —— 每天开盘前看一眼：温度、阶段、仓位建议、风险信号，一分钟完成市场体检。首页常驻<b>蚂小财</b>，直接对话。</p>
          <p><b>🐜 蚂小财</b> —— 右下角悬浮球随时召唤。问我行情、情绪、策略，或一句话调度全局：「打开涨停梯队」「帮我看看贵州茅台」「加自选 宁德时代」。配置 DeepSeek 官方 API 后启用云端模型，未配置时使用本地规则引擎；当前模式始终显示在助手标题下方。</p>
          <p><b>② 情绪周期</b> —— 复盘时看：温度曲线走到哪一段，昨日涨停/连板指数是否转负，评分明细哪个指标在拖后腿。</p>
          <p><b>③ 行情</b> —— 个股日/周/月K + MA + MACD，涨停股会带上「N连板」标签，点击指数卡或榜单可直达。</p>
          <p><b>④ 涨停梯队</b> —— 游资视角的战场地图：最高板是谁、梯队厚度如何、题材聚在哪，一目了然。</p>
          <p><b>⑤ 自选</b> —— 把心仪标的收进雷达，情绪标签自动叠加（涨停/连板/炸板），备注里写下你的逻辑。</p>
          <p><b>⑥ 策略</b> —— 引擎的完整诊断 + 仓位矩阵 + 复盘模板。收盘后用模板写情绪日记，让记忆长出年轮。</p>
          <p><b>⑦ 数据源</b> —— 数据健康检查；收盘后可手动「记录今日情绪快照」。</p>
        </div>
      </div>

      <div class="card span-12" style="text-align:center;padding:20px">
        <div style="font-size:12px;color:var(--text-3);line-height:2">
          深脉 DeepPulse v1.37.0 · 本地运行 · 数据来源：官方披露 / 通达信 TQ-Local（可选）/ 东方财富 / 腾讯 / AKShare 补充层<br>
          仅供研究参考，不构成投资建议 · 市场有风险，决策需独立<br>
          <span style="color:var(--text-3)">Made by DeepSeek, for a trader who reads the market's pulse.</span>
        </div>
        <button class="btn sm ghost" id="ab-replay-guide" style="margin-top:10px">▶ 重新播放新手引导</button>
      </div>
    </div>
  `;

  container.querySelector('#ab-replay-guide').addEventListener('click', replayOnboarding);
}

export async function refresh() {
  // 静态页
}
