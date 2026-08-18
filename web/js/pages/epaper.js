/* 深脉 DeepPulse — 微雪 7.5 英寸墨水屏开发模式 */

import { api } from '../api.js?v=1.5.0';
import { esc, debounce, toast } from '../util.js?v=1.5.0';

let built = false;
let snapshot = null;
let previewUrl = '';
let demoMode = '';
let searchResults = [];
let searchIndex = -1;

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
  container.querySelector('#ep-poll').value = cfg.poll_seconds || 30;
  container.querySelector('#ep-display').value = cfg.display_seconds || 180;
  container.querySelector('#ep-partial').value = cfg.partial_before_full || 6;
  container.querySelector('#ep-gateway-enable').checked = !!cfg.enabled;

  const endpoint = gatewayEndpoint(gateway);
  const stateClass = gateway.running ? 'ok' : gateway.enabled ? 'warn' : 'idle';
  const stateText = gateway.running ? '局域网网关运行中' : gateway.enabled ? '已启用但监听失败' : '未启用（安全默认）';
  container.querySelector('#ep-gateway-state').innerHTML = `
    <div class="ep-gateway-line"><span class="ep-gateway-dot ${stateClass}"></span><b>${esc(stateText)}</b></div>
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
  const image = container.querySelector('#ep-preview-image');
  const loading = container.querySelector('#ep-preview-loading');
  const meta = container.querySelector('#ep-preview-meta');
  loading.style.display = 'grid';
  image.classList.add('loading');
  try {
    const [state, blob] = await Promise.all([api.deviceState(demoMode), api.devicePreview(demoMode)]);
    revokePreview();
    previewUrl = URL.createObjectURL(blob);
    image.src = previewUrl;
    const em = state.emotion || {};
    const focus = state.focus || {};
    const quality = state.quality || {};
    meta.innerHTML = `
      <span><b>${esc(focus.code || '--')}</b> ${esc(focus.name || '')}</span>
      <span>温度 <b>${em.temperature ?? '--'}°</b> · ${esc(em.phase || '--')}</span>
      <span>通达信 <b>${esc(quality.tdx_status || '--')}</b></span>
      <span>${esc(state.generated_at || '--')}</span>
    `;
    container.querySelector('#ep-frame-hash').textContent = '帧已按 1bpp / MSB-first 生成 · ESP32 实际接收 48,000 bytes';
  } catch (error) {
    meta.innerHTML = `<span class="up">预览生成失败：${esc(error.message)}</span>`;
    container.querySelector('#ep-frame-hash').textContent = '可稍后重试；预览失败不会修改任何设备配置';
  } finally {
    loading.style.display = 'none';
    image.classList.remove('loading');
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
        <span class="epaper-kicker">HARDWARE LAB · 无实物开发模式</span>
        <h2>微雪 7.5 英寸墨水屏</h2>
        <p>800 × 480 黑白裸屏 · ESP32 WiFi 驱动板 · 深脉负责数据与画面，设备只读显示</p>
      </div>
      <span class="epaper-ready">软件链路可测试</span>
    </div>

    <div class="grid g12 epaper-grid">
      <div class="card span-8 epaper-preview-card">
        <div class="card-head">
          <div>
            <div class="card-title">真实设备帧模拟器</div>
            <div class="card-sub">与 ESP32 下载内容完全相同 · 1 bit 黑白 · 48,000 bytes</div>
          </div>
          <div class="ep-preview-tabs" role="tablist" aria-label="模拟场景">
            <button class="ep-preview-tab active" data-demo="" role="tab" aria-selected="true">常规看板</button>
            <button class="ep-preview-tab" data-demo="alert" role="tab" aria-selected="false">提醒演示</button>
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
        <div class="card-head"><div><div class="card-title">显示配置</div><div class="card-sub">现在保存，实物到货后直接复用</div></div></div>
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
          <option value="alert">提醒优先</option>
        </select></label>
        <div class="ep-field-row">
          <label class="ep-field"><span>数据轮询</span><select id="ep-poll">
            <option value="15">15 秒</option><option value="30">30 秒</option><option value="60">60 秒</option>
          </select></label>
          <label class="ep-field"><span>普通刷新</span><select id="ep-display">
            <option value="60">1 分钟</option><option value="180">3 分钟</option><option value="300">5 分钟</option>
          </select></label>
        </div>
        <label class="ep-field"><span>局刷后全刷</span><select id="ep-partial">
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

      <div class="card span-5">
        <div class="card-head"><div><div class="card-title">到货后只剩这些工作</div><div class="card-sub">软件侧无需推倒重来</div></div></div>
        <ol class="ep-checklist">
          <li><b>识别屏幕批次</b><span>核对背面 V2 标签和完整物料型号</span></li>
          <li><b>确认排线与拨码</b><span>先断电连接，按屏幕型号选择 A/B 档</span></li>
          <li><b>测全刷与局刷</b><span>校准 LUT、残影阈值与温度影响</span></li>
          <li><b>接入提醒外设</b><span>可选蜂鸣器、LED和实体确认键</span></li>
        </ol>
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
    demoMode = button.dataset.demo || '';
    container.querySelectorAll('.ep-preview-tab').forEach(row => {
      const active = row === button;
      row.classList.toggle('active', active);
      row.setAttribute('aria-selected', String(active));
    });
    await renderPreview(container);
  }));
  container.querySelector('#ep-refresh-preview').addEventListener('click', () => renderPreview(container));
  container.querySelector('#ep-download-frame').addEventListener('click', async event => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      const blob = await api.deviceFrame(demoMode);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url; anchor.download = `deeppulse-800x480-${demoMode || 'focus'}.bin`; anchor.click();
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

  Promise.allSettled([loadConfig(container), renderPreview(container)]);
}

export async function refresh(container) {
  init(container);
  await Promise.allSettled([loadConfig(container), renderPreview(container)]);
}

