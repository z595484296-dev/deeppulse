/* 深脉 DeepPulse — 微雪 7.5 英寸墨水屏开发模式 */

import { api } from '../api.js?v=1.33.0';
import { esc, debounce, toast } from '../util.js?v=1.33.0';

let built = false;
let snapshot = null;
let previewUrl = '';
let previewScene = 'selected';
let previewRequest = 0;
let searchResults = [];
let searchIndex = -1;

const MODE_META = {
  focus: { label: '个股专注', help: '关注股票、日K线、情绪温度与五大指数，适合日常常驻。' },
  overview: { label: '市场总览', help: '指数、市场温度和数据状态，快速判断整体环境。' },
  emotion: { label: '情绪周期', help: '温度、阶段、六维结构与涨跌停等核心指标，适合复盘。' },
  watch: { label: '自选组合', help: '同屏查看最多 6 只自选股的价格和涨跌幅。' },
  hotspot: { label: '热点雷达', help: '展示领涨行业、强度与市场结构，捕捉当下热点。' },
  event: { label: '事件雷达', help: '展示事件数量、质量、敏感行业和命中的自选代码；需先在总览明确开启事件服务。' },
  research: { label: '研究结果', help: '展示最近一次研究流程的证据数量、数据缺口、陈旧项与同源提醒，不自动给出结论。' },
  alert: { label: '提醒优先', help: '触发关注价提醒时占满画面，未触发时回到个股专注。' },
};

const REFRESH_META = {
  stable: { label: '稳定全刷', help: '每次更新都全刷，残影最少，但会出现完整黑白闪烁。' },
  smart: { label: '智能混合', help: '无实质变化不刷；小区域局刷；模式或结构变化全刷。推荐。' },
  fast: { label: '快速全屏', help: '优先使用约 1.5 秒快刷，并定期全刷清理残影。' },
};

function selectedMode(container) {
  return container.querySelector('#ep-layout')?.value || 'focus';
}

function previewMode(container) {
  return previewScene === 'alert' ? 'alert' : selectedMode(container);
}

function updateModeHelp(container) {
  const mode = selectedMode(container);
  const meta = MODE_META[mode] || MODE_META.focus;
  const help = container.querySelector('#ep-mode-help');
  if (help) help.textContent = meta.help;
}

function updateRefreshHelp(container) {
  const policy = container.querySelector('#ep-refresh-policy')?.value || 'smart';
  const help = container.querySelector('#ep-refresh-help');
  if (help) help.textContent = (REFRESH_META[policy] || REFRESH_META.smart).help;
}

function revokePreview() {
  if (previewUrl) URL.revokeObjectURL(previewUrl);
  previewUrl = '';
}

function gatewayEndpoint(gateway) {
  const ip = (gateway.addresses || [])[0];
  return ip ? `http://${ip}:${gateway.port}${gateway.endpoint_path}` : `http://本机局域网IP:${gateway.port}${gateway.endpoint_path}`;
}

