/* 深脉 DeepPulse — ECharts 图表构建器（深色金融终端主题）
   涨=红 #f6465d  跌=绿 #2ebd85（A股惯例） */

import { UP, DOWN, FLAT, ACCENT, PHASE_COLORS } from './util.js?v=1.5.2';

const registry = [];
if (typeof window !== 'undefined') {
  window.addEventListener('resize', () => registry.forEach(c => c.resize()));
}

export function initChart(el) {
  const chart = echarts.init(el, null, { renderer: 'canvas' });
  registry.push(chart);
  return chart;
}

export function disposeChart(el) {
  const i = registry.findIndex(c => c.getDom() === el);
  if (i >= 0) { registry[i].dispose(); registry.splice(i, 1); }
}

const AXIS_LABEL = { color: '#5e6c88', fontSize: 10, fontFamily: 'inherit' };
const SPLIT_LINE = { lineStyle: { color: 'rgba(148,163,184,.07)' } };
const TOOLTIP = {
  backgroundColor: 'rgba(20,26,41,.96)', borderColor: 'rgba(148,163,184,.25)',
  textStyle: { color: '#e9eef8', fontSize: 11.5 }, extraCssText: 'border-radius:9px;box-shadow:0 8px 24px rgba(0,0,0,.5);',
};

/** 图表主题适配：浅色模式下轴文字/tooltip 跟随外壳，K线红绿与数据区配色不变。
    共享对象原地更新——ECharts 在 setOption 时已拷贝取值，重渲染即生效。 */
export function applyChartTheme(light) {
  AXIS_LABEL.color = light ? '#667085' : '#5e6c88';
  SPLIT_LINE.lineStyle.color = light ? 'rgba(15,23,42,.08)' : 'rgba(148,163,184,.07)';
  TOOLTIP.backgroundColor = light ? 'rgba(255,255,255,.97)' : 'rgba(20,26,41,.96)';
  TOOLTIP.borderColor = light ? 'rgba(15,23,42,.14)' : 'rgba(148,163,184,.25)';
  TOOLTIP.textStyle.color = light ? '#101828' : '#e9eef8';
  TOOLTIP.extraCssText = 'border-radius:9px;box-shadow:0 8px 24px rgba(0,0,0,.25);';
}

const PHASE_ZONES = [
  { from: 0, to: 20, color: PHASE_COLORS.blue },
  { from: 20, to: 40, color: PHASE_COLORS.cyan },
  { from: 40, to: 60, color: PHASE_COLORS.amber },
  { from: 60, to: 80, color: PHASE_COLORS.red },
  { from: 80, to: 100, color: PHASE_COLORS.violet },
];

/* ---------------- 情绪温度计（半圆仪表） ---------------- */
export function gaugeChart(el, temp) {
  const chart = initChart(el);
  const t = Math.max(0, Math.min(100, temp ?? 0));
  chart.setOption({
    series: [{
      type: 'gauge', startAngle: 200, endAngle: -20, min: 0, max: 100,
      radius: '92%', center: ['50%', '78%'],
      pointer: { show: true, length: '58%', width: 4, itemStyle: { color: '#e9eef8', shadowColor: 'rgba(233,238,248,.5)', shadowBlur: 8 } },
      anchor: { show: true, size: 10, itemStyle: { color: '#e9eef8', borderColor: '#0f1420', borderWidth: 3 } },
      progress: { show: true, width: 14, roundCap: true, itemStyle: { color: PHASE_COLORS[zoneColor(t)], shadowColor: PHASE_COLORS[zoneColor(t)], shadowBlur: 14 } },
      axisLine: {
        lineStyle: {
          width: 14,
          color: PHASE_ZONES.map(z => [z.to / 100, z.color + '55']),
        },
      },
      axisTick: { show: false },
      splitLine: { distance: -24, length: 6, lineStyle: { color: 'rgba(148,163,184,.35)', width: 1 } },
      axisLabel: { distance: -14, color: '#5e6c88', fontSize: 9.5 },
      detail: { show: false },
      title: { show: false },
      data: [{ value: t, name: '' }],
    }],
  });
  return chart;
}

