# -*- coding: utf-8 -*-
"""深脉 DeepPulse · 情绪周期引擎 2.0。

目标不是预测确定涨跌，而是把 A 股短线生态压缩成可解释、可降级、可验证的状态：
温度 + 六维结构 + 变化速度 + 背离 + 数据可信度 + 启发式状态倾向。
"""

import json
import math
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WEIGHTS_FILE = os.path.join(BASE_DIR, 'data', 'weights.json')
MODEL_VERSION = '2.0'
REFERENCE_UNIVERSE = 5300.0


def clamp(v, lo=0.0, hi=100.0):
    return max(lo, min(hi, v))


def step(v, bands):
    """下界分档。bands 必须按下界从高到低排列。"""
    for threshold, score in bands:
        if v >= threshold:
            return score
    return bands[-1][1]


def step_upper(v, bands):
    """上界分档。bands 必须按上界从低到高排列。"""
    for threshold, score in bands:
        if v <= threshold:
            return score
    return bands[-1][1]


# 所有指标统一限制在 [-20, +20]，避免某一项仅因量纲更大而支配温度。
def score_zt(v):
    """按全 A 参考规模折算后的涨停家数。"""
    return step(v, [(130, 20), (100, 16), (80, 12), (60, 8),
                    (40, 3), (20, -5), (0, -15)])


def score_dt(v):
    """按全 A 参考规模折算后的跌停家数；越少越健康。"""
    return step_upper(v, [(3, 15), (8, 10), (15, 0), (25, -10), (math.inf, -20)])


def score_zb(rate):
    """炸板率越低，封板质量越好。"""
    return step_upper(rate, [(0.15, 10), (0.25, 5), (0.35, 0),
                             (0.45, -10), (math.inf, -20)])


def score_height(h):
    return step(h, [(6, 15), (5, 12), (4, 8), (3, 4), (2, 0), (1, -5), (0, -10)])


def score_lb_count(n):
    return step(n, [(12, 10), (8, 6), (5, 3), (3, 0), (1, -3), (0, -8)])


def score_zt_idx(pct):
    return step(pct, [(3.0, 15), (1.0, 10), (0.0, 5), (-1.0, 0),
                      (-3.0, -8), (-99, -15)])


def score_lb_idx(pct):
    return step(pct, [(3.0, 10), (1.0, 6), (0.0, 3), (-1.0, 0),
                      (-3.0, -5), (-99, -10)])


def score_breadth(up_ratio):
    return step(up_ratio, [(0.75, 10), (0.6, 6), (0.45, 0),
                           (0.3, -6), (0, -10)])


def score_volume(ratio):
    return step(ratio, [(1.3, 8), (1.1, 4), (0.9, 0), (0.7, -4), (0, -8)])


def score_flow(yi):
    return step(yi, [(200, 10), (100, 6), (0, 2), (-100, -2),
                     (-200, -6), (-99999, -10)])


def score_trend(pct_vs_ma20):
    return step(pct_vs_ma20, [(3.0, 5), (0.0, 2), (-3.0, -2), (-99, -5)])


# (key, 名称, 默认权重, 打分函数, 展示格式, 单位, 数据依赖)
# “主力净流入”口径不够透明，降为辅助权重；不把它当作独立事实结论。
INDICATORS = [
    ('zt', '涨停家数', 1.0, score_zt, '%d', '家', 'pool'),
    ('dt', '跌停家数', 1.0, score_dt, '%d', '家', 'pool'),
    ('zb', '炸板率', 1.0, score_zb, '%.0f', '%%', 'pool'),
    ('height', '最高连板', 0.8, score_height, '%d', '板', 'pool'),
    ('lb_count', '连板家数', 0.8, score_lb_count, '%d', '家', 'pool'),
    ('zt_idx', '昨日涨停指数', 1.0, score_zt_idx, '%+.2f', '%%', 'bk'),
    ('lb_idx', '昨日连板指数', 0.8, score_lb_idx, '%+.2f', '%%', 'bk'),
    ('breadth', '上涨家数占比', 0.8, score_breadth, '%.0f', '%%', 'breadth'),
    ('volume', '同刻量能比(20日)', 0.6, score_volume, '%.2f', '×', 'kline'),
    ('flow', '主力净流入(辅助)', 0.3, score_flow, '%+.0f', '亿', 'flow'),
    ('trend', '上证vs MA20', 0.4, score_trend, '%+.1f', '%%', 'kline'),
]