function renderConfig(container, data) {
  snapshot = data;
  const cfg = data.config || {};
  const gateway = data.gateway || {};
  const stock = container.querySelector('#ep-stock');
  stock.value = `${cfg.focus_name || ''}${cfg.focus_name ? ' · ' : ''}${cfg.focus_code || ''}`;
  stock.dataset.code = cfg.focus_code || '';
  stock.dataset.name = cfg.focus_name || '';
  container.querySelector('#ep-layout').value = cfg.mode || 'focus';
  updateModeHelp(container);
  container.querySelector('#ep-refresh-policy').value = cfg.refresh_policy || 'smart';
  updateRefreshHelp(container);
  container.querySelector('#ep-poll').value = cfg.poll_seconds || 30;
  container.querySelector('#ep-display').value = cfg.display_seconds || 180;
  container.querySelector('#ep-partial').value = cfg.partial_before_full || 6;
  container.querySelector('#ep-gateway-enable').checked = !!cfg.enabled;

  const endpoint = gatewayEndpoint(gateway);
  const stateClass = gateway.running ? 'ok' : gateway.enabled ? 'warn' : 'idle';
  const stateText = gateway.running ? '局域网网关运行中' : gateway.enabled ? '已启用但监听失败' : '未启用（安全默认）';
  const firmwareAgent = gateway.last_user_agent || '';
  const firmwareMatch = firmwareAgent.match(/DeepPulse-EPaper\/(\d+\.\d+)/);
  const firmwareVersion = firmwareMatch?.[1] || '';
  const firmwareReady = firmwareVersion && Number(firmwareVersion) >= 1.1;
  const firmwareText = firmwareVersion
    ? `${firmwareVersion}${firmwareReady ? ' · 智能刷新就绪' : ' · 需 USB 升级'}`
    : '等待设备上报';
  const refreshMeta = REFRESH_META[cfg.refresh_policy] || REFRESH_META.smart;
  container.querySelector('#ep-gateway-state').innerHTML = `
    <div class="ep-gateway-line"><span class="ep-gateway-dot ${stateClass}"></span><b>${esc(stateText)}</b></div>
    <div class="ep-kv"><span>刷新策略</span><code>${esc(refreshMeta.label)}</code></div>
    <div class="ep-kv"><span>设备固件</span><code>${esc(firmwareText)}</code></div>
    <div class="ep-kv"><span>设备帧地址</span><code>${esc(endpoint)}</code></div>
    <div class="ep-kv"><span>配对令牌</span><code id="ep-token-value" data-token="${esc(gateway.token || '')}">••••••••••••••••••••••••</code></div>
    <div class="ep-kv"><span>最近设备</span><code>${esc(gateway.last_seen ? `${gateway.last_ip || '--'} · ${gateway.last_seen}` : '尚无 ESP32 连接')}</code></div>
    ${gateway.last_error ? `<div class="ep-gateway-error">${esc(gateway.last_error)}</div>` : ''}
  `;
  container.querySelector('#ep-copy-endpoint').dataset.copy = endpoint;
  container.querySelector('#ep-copy-token').dataset.token = gateway.token || '';
  container.querySelector('#ep-reveal-token').dataset.token = gateway.token || '';
}

async function loadConfig(container) {
  try {
    const data = await api.deviceConfig();
    renderConfig(container, data);
    return data;
  } catch (error) {
    container.querySelector('#ep-gateway-state').innerHTML = `<div class="empty">设备配置读取失败：${esc(error.message)}</div>`;
    throw error;
  }
}

async function renderPreview(container) {
  const requestId = ++previewRequest;
  const image = container.querySelector('#ep-preview-image');
  const loading = container.querySelector('#ep-preview-loading');
  const meta = container.querySelector('#ep-preview-meta');
  loading.style.display = 'grid';
  image.classList.add('loading');
  try {
    const mode = previewMode(container);
    const [state, blob] = await Promise.all([api.deviceState(mode), api.devicePreview(mode)]);
    if (requestId !== previewRequest) return;
    revokePreview();
    previewUrl = URL.createObjectURL(blob);
    image.src = previewUrl;
    const em = state.emotion || {};
    const focus = state.focus || {};
    const quality = state.quality || {};
    const modeMeta = MODE_META[state.device?.mode] || MODE_META[mode] || MODE_META.focus;
    const modeDetail = mode === 'watch'
      ? ` · ${state.watch?.length || 0} 只`
      : mode === 'hotspot' && state.hotspots?.[0]
        ? ` · ${state.hotspots[0].name || state.hotspots[0].code}`
        : mode === 'research'
          ? ` · ${state.research_workflow?.state === 'ready' ? '最近研究已就绪' : '等待首次运行'}` : '';
    meta.innerHTML = `
      <span><b>${esc(modeMeta.label)}</b>${esc(modeDetail)}</span>
      <span><b>${esc(focus.code || '--')}</b> ${esc(focus.name || '')}</span>
      <span>温度 <b>${em.temperature ?? '--'}°</b> · ${esc(em.phase || '--')}</span>
      <span>通达信 <b>${esc(quality.tdx_status || '--')}</b></span>
      <span>${esc(state.generated_at || '--')}</span>
    `;
    container.querySelector('#ep-frame-hash').textContent = '帧已按 1bpp / MSB-first 生成 · ESP32 实际接收 48,000 bytes';
  } catch (error) {
    if (requestId === previewRequest) {
      meta.innerHTML = `<span class="up">预览生成失败：${esc(error.message)}</span>`;
      container.querySelector('#ep-frame-hash').textContent = '可稍后重试；预览失败不会修改任何设备配置';
    }
  } finally {
    if (requestId === previewRequest) {
      loading.style.display = 'none';
      image.classList.remove('loading');
    }
  }
}