function zoneColor(t) {
  if (t < 20) return 'blue';
  if (t < 40) return 'cyan';
  if (t < 60) return 'amber';
  if (t < 80) return 'red';
  return 'violet';
}

/* ---------------- 市场宽度条 ---------------- */
export function breadthChart(el, up, down, flat) {
  const chart = initChart(el);
  chart.setOption({
    grid: { left: 6, right: 6, top: 4, bottom: 4, containLabel: true },
    tooltip: { ...TOOLTIP, trigger: 'item', formatter: p => `${p.name}：${p.value} 家` },
    xAxis: { type: 'value', show: false, max: 'dataMax' },
    yAxis: { type: 'category', show: false, data: [''] },
    series: [{
      type: 'bar', stack: 'w', barWidth: 22,
      label: { show: true, position: 'insideLeft', color: '#fff', fontSize: 11, formatter: p => (p.value > 400 ? p.value : '') },
      itemStyle: { color: UP, borderRadius: [9, 0, 0, 9] },
      data: [up], name: `上涨 ${up}`,
    }, {
      type: 'bar', stack: 'w',
      itemStyle: { color: 'rgba(139,149,168,.5)' },
      data: [flat], name: `平盘 ${flat}`,
    }, {
      type: 'bar', stack: 'w',
      label: { show: true, position: 'insideRight', color: '#fff', fontSize: 11, formatter: p => (p.value > 400 ? p.value : '') },
      itemStyle: { color: DOWN, borderRadius: [0, 9, 9, 0] },
      data: [down], name: `下跌 ${down}`,
    }],
  });
  return chart;
}

/* ---------------- 情绪温度历史 ---------------- */
export function tempHistoryChart(el, snaps) {
  const chart = initChart(el);
  const dates = snaps.map(s => s.date.slice(5));
  const temps = snaps.map(s => s.temp);
  const colors = snaps.map(s => PHASE_COLORS[s.color] || ACCENT);
  const areas = [
    { name: '冰点', yAxis: 0, itemStyle: { color: 'rgba(79,140,255,.06)' } },
    { name: '修复', yAxis: 20, itemStyle: { color: 'rgba(34,211,238,.06)' } },
    { name: '发酵', yAxis: 40, itemStyle: { color: 'rgba(240,185,11,.06)' } },
    { name: '高潮', yAxis: 60, itemStyle: { color: 'rgba(246,70,93,.06)' } },
  ];
  chart.setOption({
    tooltip: { ...TOOLTIP, trigger: 'axis', formatter: ps => {
      const p = ps.find(x => x.seriesName === '情绪温度');
      return p ? `${p.axisValue}<br/>情绪温度 <b>${p.value}°</b>` : '';
    } },
    grid: { left: 34, right: 10, top: 16, bottom: 24 },
    xAxis: { type: 'category', data: dates, axisLabel: AXIS_LABEL, axisLine: SPLIT_LINE, boundaryGap: false },
    yAxis: { type: 'value', min: 0, max: 100, axisLabel: AXIS_LABEL, splitLine: SPLIT_LINE, name: '温度', nameTextStyle: { color: '#5e6c88', fontSize: 10 } },
    series: [{
      name: '情绪温度', type: 'line', data: temps, smooth: .45, symbol: 'circle', symbolSize: 5,
      lineStyle: { width: 2.4, color: ACCENT, shadowColor: 'rgba(79,140,255,.45)', shadowBlur: 10 },
      itemStyle: { color: p => colors[p.dataIndex] || ACCENT, borderColor: '#0b0f19', borderWidth: 1.5 },
      markArea: { silent: true, data: areas.map(a => [{ yAxis: a.yAxis, itemStyle: a.itemStyle }, { yAxis: a.yAxis + 20 }]) },
      areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: 'rgba(79,140,255,.22)' }, { offset: 1, color: 'rgba(79,140,255,0)' }] } },
    }],
  });
  return chart;
}