DEFAULT_WEIGHTS = {item[0]: item[2] for item in INDICATORS}
SOURCE_QUALITY = {'pool': 0.98, 'bk': 0.90, 'breadth': 0.95, 'kline': 0.95, 'flow': 0.65}


def load_weights():
    try:
        with open(WEIGHTS_FILE, encoding='utf-8') as handle:
            saved = json.load(handle)
        return {key: float(saved.get(key, DEFAULT_WEIGHTS[key])) for key in DEFAULT_WEIGHTS}
    except Exception:
        return dict(DEFAULT_WEIGHTS)


def save_weights(weights):
    clean = {}
    for key in DEFAULT_WEIGHTS:
        if key in weights:
            try:
                clean[key] = max(0.0, min(3.0, float(weights[key])))
            except (TypeError, ValueError):
                continue
    os.makedirs(os.path.dirname(WEIGHTS_FILE), exist_ok=True)
    with open(WEIGHTS_FILE, 'w', encoding='utf-8') as handle:
        json.dump(clean, handle, ensure_ascii=False, indent=1)
    return clean


PHASES = [
    (20, '冰点期', 'blue', '亏钱效应极致，市场冰封。等待亏钱效应收敛与回暖确认。'),
    (40, '修复期', 'cyan', '情绪开始回暖但仍易反复，优先观察修复是否获得广度与溢价确认。'),
    (60, '发酵期', 'amber', '赚钱效应扩散，重点判断主线持续性与梯队完整度。'),
    (80, '高潮期', 'red', '情绪处于高位，继续上行与高位分歧可能同时存在。'),
    (999, '亢奋期', 'violet', '情绪过热，拥挤与退潮风险明显上升。'),
]

POSITIONS = {
    '冰点期': ('0-2成', 10, '观察为主 · 等待回暖确认'),
    '修复期': ('2-4成', 30, '小仓验证 · 防止修复失败'),
    '发酵期': ('5-8成', 65, '围绕主线 · 服从强弱反馈'),
    '高潮期': ('5-7成', 55, '去弱留强 · 防范高位分歧'),
    '亢奋期': ('≤3成', 20, '降低拥挤暴露 · 等待再平衡'),
}

DIMENSIONS = [
    ('earning', '赚钱效应', ('zt', 'zt_idx')),
    ('loss_control', '亏钱收敛', ('dt', 'zb', 'lb_idx')),
    ('continuity', '接力持续性', ('height', 'lb_count', 'lb_idx')),
    ('breadth', '市场广度', ('breadth', 'trend')),
    ('liquidity', '流动性', ('volume', 'flow')),
    ('quality', '封板与溢价', ('zb', 'zt_idx', 'lb_idx')),
]


def phase_of(temp):
    for index, (threshold, name, color, description) in enumerate(PHASES):
        if temp < threshold:
            return index, name, color, description
    return len(PHASES) - 1, PHASES[-1][1], PHASES[-1][2], PHASES[-1][3]


def expected_volume_fraction(server_time):
    """A 股盘中累计成交占全天的启发式曲线；非交易时段返回 1。"""
    try:
        hm = str(server_time).split(' ')[-1][:5]
        hour, minute = (int(part) for part in hm.split(':'))
        current = hour * 60 + minute
    except (TypeError, ValueError):
        return 1.0
    points = [
        (570, 0.03), (600, 0.24), (630, 0.36), (660, 0.46), (690, 0.54),
        (780, 0.54), (810, 0.65), (840, 0.75), (870, 0.86), (900, 1.0),
    ]
    if current < points[0][0] or current >= points[-1][0]:
        return 1.0
    for (left_t, left_v), (right_t, right_v) in zip(points, points[1:]):
        if left_t <= current <= right_t:
            if right_t == left_t:
                return right_v
            ratio = (current - left_t) / (right_t - left_t)
            return left_v + (right_v - left_v) * ratio
    return 1.0


def fmt_val(key, value):
    if key in ('zt_idx', 'lb_idx', 'flow', 'trend', 'zb', 'volume'):
        value = round(value, 2)
    return value


