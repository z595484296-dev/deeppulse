/* 深脉 DeepPulse — 自选页（分组 · 排序 · 批量 · 提醒） */

import { api } from '../api.js?v=1.5.0';
import {
  loadWatch, saveWatch, removeWatch, setWatchNote,
  batchRemoveWatch, batchMoveWatch, watchGroups, bus,
  loadAlerts, addAlert, removeAlert,
} from '../store.js?v=1.5.0';
import { fmtPct, fmtPrice, pctClass, esc, debounce, toast, emptyState } from '../util.js?v=1.5.0';

let built = false;
let timer = null;
let emCache = null;
let alertDraft = null;    // 待填提醒的股票（表格 🔔 点击预填）
let curGroup = '默认';     // 新添加自选的入组
let sortKey = 'pct';      // 默认按涨跌幅降序
let sortDir = -1;
const selected = new Set(); // 批量选择

const SORTABLE = {
  pct: '涨跌幅', price: '现价', turnover: '换手率', vr: '量比',
};

export function init(container) {
  if (built) return;
  built = true;
  container.innerHTML = `
    <div class="grid g12">
      <div class="card span-12" style="padding:13px 16px">
        <div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap">
          <div class="search-box" style="width:280px">
            <svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7" fill="none" stroke="currentColor" stroke-width="2"/><path d="M20 20l-3.8-3.8" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
            <input id="wt-search" placeholder="添加自选：代码或名称" autocomplete="off">
            <div class="search-results" id="wt-results"></div>
          </div>
          <div style="display:flex;gap:7px;align-items:center">
            <span style="font-size:11.5px;color:var(--text-3)">入组</span>
            <select id="wt-group" style="height:30px;border-radius:8px;background:var(--panel-2);border:1px solid var(--line);color:var(--text);font-size:12px;padding:0 8px;outline:none"></select>
            <input id="wt-new-group" placeholder="新分组名" style="display:none;height:30px;width:110px;border-radius:8px;background:var(--panel-2);border:1px solid var(--line);color:var(--text);font-size:12px;padding:0 8px;outline:none">
            <button class="btn sm" id="wt-new-group-ok" style="display:none">确定</button>
          </div>
          <span style="font-size:11.5px;color:var(--text-3)">数据保存在本地浏览器</span>
          <div style="margin-left:auto;display:flex;gap:8px">
            <button class="btn sm" id="wt-export">导出 JSON</button>
            <button class="btn sm" id="wt-import">导入</button>
            <input type="file" id="wt-file" accept=".json" style="display:none">
          </div>
        </div>
      </div>

      <div class="card span-12" id="wt-alerts-card">
        <div class="card-head">
          <div class="card-title">🔔 价格提醒</div>
          <div class="card-sub">自选股 5 秒自动检查 · 触发后播报一次</div>
        </div>
        <div class="alert-bar">
          <select id="wt-alert-stock" style="flex:0 0 auto;min-width:150px"></select>
          <select id="wt-alert-dir" style="flex:0 0 auto">
            <option value="up">上破 ≥</option>
            <option value="down">下破 ≤</option>
          </select>
          <input id="wt-alert-price" type="number" step="0.01" placeholder="目标价" style="flex:0 0 auto;width:110px">
          <button class="btn sm primary" id="wt-alert-add">添加提醒</button>
        </div>
        <div id="wt-alerts-list"></div>
      </div>

      <div class="card span-12">
        <div class="card-head">
          <div class="card-title">我的自选</div>
          <div class="card-sub" id="wt-count">0 只 · 5秒自动刷新</div>
        </div>
        <div class="batch-bar" id="wt-batch" style="display:none">
          <span id="wt-batch-n" class="bb-n">已选 0 只</span>
          <select id="wt-batch-group" class="bb-sel"></select>
          <button class="btn sm" id="wt-batch-move">移入分组</button>
          <button class="btn sm" id="wt-batch-del">批量删除</button>
          <button class="btn sm ghost" id="wt-batch-clear">取消选择</button>
        </div>
        <div class="table-scroll" style="max-height:640px">
          <table class="tbl">
            <thead><tr>
              <th class="c" style="width:34px"><input type="checkbox" id="wt-checkall" title="全选"></th>
              <th>名称</th>
              <th class="r sortable" data-sort="price">现价</th>
              <th class="r sortable active" data-sort="pct">涨跌幅<span class="sort-mark">▼</span></th>
              <th class="r">今开</th><th class="r">最高</th><th class="r">最低</th>
              <th class="r sortable" data-sort="turnover">换手率</th>
              <th class="r sortable" data-sort="vr">量比</th>
              <th>情绪标签</th>
              <th style="min-width:180px">备注</th>
              <th class="c">操作</th>
            </tr></thead>
            <tbody id="wt-body"></tbody>
          </table>
        </div>
        <div class="empty" id="wt-empty" style="display:none">暂无自选 · 用上方搜索框添加你关注的股票</div>
      </div>
    </div>
  `;

  const searchEl = container.querySelector('#wt-search');
  const resEl = container.querySelector('#wt-results');
  const doSearch = debounce(async (q) => {
    if (!q) { resEl.classList.remove('show'); return; }
    try {
      const hits = await api.search(q);
      resEl.innerHTML = hits.length
        ? hits.map(h => `<div class="sr-item" data-code="${esc(h.code)}" data-name="${esc(h.name)}">
            <span class="sr-name">${esc(h.name)}</span><span class="sr-code">${esc(h.code)}</span></div>`).join('')
        : '<div class="empty">未找到</div>';
      resEl.classList.add('show');
    } catch { /* 静默 */ }
  }, 260);
  searchEl.addEventListener('input', () => doSearch(searchEl.value.trim()));
  resEl.addEventListener('click', e => {
    const it = e.target.closest('.sr-item');
    if (!it) return;
    addToWatch(it.dataset.code, it.dataset.name, container);
    searchEl.value = '';
    resEl.classList.remove('show');
  });
  document.addEventListener('click', e => {
    if (!e.target.closest('.search-box')) resEl.classList.remove('show');
  });

  // ---- 分组管理 ----
  const groupSel = container.querySelector('#wt-group');
  const newGroupInp = container.querySelector('#wt-new-group');
  const newGroupOk = container.querySelector('#wt-new-group-ok');
  const renderGroups = () => {
    const groups = watchGroups(loadWatch());
    if (!groups.includes(curGroup)) groups.unshift(curGroup);
    groupSel.innerHTML = groups.map(g => `<option value="${esc(g)}">${esc(g)}</option>`).join('')
      + '<option value="__new__">＋ 新建分组…</option>';
    groupSel.value = curGroup;
    const bg = container.querySelector('#wt-batch-group');
    if (bg) bg.innerHTML = groups.map(g => `<option value="${esc(g)}">${esc(g)}</option>`).join('');
  };
  groupSel.addEventListener('change', () => {
    if (groupSel.value === '__new__') {
      newGroupInp.style.display = '';
      newGroupOk.style.display = '';
      newGroupInp.focus();
    } else {
      curGroup = groupSel.value;
      newGroupInp.style.display = 'none';
      newGroupOk.style.display = 'none';
    }
  });
  const commitNewGroup = () => {
    const name = newGroupInp.value.trim();
    if (!name) { toast('请输入分组名', 'err'); return; }
    if (name === '__new__') { toast('名称不可用', 'err'); return; }
    curGroup = name;
    newGroupInp.value = '';
    newGroupInp.style.display = 'none';
    newGroupOk.style.display = 'none';
    renderGroups();
    toast(`新分组「${name}」已创建，新添加的自选将进入该组`);
  };
  newGroupOk.addEventListener('click', commitNewGroup);
  newGroupInp.addEventListener('keydown', e => { if (e.key === 'Enter') commitNewGroup(); });

  // ---- 排序 ----
  container.querySelectorAll('th.sortable').forEach(th => {
    th.addEventListener('click', () => {
      const k = th.dataset.sort;
      if (sortKey === k) sortDir = -sortDir;
      else { sortKey = k; sortDir = -1; }
      container.querySelectorAll('th.sortable').forEach(x => {
        x.classList.toggle('active', x.dataset.sort === sortKey);
        const m = x.querySelector('.sort-mark');
        if (m) m.textContent = (x.dataset.sort === sortKey ? (sortDir > 0 ? '▲' : '▼') : '');
      });
      refresh(container);
    });
  });

  // ---- 表格委托 ----
  const body = container.querySelector('#wt-body');
  body.addEventListener('input', e => {
    const input = e.target.closest('.wt-note');
    if (input) setWatchNote(input.dataset.code, input.value);
  });
  body.addEventListener('change', e => {
    const cb = e.target.closest('[data-check]');
    if (cb) {
      if (cb.checked) selected.add(cb.dataset.check); else selected.delete(cb.dataset.check);
      renderBatchBar(container);
    }
  });
  body.addEventListener('click', e => {
    const bell = e.target.closest('[data-bell]');
    if (bell) {
      alertDraft = { code: bell.dataset.bell, name: bell.dataset.name || bell.dataset.bell };
      refreshStockOptions(container);
      container.querySelector('#wt-alert-stock').value = bell.dataset.bell;
      container.querySelector('#wt-alerts-card').scrollIntoView({ behavior: 'smooth', block: 'center' });
      setTimeout(() => container.querySelector('#wt-alert-price').focus(), 350);
      return;
    }
    const grp = e.target.closest('[data-move-one]');
    if (grp) {
      // 单行移组 = 选中该行 + 弹出批量移组栏（统一交互，无 prompt）
      selected.add(grp.dataset.moveOne);
      refresh(container);
      renderBatchBar(container);
      container.querySelector('#wt-batch-group').focus();
      toast('已选中，用上方「移入分组」操作');
      return;
    }
    const btn = e.target.closest('[data-act]');
    if (!btn) return;
    if (btn.dataset.act === 'del') {
      removeWatch(btn.dataset.code);
      selected.delete(btn.dataset.code);
      refresh(container);
      toast('已从自选移除');
    }
    if (btn.dataset.act === 'open') {
      document.dispatchEvent(new CustomEvent('open-quote', { detail: { code: btn.dataset.code, name: btn.dataset.name } }));
      document.querySelector('.nav-item[data-page="market"]').click();
    }
  });

  // ---- 批量操作 ----
  container.querySelector('#wt-checkall').addEventListener('change', e => {
    const codes = loadWatch().map(w => w.code);
    if (e.target.checked) codes.forEach(c => selected.add(c)); else selected.clear();
    refresh(container);
    renderBatchBar(container);
  });
  container.querySelector('#wt-batch-del').addEventListener('click', () => {
    const n = selected.size;
    batchRemoveWatch([...selected]);
    selected.clear();
    refresh(container);
    toast(`已批量移除 ${n} 只`);
  });
  container.querySelector('#wt-batch-move').addEventListener('click', () => {
    const g = container.querySelector('#wt-batch-group').value;
    batchMoveWatch([...selected], g);
    refresh(container);
    toast(`已将 ${selected.size} 只移入「${g}」`);
  });
  container.querySelector('#wt-batch-clear').addEventListener('click', () => {
    selected.clear();
    refresh(container);
    renderBatchBar(container);
  });

  // ---- 导入导出 ----
  container.querySelector('#wt-export').addEventListener('click', () => {
    const blob = new Blob([JSON.stringify(loadWatch(), null, 2)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `深脉自选_${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  });
  container.querySelector('#wt-import').addEventListener('click', () => container.querySelector('#wt-file').click());
  container.querySelector('#wt-file').addEventListener('change', e => {
    const f = e.target.files[0];
    if (!f) return;
    const rd = new FileReader();
    rd.onload = () => {
      try {
        const arr = JSON.parse(rd.result);
        if (!Array.isArray(arr)) throw new Error('bad');
        const cur = loadWatch();
        let added = 0;
        arr.forEach(it => {
          if (it && it.code && !cur.some(w => w.code === it.code)) {
            cur.push({ code: it.code, name: it.name || it.code, note: it.note || '', group: it.group || '默认', added: Date.now() });
            added++;
          }
        });
        saveWatch(cur);
        refresh(container, null);
        toast(`导入完成，新增 ${added} 只`);
      } catch { toast('导入失败：文件格式不正确', 'err'); }
    };
    rd.readAsText(f);
    e.target.value = '';
  });

  // ---- 价格提醒（设定与展示；到价检查由 app.js 全局轮询驱动） ----
  const alertsListEl = container.querySelector('#wt-alerts-list');
  function refreshStockOptions(c) {
    const sel = c.querySelector('#wt-alert-stock');
    const list = loadWatch();
    sel.innerHTML = list.length
      ? list.map(w => `<option value="${esc(w.code)}">${esc(w.name || w.code)}（${esc(w.code)}）</option>`).join('')
      : '<option value="">— 先添加自选 —</option>';
    if (alertDraft && list.some(w => w.code === alertDraft.code)) sel.value = alertDraft.code;
    else if (list.length) sel.value = list[0].code;
  }
  const renderAlerts = () => {
    const list = loadAlerts();
    if (!list.length) {
      alertsListEl.innerHTML = '<div class="empty" style="padding:14px">暂无提醒。给自选股设定目标价，到价我会主动播报 🔔</div>';
      return;
    }
    alertsListEl.innerHTML = `<div class="alert-list">` + list.map(a => `
      <div class="alert-item ${a.triggered ? 'triggered' : ''}">
        <span class="al-name">${esc(a.name || a.code)}</span>
        <span class="al-cond num">${a.dir === 'up' ? '上破 ≥' : '下破 ≤'} ${Number(a.price).toFixed(2)}</span>
        <span class="al-state">${a.triggered ? '✅ 已触发' : '⏳ 等待中'}</span>
        <button class="btn sm ghost" data-del-alert="${esc(a.id)}">删除</button>
      </div>`).join('') + '</div>';
  };
  container.querySelector('#wt-alert-add').addEventListener('click', () => {
    const code = container.querySelector('#wt-alert-stock').value;
    const price = parseFloat(container.querySelector('#wt-alert-price').value);
    if (!code) { toast('先在自选里添加股票', 'err'); return; }
    if (!price || price <= 0) { toast('请填写有效的目标价', 'err'); return; }
    const w = loadWatch().find(x => x.code === code);
    addAlert({ code, name: w ? w.name : code, dir: container.querySelector('#wt-alert-dir').value, price });
    container.querySelector('#wt-alert-price').value = '';
    toast(`已设提醒：${w ? w.name : code} ${container.querySelector('#wt-alert-dir').value === 'up' ? '上破' : '下破'} ${price}`);
  });
  alertsListEl.addEventListener('click', e => {
    const b = e.target.closest('[data-del-alert]');
    if (b) removeAlert(b.dataset.delAlert);
  });
  bus.addEventListener('alerts', renderAlerts);
  bus.addEventListener('watch', () => {
    refreshStockOptions(container);
    renderGroups();
    renderAlerts();
  });
  refreshStockOptions(container);
  renderGroups();
  renderAlerts();

  bus.addEventListener('watch', () => refresh(container));

  if (timer) clearInterval(timer);
  timer = setInterval(() => refresh(container), 5000);
}

function renderBatchBar(container) {
  const bar = container.querySelector('#wt-batch');
  const n = selected.size;
  bar.style.display = n ? '' : 'none';
  if (n) container.querySelector('#wt-batch-n').textContent = `已选 ${n} 只`;
  container.querySelector('#wt-checkall').checked = n > 0 && n === loadWatch().length;
}

function addToWatch(code, name, container) {
  const list = loadWatch();
  if (list.some(w => w.code === code)) { toast('已在自选中', 'err'); return; }
  list.push({ code, name, note: '', group: curGroup, added: Date.now() });
  saveWatch(list);
  refresh(container);
  toast(`已添加 ${name}（${curGroup}）`);
}

const sortVal = (q, key) => {
  if (!q || q._err) return -Infinity;
  if (key === 'price') return q.price ?? -Infinity;
  if (key === 'pct') return q.pct ?? -Infinity;
  if (key === 'turnover') return q.turnover ?? -Infinity;
  if (key === 'vr') return q.vol_ratio ?? -Infinity;
  return 0;
};

async function refresh(container) {
  if (!built) return;
  const list = loadWatch();
  const countEl = container.querySelector('#wt-count');
  const body = container.querySelector('#wt-body');
  const emptyEl = container.querySelector('#wt-empty');
  if (countEl) countEl.textContent = list.length + ' 只 · 5秒自动刷新 · ' + SORTABLE[sortKey] + (sortDir > 0 ? '升序' : '降序');
  renderBatchBar(container);
  if (!list.length) {
    body.innerHTML = '';
    if (emptyEl) {
      emptyEl.style.display = 'block';
      emptyState(emptyEl, '⭐', '还没有自选', '用上方搜索框添加你关注的股票——我会实时盯盘，并自动叠加涨停/连板/炸板情绪标签');
    }
    return;
  }
  if (emptyEl) emptyEl.style.display = 'none';

  // 情绪池缓存（60秒）
  if (!emCache || Date.now() - emCache.ts > 60000) {
    try { emCache = { ts: Date.now(), data: await api.emotion() }; } catch { /* 静默 */ }
  }
  const pools = (emCache && emCache.data && emCache.data.pools) || null;

  const quotes = await Promise.all(list.map(async (w) => {
    try {
      const q = await api.quote(w.code);
      q._watch = w;
      return q;
    } catch {
      return { _watch: w, _err: true };
    }
  }));
  quotes.sort((a, b) => sortDir * (sortVal(b, sortKey) - sortVal(a, sortKey)));

  // 按分组渲染（保持排序，组内有序）
  const groups = [];
  quotes.forEach(q => {
    const g = q._watch.group || '默认';
    if (!groups.includes(g)) groups.push(g);
  });
  body.innerHTML = groups.map(g => {
    const rows = quotes.filter(q => (q._watch.group || '默认') === g);
    const header = `<tr class="grp-row" data-group="${esc(g)}"><td colspan="13">
      <span class="grp-name">📁 ${esc(g)}</span><span class="grp-count">${rows.length} 只</span></td></tr>`;
    return header + rows.map(q => rowHtml(q, pools, selected)).join('');
  }).join('');
}

function rowHtml(q, pools, selectedSet) {
  const w = q._watch;
  if (q._err) {
    return `<tr>
      <td class="c"><input type="checkbox" data-check="${esc(w.code)}" ${selectedSet.has(w.code) ? 'checked' : ''}></td>
      <td><div class="name-cell"><b>${esc(w.name || w.code)}</b><span class="code-sub">${esc(w.code)}</span></div></td>
      <td colspan="11" class="r" style="color:var(--text-3)">行情暂不可用，稍后自动重试</td>
    </tr>`;
  }
  const cls = pctClass(q.pct);
  let tags = '';
  if (pools) {
    const zt = pools.ZT.pool.find(it => it.code === w.code);
    const dt = pools.DT.pool.find(it => it.code === w.code);
    const zb = pools.ZB.pool.find(it => it.code === w.code);
    if (zt) tags += `<span class="badge red">${zt.lbc >= 2 ? zt.lbc + '连板' : '涨停'}</span> `;
    if (dt) tags += '<span class="badge green">跌停</span> ';
    if (zb) tags += '<span class="badge amber">炸板</span> ';
  }
  if (!tags) tags = '<span class="badge gray">普通</span>';
  return `<tr>
    <td class="c"><input type="checkbox" data-check="${esc(w.code)}" ${selectedSet.has(w.code) ? 'checked' : ''}></td>
    <td><div class="name-cell" style="cursor:pointer" data-act="open" data-code="${esc(w.code)}" data-name="${esc(w.name)}">
      <b>${esc(w.name || q.name)}</b><span class="code-sub">${esc(w.code)}</span></div></td>
    <td class="r num ${cls}" style="font-weight:700">${fmtPrice(q.price)}</td>
    <td class="r num ${cls}" style="font-weight:650">${fmtPct(q.pct)}</td>
    <td class="r num">${fmtPrice(q.open)}</td>
    <td class="r num">${fmtPrice(q.high)}</td>
    <td class="r num">${fmtPrice(q.low)}</td>
    <td class="r num">${(q.turnover ?? 0).toFixed(2) + '%'}</td>
    <td class="r num">${q.vol_ratio ? q.vol_ratio.toFixed(2) : '--'}</td>
    <td>${tags}</td>
    <td><input class="wt-note" data-code="${esc(w.code)}" value="${esc(w.note || '')}"
      placeholder="记录逻辑…" style="width:100%;background:transparent;border:1px solid var(--line);border-radius:6px;color:var(--text-2);font-size:11.5px;padding:4px 8px;outline:none;font-family:inherit"></td>
    <td class="c" style="white-space:nowrap">
      <button class="btn sm ghost" data-bell="${esc(w.code)}" data-name="${esc(w.name || '')}" title="设价格提醒">🔔</button>
      <button class="btn sm ghost" data-move-one="${esc(w.code)}" title="移动分组">📁</button>
      <button class="btn sm ghost" data-act="del" data-code="${esc(w.code)}" title="移除">✕</button>
    </td>
  </tr>`;
}

export { refresh };