function renderSearchOptions(container) {
  const list = container.querySelector('#ep-stock-results');
  list.innerHTML = searchResults.map((row, index) => `
    <button type="button" role="option" aria-selected="${index === searchIndex}"
      class="ep-search-option${index === searchIndex ? ' active' : ''}"
      data-index="${index}" data-code="${esc(row.code)}" data-name="${esc(row.name)}">
      <span>${esc(row.name)}</span><code>${esc(row.code)}</code>
    </button>`).join('');
  list.classList.toggle('show', searchResults.length > 0);
  container.querySelector('#ep-stock').setAttribute('aria-expanded', String(searchResults.length > 0));
}

function chooseSearchResult(container, index) {
  const row = searchResults[index];
  if (!row) return;
  const input = container.querySelector('#ep-stock');
  input.value = `${row.name} · ${row.code}`;
  input.dataset.code = row.code;
  input.dataset.name = row.name;
  searchResults = [];
  searchIndex = -1;
  renderSearchOptions(container);
}

async function saveConfig(container) {
  const button = container.querySelector('#ep-save');
  const stock = container.querySelector('#ep-stock');
  if (!/^\d{6}$/.test(stock.dataset.code || '')) {
    toast('请从搜索结果中选择一只股票', 'err');
    stock.focus();
    return;
  }
  button.disabled = true;
  button.textContent = '保存中…';
  try {
    const data = await api.saveDeviceConfig({
      enabled: container.querySelector('#ep-gateway-enable').checked,
      focus_code: stock.dataset.code,
      focus_name: stock.dataset.name,
      mode: container.querySelector('#ep-layout').value,
      refresh_policy: container.querySelector('#ep-refresh-policy').value,
      poll_seconds: Number(container.querySelector('#ep-poll').value),
      display_seconds: Number(container.querySelector('#ep-display').value),
      partial_before_full: Number(container.querySelector('#ep-partial').value),
    });
    renderConfig(container, data);
    await renderPreview(container);
    toast(data.gateway.running ? '设备配置已保存，局域网网关已启动' : '设备配置已保存');
  } catch (error) {
    toast('保存失败：' + error.message, 'err', 6000);
  } finally {
    button.disabled = false;
    button.textContent = '保存设备配置';
  }
}