def note_for(key, value):
    notes = {
        'zt': lambda v: '涨停 %d 家：' % v + ('情绪冰封' if v < 20 else '情绪低迷' if v < 40 else '情绪中性' if v < 60 else '情绪活跃' if v < 100 else '情绪过热'),
        'dt': lambda v: '跌停 %d 家：' % v + ('亏钱效应弱' if v <= 3 else '亏钱效应可控' if v <= 8 else '亏钱效应抬头' if v <= 15 else '亏钱效应扩散' if v <= 25 else '恐慌出清中'),
        'zb': lambda v: '炸板率 %.0f%%：' % (v * 100) + ('封板质量高' if v <= 0.15 else '封板质量较好' if v <= 0.25 else '分歧显现' if v <= 0.35 else '分歧加剧' if v <= 0.45 else '炸板潮'),
        'height': lambda v: '最高 %d 连板：' % v + ('空间打开' if v >= 6 else '主线有高度' if v >= 4 else '空间中等' if v >= 3 else '空间受限' if v >= 2 else '梯队断层'),
        'lb_count': lambda v: '连板 %d 家：' % v + ('梯队厚实' if v >= 12 else '梯队完整' if v >= 8 else '梯队一般' if v >= 5 else '梯队单薄' if v >= 1 else '情绪断档'),
        'zt_idx': lambda v: '昨日涨停指数 %+.2f%%：' % v + ('溢价丰厚' if v > 3 else '溢价良好' if v > 1 else '溢价正常' if v >= 0 else '溢价转负' if v > -3 else '打板负反馈强'),
        'lb_idx': lambda v: '昨日连板指数 %+.2f%%：' % v + ('高位接力赚钱' if v > 3 else '高位反馈尚可' if v > 0 else '高位开始亏钱' if v > -3 else '高位负反馈剧烈'),
        'breadth': lambda v: '上涨占比 %.0f%%：' % (v * 100) + ('普涨' if v > 0.75 else '多数上涨' if v > 0.6 else '涨跌互现' if v > 0.45 else '多数下跌' if v > 0.3 else '普跌'),
        'volume': lambda v: '同刻量能 %.2f×20日均量：' % v + ('明显放量' if v > 1.3 else '温和放量' if v > 1.1 else '量能平稳' if v > 0.9 else '缩量' if v > 0.7 else '地量'),
        'flow': lambda v: '聚合口径主力净流入 %+.0f 亿：' % v + ('大幅流入' if v > 200 else '净流入' if v > 0 else '小幅流出' if v > -100 else '明显流出') + '（辅助项）',
        'trend': lambda v: '上证相对 MA20 %+.1f%%：' % v + ('趋势强势' if v > 3 else '趋势向上' if v > 0 else '趋势走弱' if v > -3 else '趋势破位'),
    }
    return notes[key](value)


def _history_temps(history, current_date):
    rows = []
    for snap in history or []:
        if snap.get('date') == current_date:
            continue
        temp = snap.get('temp')
        if isinstance(temp, (int, float)):
            rows.append((str(snap.get('date') or ''), float(temp), snap))
    rows.sort(key=lambda item: item[0])
    return rows


def _dynamics(temp, history, current_date):
    rows = _history_temps(history, current_date)
    temps = [item[1] for item in rows]
    delta1 = round(temp - temps[-1], 1) if temps else None
    delta3 = round(temp - temps[-3], 1) if len(temps) >= 3 else None
    previous_delta = (temps[-1] - temps[-2]) if len(temps) >= 2 else None
    acceleration = round(delta1 - previous_delta, 1) if delta1 is not None and previous_delta is not None else None
    if delta1 is None:
        direction, arrow = '待积累', '·'
    elif delta1 >= 2:
        direction, arrow = '升温', '↑'
    elif delta1 <= -2:
        direction, arrow = '降温', '↓'
    else:
        direction, arrow = '震荡', '→'
    series = temps + [temp]
    streak = 0
    if len(series) >= 2:
        sign = 1 if series[-1] > series[-2] else -1 if series[-1] < series[-2] else 0
        if sign:
            for index in range(len(series) - 1, 0, -1):
                current_sign = 1 if series[index] > series[index - 1] else -1 if series[index] < series[index - 1] else 0
                if current_sign != sign:
                    break
                streak += sign
    return {
        'direction': direction, 'arrow': arrow, 'delta1': delta1, 'delta3': delta3,
        'acceleration': acceleration, 'streak': streak, 'history_points': len(temps),
    }