/* ---------------- 昨日涨停/连板指数（归一化） ---------------- */
export function ztIdxChart(el, seriesList) {
  const chart = initChart(el);
  const series = seriesList.map(({ name, rows, color }) => {
    let acc = 100;
    const data = rows.map(r => {
      if (rows.indexOf(r) === 0) { acc = 100; return [r.date, 100]; }
      const prev = rows[rows.indexOf(r) - 1];
      acc = acc * (1 + (r.close - prev.close) / prev.close);
      return [r.date, Number(acc.toFixed(2))];
    });
    return { name, data, color, type: 'line', smooth: .35, symbol: 'none',
             lineStyle: { width: 2, color }, areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: color + '33' }, { offset: 1, color: color + '00' }] } } };
  });
  chart.setOption({
    tooltip: { ...TOOLTIP, trigger: 'axis' },
    legend: { show: true, top: 2, right: 6, itemWidth: 14, itemHeight: 2, textStyle: { color: '#9aa8c0', fontSize: 11 } },
    grid: { left: 40, right: 12, top: 30, bottom: 24 },
    xAxis: { type: 'time', axisLabel: AXIS_LABEL, splitLine: { show: false } },
    yAxis: { type: 'value', scale: true, axisLabel: AXIS_LABEL, splitLine: SPLIT_LINE },
    series,
  });
  return chart;
}

