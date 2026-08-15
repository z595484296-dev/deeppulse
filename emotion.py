# -*- coding: utf-8 -*-
"""
深脉 DeepPulse · 情绪周期引擎 — 「我的心脏」
====================================================
把 A 股超短情绪周期的经典观察指标（涨停/跌停/炸板/连板/溢价/宽度/量能/资金/趋势）
量化为 0-100 的情绪温度，并映射到五阶段周期：
  冰点期 → 修复期 → 发酵期 → 高潮期 → 亢奋期(风险区)
再结合「退潮预警 / 分歧日」等动态信号，输出仓位与打法建议。

模型说明：温度 = 50 + Σ(指标得分 × 权重) / Σ权重，clamp 到 0-100。
每个指标的分档阈值基于公开的超短复盘方法论设定，详见《情绪周期方法论.md》。

权重可调教：data/weights.json 中的自定义权重会覆盖默认值（UI 或 API 修改后
下一次评分立即生效）。这是你的身体——按你的盘感调教。
"""

import json
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WEIGHTS_FILE = os.path.join(BASE_DIR, 'data', 'weights.json')


def clamp(v, lo=0.0, hi=100.0):
    return max(lo, min(hi, v))


def step(v, bands):
    """bands: [(下界, 得分)] 按阈值从高到低排列，v >= 下界 取对应得分"""
    for th, sc in bands:
        if v >= th:
            return sc
    return bands[-1][1]


# ---------------------------------------------------------------- 各指标打分
# 得分区间约 [-20, +20]，权重体现该指标在周期判断中的重要程度。

def score_zt(v):
    """涨停家数：情绪的量度。全A约 5300 家，>100 家为过热，<20 家为冰点。"""
    return step(v, [(130, 100), (100, 90), (80, 80), (60, 65), (40, 45), (20, 25), (0, 10)])


def score_dt(v):
    """跌停家数：亏钱效应的量度。"""
    return step(v, [(0, 15), (3, 10), (8, 0), (15, -10), (25, -20)])


def score_zb(rate):
    """炸板率 = 炸板 / (涨停 + 炸板)：封板质量。低炸板率说明共识强。"""
    return step(rate, [(0.0, 10), (0.15, 5), (0.25, 0), (0.35, -10), (0.45, -20)])


def score_height(h):
    """最高连板高度：空间高度决定赚钱效应的想象力。"""
    return step(h, [(6, 15), (5, 12), (4, 8), (3, 4), (2, 0), (1, -5), (0, -10)])


def score_lb_count(n):
    """连板家数：梯队的厚度，判断是孤军还是集团作战。"""
    return step(n, [(12, 10), (8, 6), (5, 3), (3, 0), (1, -3), (0, -8)])


def score_zt_idx(pct):
    """昨日涨停指数涨跌：打板次日溢价，超短情绪最敏感的先行指标。"""
    return step(pct, [(3.0, 15), (1.0, 10), (0.0, 5), (-1.0, 0), (-3.0, -8), (-99, -15)])


def score_lb_idx(pct):
    """昨日连板指数涨跌：高位接力的盈亏反馈。"""
    return step(pct, [(3.0, 10), (1.0, 6), (0.0, 3), (-1.0, 0), (-3.0, -5), (-99, -10)])


def score_breadth(up_ratio):
    """上涨家数占比：市场宽度，情绪是普涨还是抱团。"""
    return step(up_ratio, [(0.75, 10), (0.6, 6), (0.45, 0), (0.3, -6), (0, -10)])


def score_volume(ratio):
    """两市成交额 / 20日均量：量能是情绪的燃料。"""
    return step(ratio, [(1.3, 8), (1.1, 4), (0.9, 0), (0.7, -4), (0, -8)])


def score_flow(yi):
    """两市主力资金净流入（亿元）：大资金的真实态度。"""
    return step(yi, [(200, 10), (100, 6), (0, 2), (-100, -2), (-200, -6), (-99999, -10)])


def score_trend(pct_vs_ma20):
    """上证指数相对 MA20 的偏离：指数趋势背景（权重最低，情绪为主）。"""
    return step(pct_vs_ma20, [(3.0, 5), (0.0, 2), (-3.0, -2), (-99, -5)])