def _stabilized_phase(temp, history, current_date, severe_reversal=False):
    candidate = phase_of(temp)
    rows = _history_temps(history, current_date)
    if not rows:
        return candidate, False
    previous = rows[-1][2]
    previous_name = previous.get('phase')
    names = [phase[1] for phase in PHASES]
    if previous_name not in names or previous_name == candidate[1]:
        return candidate, False
    previous_candidate = phase_of(rows[-1][1])
    previous_index = names.index(previous_name)
    moving_down = candidate[0] < previous_index
    decisive = abs(temp - rows[-1][1]) >= 12 or abs(candidate[0] - previous_index) >= 2
    confirmed = previous_candidate[1] == candidate[1]
    if confirmed or decisive or (severe_reversal and moving_down):
        return candidate, False
    threshold, name, color, description = PHASES[previous_index]
    return (previous_index, name, color, description), True


def _dimensions(signals):
    by_key = {signal['key']: signal for signal in signals}
    result = []
    for key, name, members in DIMENSIONS:
        available = [by_key[member] for member in members if by_key.get(member, {}).get('avail')]
        member_weight = sum(item['weight'] for item in available)
        score = sum(item['score'] * item['weight'] for item in available) / member_weight if member_weight else 0.0
        value = round(clamp(50 + score * 2.5)) if available else None
        coverage = round(100 * len(available) / len(members))
        result.append({'key': key, 'name': name, 'value': value, 'coverage': coverage,
                       'members': list(members), 'available': bool(available)})
    return result


def _transition_estimate(temp, dynamics, score_map, zb_rate, dt):
    """非校准的状态倾向，只用于展示当前证据方向，不冒充回测概率。"""
    delta = dynamics.get('delta1') or 0.0
    up = 22.0 + max(delta, 0) * 2.2 + max(score_map.get('breadth', 0), 0) * 0.7 + max(score_map.get('zt_idx', 0), 0) * 0.5
    down = 22.0 + max(-delta, 0) * 2.2 + max(-score_map.get('breadth', 0), 0) * 0.7 + max(-score_map.get('lb_idx', 0), 0) * 0.8
    if zb_rate > 0.40:
        down += 14
    if dt >= 15:
        down += 12
    if temp >= 80:
        down += 14
    stay = max(20.0, 62.0 - abs(delta) * 2.0)
    total = up + stay + down
    values = [round(up * 100 / total), round(stay * 100 / total)]
    values.append(100 - sum(values))
    return {
        'upgrade': values[0], 'stay': values[1], 'downgrade': values[2],
        'calibrated': False, 'label': '启发式状态倾向（未做历史概率校准）',
    }