/* ---------------- K线 + 均线 + 成交量 + MACD ---------------- */
export function klineChart(el, rows, opts = {}) {
  const chart = initChart(el);
  const n = rows.length;
  if (!n) { chart.clear(); return chart; }
  const dates = rows.map(r => r.date);
  const ohlc = rows.map(r => [r.open, r.close, r.low, r.high]);
  const vols = rows.map((r, i) => ({
    value: r.volume, itemStyle: { color: r.close >= r.open ? UP : DOWN },
  }));
  const closes = rows.map(r => r.close);

  const ma = (p) => closes.map((_, i) => {
    if (i < p - 1) return '-';
    let s = 0; for (let j = i - p + 1; j <= i; j++) s += closes[j];
    return +(s / p).toFixed(2);
  });
  const maColors = { 5: '#f7d154', 10: '#ff8a5c', 20: '#22d3ee', 60: '#c084fc' };
  const maSeries = (opts.ma || [5, 10, 20, 60]).map(p => ({
    name: 'MA' + p, type: 'line', data: ma(p), smooth: true, symbol: 'none',
    lineStyle: { width: 1, color: maColors[p] || '#9aa8c0' }, emphasis: { disabled: true },
  }));

  // MACD (12,26,9)
  const ema = (arr, p) => {
    const k = 2 / (p + 1); const out = [];
    arr.forEach((v, i) => out.push(i === 0 ? v : v * k + out[i - 1] * (1 - k)));
    return out;
  };
  const e12 = ema(closes, 12), e26 = ema(closes, 26);
  const dif = closes.map((_, i) => +(e12[i] - e26[i]).toFixed(3));
  const dea = ema(dif, 9).map(v => +v.toFixed(3));
  const hist = dif.map((v, i) => +(2 * (v - dea[i])).toFixed(3));
  const macdBar = hist.map(v => ({ value: v, itemStyle: { color: v >= 0 ? UP : DOWN } }));

  const name = opts.name || '';
  const pct = opts.pct || 2;
  const indicator = opts.indicator || 'macd';

  // 副图指标：macd / kdj / rsi
  let subTitle = 'MACD(12,26,9)';
  const subSeries = [];
  if (indicator === 'kdj') {
    subTitle = 'KDJ(9,3,3)';
    const kLine = [], dLine = [], jLine = [];
    let k = 50, d = 50;
    for (let i = 0; i < n; i++) {
      const lo = Math.min(...rows.slice(Math.max(0, i - 8), i + 1).map(r => r.low));
      const hi = Math.max(...rows.slice(Math.max(0, i - 8), i + 1).map(r => r.high));
      const rsv = hi === lo ? 50 : (closes[i] - lo) / (hi - lo) * 100;
      k = (2 / 3) * k + (1 / 3) * rsv;
      d = (2 / 3) * d + (1 / 3) * k;
      kLine.push(+k.toFixed(2)); dLine.push(+d.toFixed(2)); jLine.push(+(3 * k - 2 * d).toFixed(2));
    }
    subSeries.push(
      { name: 'K', type: 'line', xAxisIndex: 2, yAxisIndex: 2, data: kLine, symbol: 'none', lineStyle: { width: 1.2, color: '#f0b90b' } },
      { name: 'D', type: 'line', xAxisIndex: 2, yAxisIndex: 2, data: dLine, symbol: 'none', lineStyle: { width: 1.2, color: '#22d3ee' } },
      { name: 'J', type: 'line', xAxisIndex: 2, yAxisIndex: 2, data: jLine, symbol: 'none', lineStyle: { width: 1.2, color: '#c084fc' } },
    );
  } else if (indicator === 'rsi') {
    subTitle = 'RSI(6,12,24)';
    const rsiOf = (p) => {
      const out = [];
      let upSum = 0, downSum = 0;
      for (let i = 0; i < n; i++) {
        const chg = i === 0 ? 0 : closes[i] - closes[i - 1];
        const up = Math.max(chg, 0), down = Math.max(-chg, 0);
        if (i === 0) { out.push(50); continue; }
        if (i <= p) {
          upSum += up; downSum += down;
          if (i === p) {
            const rs = downSum === 0 ? 999 : upSum / downSum;
            out.push(+(100 - 100 / (1 + rs)).toFixed(2));
          } else {
            out.push(50);
          }
          continue;
        }
        upSum = (upSum * (p - 1) + up) / p;
        downSum = (downSum * (p - 1) + down) / p;
        const rs = downSum === 0 ? 999 : upSum / downSum;
        out.push(+(100 - 100 / (1 + rs)).toFixed(2));
      }
      return out;
    };
    const rsi6 = rsiOf(6), rsi12 = rsiOf(12), rsi24 = rsiOf(24);
    subSeries.push(
      { name: 'RSI6', type: 'line', xAxisIndex: 2, yAxisIndex: 2, data: rsi6, symbol: 'none', lineStyle: { width: 1.2, color: '#f0b90b' } },
      { name: 'RSI12', type: 'line', xAxisIndex: 2, yAxisIndex: 2, data: rsi12, symbol: 'none', lineStyle: { width: 1.2, color: '#22d3ee' } },
      { name: 'RSI24', type: 'line', xAxisIndex: 2, yAxisIndex: 2, data: rsi24, symbol: 'none', lineStyle: { width: 1.2, color: '#c084fc' } },
    );
    if (indicator === 'rsi') {
      // RSI 超买超卖参考线（70/30）
      subSeries.push(
        { name: 'RSI高', type: 'line', xAxisIndex: 2, yAxisIndex: 2, data: rows.map(() => 70), symbol: 'none', lineStyle: { width: 1, type: 'dashed', color: 'rgba(246,70,93,.4)' } },
        { name: 'RSI低', type: 'line', xAxisIndex: 2, yAxisIndex: 2, data: rows.map(() => 30), symbol: 'none', lineStyle: { width: 1, type: 'dashed', color: 'rgba(46,189,133,.4)' } },
      );
    }
  } else {
    subSeries.push(
      { name: 'MACD', type: 'bar', xAxisIndex: 2, yAxisIndex: 2, data: macdBar, barWidth: '48%' },
      { name: 'DIF', type: 'line', xAxisIndex: 2, yAxisIndex: 2, data: dif, symbol: 'none', lineStyle: { width: 1, color: '#f0b90b' } },
      { name: 'DEA', type: 'line', xAxisIndex: 2, yAxisIndex: 2, data: dea, symbol: 'none', lineStyle: { width: 1, color: '#22d3ee' } },
    );
  }

  chart.setOption({
    animation: false,
    tooltip: {
      ...TOOLTIP, trigger: 'axis', axisPointer: { type: 'cross', lineStyle: { color: 'rgba(148,163,184,.4)' } },
      formatter: ps => {
        const i = ps[0].dataIndex; const r = rows[i];
        return `<div style="font-size:12px">${r.date}</div>
          开 ${r.open.toFixed(pct)}　高 ${r.high.toFixed(pct)}<br/>
          低 ${r.low.toFixed(pct)}　收 <b style="color:${r.close >= r.open ? UP : DOWN}">${r.close.toFixed(pct)}</b><br/>
          涨跌幅 ${(r.pct || 0).toFixed(2)}%　量 ${(r.volume / 1e4).toFixed(1)}万手`;
      },
    },
    legend: {
      top: 0, left: 6, itemWidth: 13, itemHeight: 2, textStyle: { color: '#9aa8c0', fontSize: 10.5 },
      data: maSeries.map(s => s.name).concat(subTitle),
    },
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    grid: [
      { left: 52, right: 14, top: 22, height: '52%' },
      { left: 52, right: 14, top: '64%', height: '13%' },
      { left: 52, right: 14, top: '80%', height: '14%' },
    ],
    xAxis: [
      { type: 'category', data: dates, gridIndex: 0, axisLabel: { show: false }, axisLine: SPLIT_LINE, boundaryGap: true },
      { type: 'category', data: dates, gridIndex: 1, axisLabel: { show: false }, axisLine: SPLIT_LINE },
      { type: 'category', data: dates, gridIndex: 2, axisLabel: AXIS_LABEL, axisLine: SPLIT_LINE },
    ],
    yAxis: [
      { scale: true, gridIndex: 0, axisLabel: AXIS_LABEL, splitLine: SPLIT_LINE, position: 'left' },
      { gridIndex: 1, axisLabel: { show: false }, splitLine: { show: false }, position: 'left' },
      { gridIndex: 2, axisLabel: { show: false }, splitLine: { show: false }, position: 'left' },
    ],
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1, 2], start: Math.max(0, 100 - 13000 / n), end: 100 },
      { type: 'slider', xAxisIndex: [0, 1, 2], top: '96%', height: 16, borderColor: 'rgba(148,163,184,.15)',
        backgroundColor: 'rgba(15,20,32,.6)', fillerColor: 'rgba(79,140,255,.14)',
        handleStyle: { color: '#4f8cff' }, textStyle: { color: '#5e6c88', fontSize: 9.5 },
        dataBackground: { lineStyle: { color: 'rgba(148,163,184,.2)' }, areaStyle: { color: 'rgba(148,163,184,.06)' } } },
    ],
    series: [
      {
        name, type: 'candlestick', data: ohlc, itemStyle: { color: UP, color0: DOWN, borderColor: UP, borderColor0: DOWN },
        // 情绪阶段色带：周期直接标注在K线上（市场级背景，任意标的适用）
        ...(opts.bands && opts.bands.length ? {
          markArea: {
            silent: true,
            data: opts.bands.map(b => [
              { xAxis: b.start, itemStyle: { color: (PHASE_COLORS[b.color] || ACCENT) + '1f' } },
              { xAxis: b.end },
            ]),
          },
        } : {}),
      },
      ...maSeries,
      { name: 'VOL', type: 'bar', xAxisIndex: 1, yAxisIndex: 1, data: vols, barWidth: '62%' },
      ...subSeries,
    ],
  }, true);  // notMerge：切换指标时彻底替换旧系列
  return chart;
}