# ---------------------------------------------------------------- 指标元信息
# (key, 名称, 权重, 打分函数, 数值格式化, 单位, 数据依赖)
# 数据依赖: pool=涨停/跌停/炸板池, bk=昨日涨停/连板指数, breadth=涨跌分布,
#           kline=上证K线(量能/趋势), flow=主力资金流
# 依赖的数据不可用（如上游限流）时，该指标自动剔除，不影响温度计算。

INDICATORS = [
    ('zt', '涨停家数', 1.0, score_zt, '%d', '家', 'pool'),
    ('dt', '跌停家数', 1.0, score_dt, '%d', '家', 'pool'),
    ('zb', '炸板率', 1.0, score_zb, '%.0f', '%%', 'pool'),
    ('height', '最高连板', 0.8, score_height, '%d', '板', 'pool'),
    ('lb_count', '连板家数', 0.8, score_lb_count, '%d', '家', 'pool'),
    ('zt_idx', '昨日涨停指数', 1.0, score_zt_idx, '%+.2f', '%%', 'bk'),
    ('lb_idx', '昨日连板指数', 0.8, score_lb_idx, '%+.2f', '%%', 'bk'),
    ('breadth', '上涨家数占比', 0.8, score_breadth, '%.0f', '%%', 'breadth'),
    ('volume', '量能比(20日)', 0.6, score_volume, '%.2f', '×', 'kline'),
    ('flow', '主力净流入', 0.6, score_flow, '%+.0f', '亿', 'flow'),
    ('trend', '上证vs MA20', 0.4, score_trend, '%+.1f', '%%', 'kline'),
]

# ---- 权重调教（UI / API 可改，下一次评分即生效） ----
DEFAULT_WEIGHTS = {t[0]: t[2] for t in INDICATORS}


def load_weights():
    """读取自定义权重；文件缺失/损坏时回退默认值。"""
    try:
        with open(WEIGHTS_FILE, encoding='utf-8') as f:
            j = json.load(f)
        return {k: float(j.get(k, DEFAULT_WEIGHTS[k])) for k in DEFAULT_WEIGHTS}
    except Exception:
        return dict(DEFAULT_WEIGHTS)


def save_weights(weights):
    """保存自定义权重（只接受已知键，clamp 到 0-3）。"""
    clean = {}
    for k in DEFAULT_WEIGHTS:
        if k in weights:
            try:
                v = float(weights[k])
                clean[k] = max(0.0, min(3.0, v))
            except (TypeError, ValueError):
                continue
    os.makedirs(os.path.dirname(WEIGHTS_FILE), exist_ok=True)
    with open(WEIGHTS_FILE, 'w', encoding='utf-8') as f:
        json.dump(clean, f, ensure_ascii=False, indent=1)
    return clean


PHASES = [    (20, '冰点期', 'blue', '亏钱效应极致，市场冰封。策略：空仓观望，等待回暖信号；跟踪逆势抗跌股与率先回封的首板。'),
    (40, '修复期', 'cyan', '情绪开始回暖，试错期。策略：轻仓低吸超跌核心，打首板试错，快进快出不格局。'),
    (60, '发酵期', 'amber', '主线发酵，赚钱效应扩散。策略：围绕主线题材做核心股低吸与弱转强接力，敢于上仓位。'),
    (80, '高潮期', 'red', '情绪高潮，普涨但逼近分歧。策略：持仓兑现、去弱留强，只保留主线核心，谨慎接力高位。'),
    (999, '亢奋期', 'violet', '情绪过热，盛极而衰的前夜。策略：减仓防守，回避高位接力，等待退潮出清后的新周期。'),
]

POSITIONS = {
    '冰点期': ('0-2成', 10, '空仓观察 · 等待回暖'),
    '修复期': ('2-4成', 30, '轻仓试错 · 低吸核心'),
    '发酵期': ('5-8成', 65, '主线进攻 · 强势接力'),
    '高潮期': ('5-7成', 55, '持仓兑现 · 只做核心'),
    '亢奋期': ('≤3成', 20, '防守减仓 · 谨防退潮'),
}


def fmt_val(key, v):
    if key in ('zt_idx', 'lb_idx', 'flow', 'trend', 'zb'):
        v = round(v, 2)
    return v