def compute_emotion(raw, history=None):
    """计算情绪状态。缺失数据会明确降低覆盖率与可信度。"""
    history = history or []
    zt, dt, zb = raw.get('zt') or 0, raw.get('dt') or 0, raw.get('zb') or 0
    pool = raw.get('zt_pool') or []
    breadth = raw.get('breadth') or {}
    indices = raw.get('indices') or []
    flows = raw.get('flows') or {}
    bk = raw.get('bk') or {}
    sh_rows = raw.get('sh_kline') or []

    deps = {
        'pool': not raw.get('pool_error', False),
        'bk': raw.get('bk_ok', False),
        'breadth': bool(breadth.get('bins')),
        'kline': raw.get('kline_ok', False),
        'flow': raw.get('flow_ok', False),
    }

    zt_pct = (bk.get('zt') or {}).get('pct')
    lb_pct = (bk.get('lb') or {}).get('pct')
    zt_idx_price = (bk.get('zt') or {}).get('price')
    lb_idx_price = (bk.get('lb') or {}).get('price')

    heights = [item.get('lbc') or 0 for item in pool]
    height = max(heights) if heights else 0
    lb_count = len([value for value in heights if value >= 2])

    up, down, flat = breadth.get('up') or 0, breadth.get('down') or 0, breadth.get('flat') or 0
    universe = breadth.get('total') or (up + down + flat)
    up_ratio = up / (up + down) if (up + down) else 0.5
    scale = REFERENCE_UNIVERSE / universe if universe and universe >= 1000 else 1.0
    zt_equiv, dt_equiv = zt * scale, dt * scale

    sh_amount = (indices[0].get('amount') or 0) if indices else 0
    sz_amount = (indices[1].get('amount') or 0) if len(indices) > 1 else 0
    turnover = sh_amount + sz_amount

    vols20 = [row.get('volume') or 0 for row in sh_rows[-21:-1]]
    avg20v = sum(vols20) / len(vols20) if vols20 else 0
    today_vol = (sh_rows[-1].get('volume') or 0) if sh_rows else 0
    volume_fraction = expected_volume_fraction(raw.get('server_time'))
    comparable_today_vol = today_vol / volume_fraction if 0 < volume_fraction < 1 else today_vol
    vol_ratio = comparable_today_vol / avg20v if avg20v else 1.0
    volume_basis = '盘中同刻折算' if volume_fraction < 1 else '收盘全日'

    def sum_flow(rows):
        return float((rows[-1].get('main') or 0)) if isinstance(rows, list) and rows else 0.0

    flow_yi = (sum_flow(flows.get('sh')) + sum_flow(flows.get('sz'))) / 1e8
    closes20 = [row.get('close') or 0 for row in sh_rows[-20:]]
    ma20 = sum(closes20) / len(closes20) if closes20 else 0
    last_close = closes20[-1] if closes20 else 0
    trend_pct = ((last_close / ma20) - 1) * 100 if ma20 else 0.0
    zb_rate = zb / (zt + zb) if (zt + zb) else 0.0

    display_values = {
        'zt': zt, 'dt': dt, 'zb': zb_rate, 'height': height, 'lb_count': lb_count,
        'zt_idx': zt_pct if zt_pct is not None else 0.0,
        'lb_idx': lb_pct if lb_pct is not None else 0.0,
        'breadth': up_ratio, 'volume': vol_ratio, 'flow': flow_yi, 'trend': trend_pct,
    }
    score_values = dict(display_values)
    score_values.update({'zt': zt_equiv, 'dt': dt_equiv})

    weights = load_weights()
    all_weight = sum(weights.get(key, default) for key, _name, default, *_rest in INDICATORS)
    available_weight = 0.0
    quality_weight = 0.0
    accumulator = 0.0
    signals = []
    for key, name, default_weight, scorer, value_format, unit, dependency in INDICATORS:
        weight = weights.get(key, default_weight)
        if not deps.get(dependency, False):
            signals.append({'key': key, 'name': name, 'value': None, 'display': '--', 'unit': unit,
                            'score': None, 'weight': weight, 'contribution': None,
                            'note': '上游数据暂不可用，本指标未参与评分', 'avail': False,
                            'dependency': dependency})
            continue
        value = display_values[key]
        score_value = score_values[key]
        shown_value = value * 100 if key in ('zb', 'breadth') else value
        score = float(scorer(score_value))
        available_weight += weight
        quality_weight += weight * SOURCE_QUALITY.get(dependency, 0.8)
        accumulator += score * weight
        note = note_for(key, value)
        if key in ('zt', 'dt') and abs(scale - 1.0) > 0.01:
            note += '；按全A参考规模折算为 %.1f 家参与评分' % score_value
        signals.append({
            'key': key, 'name': name, 'value': round(shown_value, 2) if key in ('zb', 'breadth') else fmt_val(key, value),
            'display': value_format % shown_value, 'unit': unit, 'score': round(score, 1),
            'weight': weight, 'contribution': round(score * weight, 1), 'note': note,
            'avail': True, 'dependency': dependency, 'normalized_value': round(score_value, 3),
        })

    # 加权平均得分 [-20,+20] 线性映射到 [0,100]。
    average_score = accumulator / available_weight if available_weight else 0.0
    temp = clamp(50.0 + average_score * 2.5)
    missing = [name for _key, name, _w, _fn, _fm, _unit, dependency in INDICATORS
               if not deps.get(dependency, False)]
    coverage = round(100 * available_weight / all_weight) if all_weight else 0
    source_quality = round(100 * quality_weight / available_weight) if available_weight else 0
    confidence = round(coverage * 0.75 + source_quality * 0.25) if available_weight else 0
    actionable = bool(coverage >= 70 and deps['pool'] and deps['breadth'])

    available_scores = [signal['score'] for signal in signals if signal['avail']]
    if len(available_scores) >= 2:
        mean_score = sum(available_scores) / len(available_scores)
        dispersion = math.sqrt(sum((value - mean_score) ** 2 for value in available_scores) / len(available_scores))
        consensus = round(clamp(100 - dispersion * 4.0))
    else:
        consensus = 0

    dynamics = _dynamics(temp, history, raw.get('date'))
    severe_reversal = dt_equiv > 25 or zb_rate > 0.45 or (lb_pct is not None and lb_pct < -3)
    phase_data, pending_confirmation = _stabilized_phase(temp, history, raw.get('date'), severe_reversal)
    phase_idx, phase, color, phase_desc = phase_data
    candidate = phase_of(temp)
    if pending_confirmation:
        phase_desc += ' 当前温度已触及%s，等待下一次有效快照确认。' % candidate[1]
    if coverage < 50 or not deps['pool']:
        phase = '数据不足'
        phase_desc = '核心数据覆盖不足，当前不输出周期和仓位结论。'

    score_map = {signal['key']: signal['score'] for signal in signals if signal['avail']}
    dimensions = _dimensions(signals)
    transition = _transition_estimate(temp, dynamics, score_map, zb_rate, dt_equiv)

    divergences = []
    if score_map.get('zt', 0) > 0 and score_map.get('zt_idx', 0) < 0:
        divergences.append('涨停数量偏强，但昨日涨停溢价为负：数量与赚钱效应背离')
    if temp >= 60 and score_map.get('breadth', 0) < 0:
        divergences.append('温度处于高位，但市场广度偏弱：局部抱团特征明显')
    if temp >= 60 and score_map.get('zb', 0) < 0:
        divergences.append('温度处于高位，但封板质量走弱：高位分歧正在累积')

    flags = []
    if deps['pool']:
        if zb_rate > 0.40 and zt > 50:
            flags.append({'type': 'warn', 'text': '分歧日：涨停仍多但炸板率超过40%，关注次日负反馈'})
        if zb_rate > 0.45:
            flags.append({'type': 'warn', 'text': '炸板潮：封板质量急剧恶化，接力风险显著上升'})
        if dt_equiv >= 15:
            flags.append({'type': 'warn', 'text': '跌停扩散：参考规模折算跌停 %.0f 家，亏钱效应蔓延' % dt_equiv})
        if height <= 1 and zt > 0:
            flags.append({'type': 'info', 'text': '梯队断层：无连板，当前涨停以首板试错为主'})
    if deps['bk'] and lb_pct is not None and lb_pct < -3.0:
        flags.append({'type': 'warn', 'text': '高位负反馈：昨日连板指数 %+.2f%%' % lb_pct})
    if temp >= 80:
        flags.append({'type': 'risk', 'text': '情绪过热：温度 %d°，拥挤和状态反转风险上升' % round(temp)})
    if deps['kline'] and vol_ratio > 1.3 and trend_pct > 0:
        flags.append({'type': 'info', 'text': '放量上行：同刻量能与趋势形成正向共振'})
    if pending_confirmation:
        flags.append({'type': 'info', 'text': '阶段切换待确认：使用两次有效快照滞回，减少阈值附近反复跳变'})
    for divergence in divergences:
        flags.append({'type': 'warn', 'text': divergence})
    if missing:
        flags.append({'type': 'info', 'text': '数据覆盖率%d%%，暂缺：%s' % (coverage, '、'.join(missing[:4]))})
    if not actionable:
        flags.append({'type': 'risk', 'text': '可信度门控：核心数据或覆盖率不足，暂停仓位建议'})

    position, position_pct, style = POSITIONS.get(phase, ('--', 0, '暂停策略建议'))
    if not actionable:
        position, position_pct, style = '--', 0, '数据不足 · 暂停策略建议'

    scenarios = [
        {'key': 'expansion', 'name': '扩张延续',
         'active': dynamics['direction'] == '升温' and score_map.get('breadth', 0) >= 0 and score_map.get('zt_idx', 0) >= 0,
         'condition': '温度升高、广度不弱、涨停溢价非负', 'action': '仅在主线持续获得反馈时维持进攻观察'},
        {'key': 'divergence', 'name': '高位分歧',
         'active': temp >= 60 and (dynamics['direction'] == '降温' or zb_rate > 0.35 or bool(divergences)),
         'condition': '高温叠加降温、炸板或结构背离', 'action': '降低高位接力暴露，优先验证负反馈是否扩散'},
        {'key': 'repair', 'name': '冰点修复',
         'active': temp < 40 and dynamics['direction'] == '升温' and dt_equiv < 15,
         'condition': '低温开始回升且跌停未继续扩散', 'action': '把修复视为候选状态，等待溢价与梯队二次确认'},
    ]

    advice = {
        'temp': round(temp), 'phase': phase, 'phase_idx': phase_idx, 'color': color,
        'phase_desc': phase_desc, 'position': position, 'position_pct': position_pct,
        'style': style, 'plan': phase_desc, 'actionable': actionable,
        'zhuXian': '以涨停梯队、题材持续性和次日溢价共同确认主线，不凭单一热度追逐',
        'scenarios': scenarios,
    }

    if phase == '数据不足':
        narrative = '核心数据覆盖不足，情绪温度与周期结论暂不可信；请查看数据源状态。'
    else:
        delta_text = '' if dynamics['delta1'] is None else '，较上一快照%+.1f°' % dynamics['delta1']
        narrative = '今日情绪温度%d°，处于%s%s；覆盖率%d%%，数据可信度%d%%。' % (
            round(temp), phase, delta_text, coverage, confidence)
        if deps['pool']:
            narrative += '涨停%d家、跌停%d家、炸板率%.0f%%；最高%d连板、连板%d家。' % (
                zt, dt, zb_rate * 100, height, lb_count)
        if deps['bk']:
            narrative += '昨日涨停指数%+.2f%%，昨日连板指数%+.2f%%。' % (zt_pct or 0, lb_pct or 0)
        if deps['breadth']:
            narrative += '市场宽度%d:%d（涨:跌）。' % (up, down)
        if deps['kline']:
            if turnover:
                narrative += '两市成交%.0f亿，%s量能%.2f×，上证相对MA20%+.1f%%。' % (
                    turnover / 1e8, volume_basis, vol_ratio, trend_pct)
            else:
                narrative += '%s量能%.2f×，上证相对MA20%+.1f%%。' % (volume_basis, vol_ratio, trend_pct)
        narrative += '研究仓位区间：%s；%s。' % (position, style)

    risks = [flag['text'] for flag in flags if flag['type'] in ('warn', 'risk')]
    if not risks:
        risks = ['当前未触发显著风险规则；仍需跟踪炸板率、溢价和市场广度变化。']

    return {
        'model_version': MODEL_VERSION, 'date': raw.get('date') or '',
        'temp': round(temp), 'temp_exact': round(temp, 2), 'phase': phase,
        'phase_candidate': candidate[1], 'phase_pending': pending_confirmation,
        'phase_idx': phase_idx, 'color': color, 'phase_desc': phase_desc,
        'coverage': coverage, 'confidence': confidence, 'source_quality': source_quality,
        'consensus': consensus, 'actionable': actionable, 'dimensions': dimensions,
        'dynamics': dynamics, 'transition': transition, 'divergences': divergences,
        'signals': signals, 'flags': flags, 'advice': advice, 'narrative': narrative,
        'risks': risks, 'degraded': bool(missing), 'missing': missing,
        'raw': {
            'zt': zt, 'dt': dt, 'zb': zb, 'zb_rate': round(zb_rate, 3),
            'zt_equiv': round(zt_equiv, 1), 'dt_equiv': round(dt_equiv, 1),
            'universe': universe or None, 'universe_scale': round(scale, 4),
            'height': height, 'lb_count': lb_count,
            'zt_idx_pct': round(zt_pct, 2) if zt_pct is not None else None,
            'lb_idx_pct': round(lb_pct, 2) if lb_pct is not None else None,
            'zt_idx_price': zt_idx_price, 'lb_idx_price': lb_idx_price,
            'up': up, 'down': down, 'flat': flat, 'up_ratio': round(up_ratio, 3),
            'turnover_yi': round(turnover / 1e8, 0) if turnover else None,
            'vol_ratio': round(vol_ratio, 3), 'volume_fraction': round(volume_fraction, 3),
            'volume_basis': volume_basis, 'flow_yi': round(flow_yi, 1),
            'trend_pct': round(trend_pct, 2), 'ma20': round(ma20, 2), 'close': last_close,
        },
    }