export function init(container) {
  if (built) return;
  built = true;
  container.innerHTML = `
    <div class="epaper-hero">
      <div>
        <span class="epaper-kicker">HARDWARE DEVICE · 微雪 7.5 V2</span>
        <h2>微雪 7.5 英寸墨水屏</h2>
        <p>800 × 480 黑白裸屏 · ESP32 WiFi 驱动板 · 深脉负责数据与画面，设备只读显示</p>
      </div>
      <span class="epaper-ready">实机已联动</span>
    </div>

    <div class="grid g12 epaper-grid">
      <div class="card span-8 epaper-preview-card">
        <div class="card-head">
          <div>
            <div class="card-title">真实设备帧模拟器</div>
            <div class="card-sub">与 ESP32 下载内容完全相同 · 1 bit 黑白 · 48,000 bytes</div>
          </div>
          <div class="ep-preview-tabs" role="tablist" aria-label="模拟场景">
            <button class="ep-preview-tab active" data-scene="selected" role="tab" aria-selected="true">所选模式</button>
            <button class="ep-preview-tab" data-scene="alert" role="tab" aria-selected="false">提醒演示</button>
          </div>
        </div>
        <div class="ep-screen-shell">
          <div class="ep-screen-bezel">
            <img id="ep-preview-image" alt="800乘480墨水屏真实单色帧预览">
            <div class="ep-preview-loading" id="ep-preview-loading">正在生成真实设备帧…</div>
          </div>
        </div>
        <div class="ep-preview-meta" id="ep-preview-meta"><span>等待首次渲染…</span></div>
        <div class="ep-preview-actions">
          <span id="ep-frame-hash">尚未生成</span>
          <div>
            <button class="btn sm" id="ep-refresh-preview">重新渲染</button>
            <button class="btn sm" id="ep-download-frame">下载 frame.bin</button>
          </div>
        </div>
      </div>

      <div class="card span-4 epaper-config-card">
        <div class="card-head"><div><div class="card-title">显示配置</div><div class="card-sub">保存后同步到真实设备</div></div></div>
        <label class="ep-field">
          <span>关注标的</span>
          <div class="ep-search-wrap">
            <input id="ep-stock" role="combobox" aria-autocomplete="list" aria-controls="ep-stock-results"
              aria-expanded="false" autocomplete="off" placeholder="输入代码或名称">
            <div id="ep-stock-results" class="ep-stock-results" role="listbox"></div>
          </div>
        </label>
        <label class="ep-field"><span>默认画面</span><select id="ep-layout">
          <option value="focus">关注标的 + K线</option>
          <option value="overview">市场总览</option>
          <option value="emotion">情绪周期</option>
          <option value="watch">自选组合</option>
          <option value="hotspot">热点雷达</option>
          <option value="event">事件雷达</option>
          <option value="research">研究结果</option>
          <option value="alert">提醒优先</option>
        </select><small class="ep-mode-help" id="ep-mode-help"></small></label>
        <label class="ep-field"><span>刷新方式</span><select id="ep-refresh-policy">
          <option value="smart">智能混合（推荐）</option>
          <option value="stable">稳定全刷</option>
          <option value="fast">快速全屏</option>
        </select><small class="ep-mode-help" id="ep-refresh-help"></small></label>
        <div class="ep-field-row">
          <label class="ep-field"><span>数据轮询</span><select id="ep-poll">
            <option value="15">15 秒</option><option value="30">30 秒</option><option value="60">60 秒</option>
          </select></label>
          <label class="ep-field"><span>普通刷新</span><select id="ep-display">
            <option value="60">1 分钟</option><option value="180">3 分钟</option><option value="300">5 分钟</option>
          </select></label>
        </div>
        <label class="ep-field"><span>轻刷后全刷</span><select id="ep-partial">
          <option value="4">4 次</option><option value="6">6 次</option><option value="8">8 次</option><option value="10">10 次</option>
        </select></label>
        <label class="ep-toggle-row">
          <span><b>启用局域网硬件网关</b><small>默认关闭；开启后只有持有令牌的ESP32能读取设备数据</small></span>
          <input type="checkbox" id="ep-gateway-enable" role="switch">
        </label>
        <button class="btn primary ep-save" id="ep-save">保存设备配置</button>
      </div>

      <div class="card span-7">
        <div class="card-head">
          <div><div class="card-title">ESP32 配对与网关</div><div class="card-sub">独立端口 8988 · 不开放自选、日记、聊天或策略修改接口</div></div>
          <span class="source-tier local">令牌只读</span>
        </div>
        <div id="ep-gateway-state"><div class="empty">正在读取网关状态…</div></div>
        <div class="ep-gateway-actions">
          <button class="btn sm" id="ep-reveal-token">显示令牌</button>
          <button class="btn sm" id="ep-copy-token">复制令牌</button>
          <button class="btn sm" id="ep-copy-endpoint">复制帧地址</button>
          <button class="btn sm ghost" id="ep-rotate-token">重置令牌</button>
        </div>
      </div>

      <div class="card span-12 ep-safety-note">
        <b>产品边界：</b>墨水屏是低频决策仪表盘，不用于秒级交易。所有“提醒”展示触发条件、数据时点与失效条件，均为研究工具输出，不构成投资建议。
      </div>
    </div>`;

  const doSearch = debounce(async value => {
    if (!value) { searchResults = []; renderSearchOptions(container); return; }
    try {
      searchResults = await api.search(value);
      searchIndex = searchResults.length ? 0 : -1;
      renderSearchOptions(container);
    } catch { searchResults = []; renderSearchOptions(container); }
  }, 250);
  const stock = container.querySelector('#ep-stock');
  stock.addEventListener('input', () => {
    stock.dataset.code = '';
    stock.dataset.name = '';
    doSearch(stock.value.trim());
  });
  stock.addEventListener('keydown', event => {
    if (!searchResults.length) return;
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      searchIndex = (searchIndex + (event.key === 'ArrowDown' ? 1 : -1) + searchResults.length) % searchResults.length;
      renderSearchOptions(container);
    } else if (event.key === 'Enter') {
      event.preventDefault();
      chooseSearchResult(container, searchIndex);
    } else if (event.key === 'Escape') {
      searchResults = []; renderSearchOptions(container);
    }
  });
  container.querySelector('#ep-stock-results').addEventListener('click', event => {
    const option = event.target.closest('.ep-search-option');
    if (option) chooseSearchResult(container, Number(option.dataset.index));
  });
  document.addEventListener('click', event => {
    if (!event.target.closest('.ep-search-wrap')) { searchResults = []; renderSearchOptions(container); }
  });

  container.querySelectorAll('.ep-preview-tab').forEach(button => button.addEventListener('click', async () => {
    previewScene = button.dataset.scene || 'selected';
    container.querySelectorAll('.ep-preview-tab').forEach(row => {
      const active = row === button;
      row.classList.toggle('active', active);
      row.setAttribute('aria-selected', String(active));
    });
    await renderPreview(container);
  }));
  container.querySelector('#ep-layout').addEventListener('change', async () => {
    updateModeHelp(container);
    if (previewScene === 'selected') await renderPreview(container);
  });
  container.querySelector('#ep-refresh-policy').addEventListener('change', () => {
    updateRefreshHelp(container);
  });
  container.querySelector('#ep-refresh-preview').addEventListener('click', () => renderPreview(container));
  container.querySelector('#ep-download-frame').addEventListener('click', async event => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      const mode = previewMode(container);
      const blob = await api.deviceFrame(mode);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url; anchor.download = `deeppulse-800x480-${mode}.bin`; anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 2000);
      toast('已下载 48,000 字节 ESP32 单色帧');
    } catch (error) { toast('下载失败：' + error.message, 'err'); }
    button.disabled = false;
  });
  container.querySelector('#ep-save').addEventListener('click', () => saveConfig(container));
  container.querySelector('#ep-reveal-token').addEventListener('click', event => {
    const token = event.currentTarget.dataset.token || '';
    const value = container.querySelector('#ep-token-value');
    const revealed = value.textContent === token;
    value.textContent = revealed ? '••••••••••••••••••••••••' : token;
    event.currentTarget.textContent = revealed ? '显示令牌' : '隐藏令牌';
  });
  container.querySelector('#ep-copy-token').addEventListener('click', event => {
    navigator.clipboard?.writeText(event.currentTarget.dataset.token || '').then(() => toast('配对令牌已复制'));
  });
  container.querySelector('#ep-copy-endpoint').addEventListener('click', event => {
    navigator.clipboard?.writeText(event.currentTarget.dataset.copy || '').then(() => toast('设备帧地址已复制'));
  });
  container.querySelector('#ep-rotate-token').addEventListener('click', async () => {
    if (!confirm('重置后，已经配对的设备必须重新输入令牌。确定继续吗？')) return;
    try {
      const data = await api.rotateDeviceToken();
      renderConfig(container, data);
      toast('配对令牌已重置');
    } catch (error) { toast('重置失败：' + error.message, 'err'); }
  });

  loadConfig(container).catch(() => {}).finally(() => renderPreview(container));
}

export async function refresh(container) {
  init(container);
  await loadConfig(container).catch(() => {});
  await renderPreview(container);
}