def note_for(key, v):
    notes = {
        'zt': (lambda v: '涨停 %d 家：' % v + (
            '情绪冰封' if v < 20 else '情绪低迷' if v < 40 else '情绪中性' if v < 60
            else '情绪活跃' if v < 80 else '情绪火爆' if v < 100 else '情绪过热，警惕盛极而衰')),
        'dt': (lambda v: '跌停 %d 家：' % v + (
            '亏钱效应极弱' if v <= 3 else '亏钱效应可控' if v <= 8 else '亏钱效应抬头' if v <= 15
            else '亏钱效应扩散，防守优先' if v <= 25 else '恐慌出清进行时，等跌停潮结束')),
        'zb': (lambda v: '炸板率 %.0f%%：' % (v * 100) + (
            '封板质量高，共识强' if v < 0.15 else '封板质量较好' if v < 0.25
            else '分歧显现' if v < 0.35 else '分歧加剧，接力需谨慎' if v < 0.45 else '炸板潮，情绪快速恶化')),
        'height': (lambda v: '最高 %d 连板：' % v + (
            '空间打开，赚钱效应强' if v >= 6 else '空间较高，主线有高度' if v >= 4
            else '空间中等' if v >= 3 else '空间受限，无高度龙头' if v >= 2 else '梯队断层，超短环境恶劣')),
        'lb_count': (lambda v: '连板 %d 家：' % v + (
            '梯队厚实，集团作战' if v >= 12 else '梯队完整' if v >= 8 else '梯队一般' if v >= 5
            else '梯队单薄' if v >= 3 else '连板稀缺，接力难做' if v >= 1 else '无连板，情绪断档')),
        'zt_idx': (lambda v: '昨日涨停指数 %+.2f%%：' % v + (
            '打板次日溢价丰厚，敢追高' if v > 3 else '溢价良好' if v > 1 else '溢价正常' if v > 0
            else '溢价转负，打板开始亏钱' if v > -3 else '打板大面，管住手')),
        'lb_idx': (lambda v: '昨日连板指数 %+.2f%%：' % v + (
            '高位接力赚钱' if v > 3 else '高位接力尚可' if v > 1 else '高位反馈一般' if v > 0
            else '高位开始亏钱，回避纯情绪接力' if v > -3 else '高位剧烈亏钱，退潮信号')),
        'breadth': (lambda v: '上涨占比 %.0f%%：' % (v * 100) + (
            '普涨格局' if v > 0.75 else '多数上涨' if v > 0.6 else '涨跌互现' if v > 0.45
            else '多数下跌，结构分化' if v > 0.3 else '普跌格局，情绪极弱')),
        'volume': (lambda v: '量能 %.2f×20日均量：' % v + (
            '放量明显，增量资金进场' if v > 1.3 else '温和放量' if v > 1.1 else '量能平稳' if v > 0.9
            else '缩量，存量博弈' if v > 0.7 else '地量，等待变盘')),
        'flow': (lambda v: '主力净流入 %+.0f 亿：' % v + (
            '大资金大幅进场' if v > 200 else '大资金净流入' if v > 100 else '小幅流入' if v > 0
            else '小幅流出' if v > -100 else '大资金撤退' if v > -200 else '主力大幅出逃')),
        'trend': (lambda v: '上证相对MA20 %+.1f%%：' % v + (
            '指数趋势强势，可积极' if v > 3 else '趋势向上' if v > 0
            else '趋势走弱，控制总仓位' if v > -3 else '趋势破位，防守为主')),
    }
    return notes[key](v)