/* ---------------- 盘中情绪温度轨迹 ---------------- */
export function intradayChart(el, points) {
  const chart = initChart(el);
  chart.setOption({
    tooltip: { ...TOOLTIP, trigger: 'axis', formatter: ps => {
      const p = ps[0];
      return `${p.axisValue} 温度 <b>${p.value}°</b>`;
    } },
    grid: { left: 34, right: 10, top: 10, bottom: 20 },
    xAxis: { type: 'category', data: points.map(p => p.t), axisLabel: { ...AXIS_LABEL, fontSize: 9 }, axisLine: SPLIT_LINE, boundaryGap: false },
    yAxis: { type: 'value', min: 0, max: 100, axisLabel: AXIS_LABEL, splitLine: SPLIT_LINE },
    series: [{
      type: 'line', data: points.map(p => p.temp), smooth: true, symbol: 'circle', symbolSize: 3.5,
      lineStyle: { width: 2, color: ACCENT },
      itemStyle: { color: ACCENT },
      areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: 'rgba(79,140,255,.25)' }, { offset: 1, color: 'rgba(79,140,255,0)' }] } },
    }],
  }, true);
  return chart;
}

/* ---------------- 迷你走势（指数卡） ---------------- */
export function sparkChart(el, rows, color) {
  const chart = initChart(el);
  const closes = (rows || []).map(r => r.close);
  chart.setOption({
    grid: { left: 1, right: 1, top: 3, bottom: 2 },
    xAxis: { type: 'category', show: false, boundaryGap: false },
    yAxis: { type: 'value', show: false, scale: true },
    series: [{
      type: 'line', data: closes, symbol: 'none', smooth: true,
      lineStyle: { width: 1.4, color },
      areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: color + '2e' }, { offset: 1, color: color + '00' }] } },
    }],
  });
  return chart;
}