def compute_emotion(raw):
    """raw: 由 server 装配的原始情绪数据；返回引擎结果 dict。
    上游部分数据缺失时自动剔除对应指标（优雅降级），并在 degraded 中标记。"""
    zt = raw.get('zt') or 0
    dt = raw.get('dt') or 0
    zb = raw.get('zb') or 0
    pool = raw.get('zt_pool') or []
    breadth = raw.get('breadth') or {}
    indices = raw.get('indices') or []
    flows = raw.get('flows') or {}
    bk = raw.get('bk') or {}
    sh_rows = raw.get('sh_kline') or []

    # ---- 数据可用性
    deps = {
        'pool': not raw.get('pool_error', False),
        'bk': raw.get('bk_ok', False),
        'breadth': bool(breadth.get('bins')),
        'kline': raw.get('kline_ok', False),
        'flow': raw.get('flow_ok', False),
    }

    # ---- 原始指标
    zt_pct = None
    lb_pct = None
    zt_idx_price = None
    lb_idx_price = None
    for key, dest in (('zt', 'zt_pct'), ('lb', 'lb_pct')):
        b = bk.get(key) or {}
        pct = b.get('pct')
        if pct is not None:
            if key == 'zt':
                zt_pct, zt_idx_price = pct, b.get('price')
            else:
                lb_pct, lb_idx_price = pct, b.get('price')

    heights = [it.get('lbc') or 0 for it in pool]
    height = max(heights) if heights else 0
    lb_count = len([h for h in heights if h >= 2])

    up, down = breadth.get('up') or 0, breadth.get('down') or 0
    up_ratio = up / (up + down) if (up + down) else 0.5

    sh_amount = (indices[0].get('amount') or 0) if indices else 0
    sz_amount = (indices[1].get('amount') or 0) if len(indices) > 1 else 0
    turnover = sh_amount + sz_amount
    # 量能基线用上证成交量（手）代理：东财/腾讯K线都有该字段，跨源稳定
    vols20 = [r.get('volume') or 0 for r in sh_rows[-21:-1]]
    avg20v = sum(vols20) / len(vols20) if vols20 else 0
    today_vol = (sh_rows[-1].get('volume') or 0) if sh_rows else 0
    vol_ratio = (today_vol / avg20v) if avg20v else 1.0

    def _sum_flow(rows):
        if not rows or not isinstance(rows, list):
            return 0.0
        last = rows[-1]
        return float(last.get('main') or 0)
    flow_sh = _sum_flow(flows.get('sh'))
    flow_sz = _sum_flow(flows.get('sz'))
    flow_yi = (flow_sh + flow_sz) / 1e8

    closes20 = [r.get('close') or 0 for r in sh_rows[-21:]]
    ma20 = sum(closes20) / len(closes20) if closes20 else 0
    last_close = closes20[-1] if closes20 else 0
    trend_pct = ((last_close / ma20) - 1) * 100 if ma20 else 0.0

    zb_rate = zb / (zt + zb) if (zt + zb) else 0.0

    vals = {
        'zt': zt, 'dt': dt, 'zb': zb_rate, 'height': height, 'lb_count': lb_count,
        'zt_idx': zt_pct if zt_pct is not None else 0.0,
        'lb_idx': lb_pct if lb_pct is not None else 0.0,
        'breadth': up_ratio, 'volume': vol_ratio, 'flow': flow_yi, 'trend': trend_pct,
    }

    # ---- 打分（只计算数据可用的指标；权重可用 data/weights.json 调教）
    weights = load_weights()
    total_w = 0.0
    acc = 0.0
    signals = []
    for key, name, w_default, fn, fm, unit, dep in INDICATORS:
        w = weights.get(key, w_default)
        if not deps.get(dep, False):
            signals.append({
                'key': key, 'name': name, 'value': None, 'display': '--',
                'unit': unit, 'score': None, 'weight': w, 'contribution': None,
                'note': '上游数据暂不可用，本指标未参与今日评分', 'avail': False,
            })
            continue
        v = vals[key]
        dv = v * 100 if key in ('zb', 'breadth') else v  # 这两个按百分数展示
        sc = float(fn(v))
        total_w += w
        acc += sc * w
        signals.append({
            'key': key, 'name': name, 'value': round(dv, 2) if key in ('zb', 'breadth') else fmt_val(key, v),
            'display': fm % dv, 'unit': unit,
            'score': round(sc, 1), 'weight': w,
            'contribution': round(sc * w, 1), 'note': note_for(key, v),
            'avail': True,
        })
    temp = clamp(50.0 + acc / total_w if total_w else 50.0)
    missing = [name for _, name, *_r, dep in INDICATORS if not deps.get(dep, False)]

    # ---- 阶段
    phase_idx, phase, color, phase_desc = 0, '冰点期', 'blue', ''
    for th, name, c, desc in PHASES:
        if temp < th:
            phase_idx, phase, color, phase_desc = PHASES.index((th, name, c, desc)), name, c, desc
            break
    if len(missing) >= 8:
        phase, phase_desc = '数据不足', '上游数据源暂时不可用，稍后自动重试。'

    # ---- 动态信号
    flags = []
    if deps['pool']:
        if zb_rate > 0.40 and zt > 50:
            flags.append({'type': 'warn', 'text': '分歧日：涨停仍多但炸板率超 40%，明日大概率分化，去弱留强'})
        if zb_rate > 0.45:
            flags.append({'type': 'warn', 'text': '炸板潮：封板质量急剧恶化，接力环境恶劣，管住手'})
        if dt >= 15:
            flags.append({'type': 'warn', 'text': '跌停扩散：跌停 %d 家，亏钱效应蔓延，防守优先' % dt})
        if height <= 1 and zt > 0:
            flags.append({'type': 'info', 'text': '梯队断层：无连板，涨停以首板为主，情绪处于试错期'})
    if deps['bk'] and lb_pct is not None and lb_pct < -3.0:
        flags.append({'type': 'warn', 'text': '高位大面：昨日连板指数 %+.2f%%，高位股亏钱效应剧烈，退潮进行时' % lb_pct})
    if temp >= 80:
        flags.append({'type': 'risk', 'text': '情绪过热：温度 %d°，历史规律盛极而衰，随时准备撤退' % round(temp)})
    if deps['kline'] and vol_ratio > 1.3 and trend_pct > 0:
        flags.append({'type': 'info', 'text': '放量上行：增量资金进场，行情有持续性基础'})
    if missing:
        flags.append({'type': 'info', 'text': '部分数据暂缺（%s），评分按可用指标折算，恢复后自动校正' % '、'.join(missing[:4])})

    pos, pos_pct, style = POSITIONS.get(phase, ('--', 0, '--'))

    advice = {
        'temp': round(temp), 'phase': phase, 'phase_idx': phase_idx, 'color': color,
        'phase_desc': phase_desc,
        'position': pos, 'position_pct': pos_pct, 'style': style,
        'plan': phase_desc,
        'zhuXian': '围绕主线题材（见涨停梯队·题材热度）做核心股的低吸与弱转强接力',
    }

    if len(missing) >= 8:
        narrative = ('当前上游数据源暂时不可用，情绪温度暂不可信，'
                     '请稍候自动重试或查看「数据源」页状态。')
    else:
        narrative = ('今日情绪温度 %d°，处于%s。' % (round(temp), phase))
        if deps['pool']:
            narrative += ('涨停 %d 家 / 跌停 %d 家，炸板率 %.0f%%；最高 %d 连板，连板 %d 家。'
                          % (zt, dt, zb_rate * 100, height, lb_count))
        if deps['bk']:
            narrative += ('昨日涨停指数 %+.2f%%，昨日连板指数 %+.2f%%。'
                          % (zt_pct or 0, lb_pct or 0))
        if deps['breadth']:
            narrative += ('市场宽度 %d:%d（涨:跌）。' % (up, down))
        if deps['kline']:
            narrative += ('上证成交 %.0f 亿（今日 %.2f×20日均量），上证相对 MA20 %+.1f%%。'
                          % (turnover / 1e8, vol_ratio, trend_pct)) if turnover else \
                         ('量能 %.2f×20日均量，上证相对 MA20 %+.1f%%。' % (vol_ratio, trend_pct))
        if deps['flow']:
            narrative += ('主力净流入 %+.0f 亿。' % flow_yi)
        narrative += ('操作建议：仓位 %s，%s。' % (pos, style))

    risks = [f['text'] for f in flags if f['type'] == 'warn']
    if not risks:
        risks = ['当前无明显风险信号，按计划执行，盘中跟踪炸板率与昨日涨停指数变化。']

    return {
        'date': raw.get('date') or '',
        'temp': round(temp), 'phase': phase, 'phase_idx': phase_idx, 'color': color,
        'phase_desc': phase_desc, 'signals': signals, 'flags': flags,
        'advice': advice, 'narrative': narrative, 'risks': risks,
        'degraded': bool(missing),
        'missing': missing,
        'raw': {
            'zt': zt, 'dt': dt, 'zb': zb, 'zb_rate': round(zb_rate, 3),
            'height': height, 'lb_count': lb_count,
            'zt_idx_pct': round(zt_pct, 2) if zt_pct is not None else None,
            'lb_idx_pct': round(lb_pct, 2) if lb_pct is not None else None,
            'zt_idx_price': zt_idx_price, 'lb_idx_price': lb_idx_price,
            'up': up, 'down': down, 'up_ratio': round(up_ratio, 3),
            'turnover_yi': round(turnover / 1e8, 0) if turnover else None,
            'vol_ratio': round(vol_ratio, 3),
            'flow_yi': round(flow_yi, 1),
            'trend_pct': round(trend_pct, 2),
            'ma20': round(ma20, 2), 'close': last_close,
        },
    }