/* ---------------- 水平条形（题材热度/梯队分布） ---------------- */
export function hbarChart(el, labels, values, colorFn) {
  const chart = initChart(el);
  const data = labels.map((l, i) => ({ name: l, value: values[i], itemStyle: { color: colorFn ? colorFn(l, values[i], i) : ACCENT, borderRadius: 4 } }));
  chart.setOption({
    tooltip: { ...TOOLTIP, trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 8, right: 30, top: 6, bottom: 4, containLabel: true },
    xAxis: { type: 'value', axisLabel: AXIS_LABEL, splitLine: SPLIT_LINE },
    yAxis: { type: 'category', data: labels, axisLabel: { ...AXIS_LABEL, fontSize: 11 }, axisLine: SPLIT_LINE },
    series: [{
      type: 'bar', data, barWidth: 12,
      label: { show: true, position: 'right', color: '#9aa8c0', fontSize: 10.5 },
    }],
  });
  return chart;
}

/* ---------------- 涨跌分布直方图 ---------------- */
export function distChart(el, bins) {
  const chart = initChart(el);
  const keys = Object.keys(bins).map(Number).sort((a, b) => a - b);
  const labels = keys.map(k => (k === 11 ? '涨停' : k === -11 ? '跌停' : k > 0 ? `+${k}%` : k < 0 ? `${k}%` : '平'));
  const data = keys.map(k => ({
    value: bins[String(k)],
    itemStyle: { color: k > 0 ? UP : k < 0 ? DOWN : 'rgba(139,149,168,.6)', borderRadius: [3, 3, 0, 0] },
  }));
  chart.setOption({
    tooltip: { ...TOOLTIP, trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 34, right: 8, top: 12, bottom: 24 },
    xAxis: { type: 'category', data: labels, axisLabel: { ...AXIS_LABEL, fontSize: 9.5, rotate: 0 }, axisLine: SPLIT_LINE },
    yAxis: { type: 'value', axisLabel: AXIS_LABEL, splitLine: SPLIT_LINE },
    series: [{ type: 'bar', data, barWidth: '55%' }],
  });
  return chart;
}

/* ---------------- 主力资金流 ---------------- */
export function flowChart(el, rows) {
  const chart = initChart(el);
  const data = rows.map(r => ({
    value: +(r.main / 1e8).toFixed(1),
    itemStyle: { color: r.main >= 0 ? UP : DOWN, borderRadius: 3 },
  }));
  chart.setOption({
    tooltip: { ...TOOLTIP, trigger: 'axis', axisPointer: { type: 'shadow' }, formatter: ps => {
      const p = ps[0]; return `${p.axisValue}<br/>主力净流入 <b style="color:${p.value >= 0 ? UP : DOWN}">${p.value} 亿</b>`;
    } },
    grid: { left: 40, right: 8, top: 10, bottom: 24 },
    xAxis: { type: 'category', data: rows.map(r => r.date.slice(5)), axisLabel: { ...AXIS_LABEL, fontSize: 9.5 }, axisLine: SPLIT_LINE },
    yAxis: { type: 'value', axisLabel: AXIS_LABEL, splitLine: SPLIT_LINE, name: '亿', nameTextStyle: { color: '#5e6c88', fontSize: 9.5 } },
    series: [{ type: 'bar', data, barWidth: '55%' }],
  });
  return chart;
}
