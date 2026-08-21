"""Build a traceable AKShare research-enrichment snapshot.

The snapshot is deliberately kept outside the emotion score.  AKShare wraps
multiple upstream websites, so two adapters that ultimately read the same
website must not be presented as independent corroboration.
"""

from datetime import date, datetime, timedelta
import math


MODEL_VERSION = 'akshare-research-v1'


def _records(value):
    if value is None:
        return []
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    if hasattr(value, 'to_dict'):
        try:
            return [row for row in value.to_dict(orient='records') if isinstance(row, dict)]
        except TypeError:
            return []
    return []


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _day(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if hasattr(value, 'to_pydatetime'):
        try:
            return value.to_pydatetime().date()
        except Exception:
            return None
    text = str(value).strip()[:10]
    try:
        return datetime.strptime(text, '%Y-%m-%d').date()
    except ValueError:
        return None


def _latest(rows, date_keys, value_keys, today):
    candidates = []
    for row in _records(rows):
        observed = next((_day(row.get(key)) for key in date_keys if _day(row.get(key))), None)
        if observed is None or observed > today:
            continue
        values = {key: _number(row.get(column)) for key, column in value_keys.items()}
        if not any(value is not None for value in values.values()):
            continue
        candidates.append((observed, values))
    if not candidates:
        return None, {}
    candidates.sort(key=lambda item: item[0])
    return candidates[-1]


def _metric(metric_id, label, value, unit, observed, today, max_age_days,
            upstream, upstream_url, independent_group, note='', reference=''):
    if observed is None or value is None:
        status, age = 'unavailable', None
    else:
        age = max(0, (today - observed).days)
        status = 'current' if age <= max_age_days else 'stale'
    return {
        'id': metric_id,
        'label': label,
        'value': value,
        'unit': unit,
        'asOf': observed.isoformat() if observed else None,
        'stalenessDays': age,
        'maxAgeDays': max_age_days,
        'status': status,
        'note': note,
        'reference': reference,
        'source': {
            'provider': 'AKShare',
            'upstream': upstream,
            'upstreamUrl': upstream_url,
            'tier': 'enrichment',
            'independentGroup': independent_group,
        },
        'includedInEmotionScore': False,
    }


def _module(module_id, label, metrics, purpose):
    states = [row['status'] for row in metrics]
    if states and all(state == 'current' for state in states):
        state = 'current'
    elif 'current' in states:
        state = 'partial'
    elif 'stale' in states:
        state = 'stale'
    else:
        state = 'unavailable'
    return {'id': module_id, 'label': label, 'purpose': purpose, 'status': state, 'metrics': metrics}


def build_snapshot(fetcher, version='', now=None):
    """Fetch selected research data through ``fetcher(name, **kwargs)``.

    Failures are isolated per interface.  The caller can record latency and
    source-health observations without coupling this pure normalization layer
    to the server runtime.
    """
    current = now or datetime.now()
    today = current.date()
    errors = []

    def fetch(name, **kwargs):
        try:
            return fetcher(name, **kwargs)
        except Exception as exc:
            errors.append({'interface': name, 'error': str(exc)[:180]})
            return []

    pmi_rows = fetch('macro_china_pmi_yearly')
    cpi_rows = fetch('macro_china_cpi_yearly')
    ppi_rows = fetch('macro_china_ppi_yearly')
    lpr_rows = fetch('macro_china_lpr')
    bond_rows = fetch('bond_zh_us_rate', start_date=(today - timedelta(days=120)).strftime('%Y%m%d'))

    pmi_day, pmi = _latest(pmi_rows, ('日期', 'date'), {'value': '今值'}, today)
    cpi_day, cpi = _latest(cpi_rows, ('日期', 'date'), {'value': '今值'}, today)
    ppi_day, ppi = _latest(ppi_rows, ('日期', 'date'), {'value': '今值'}, today)
    lpr_day, lpr = _latest(lpr_rows, ('TRADE_DATE', '日期'),
                           {'oneYear': 'LPR1Y', 'fiveYear': 'LPR5Y'}, today)
    bond_day, bond = _latest(bond_rows, ('日期', 'date'), {
        'china10Y': '中国国债收益率10年', 'us10Y': '美国国债收益率10年',
        'chinaCurve': '中国国债收益率10年-2年', 'usCurve': '美国国债收益率10年-2年',
    }, today)

    growth = _metric(
        'china-pmi', '中国制造业 PMI', pmi.get('value'), '点', pmi_day, today, 50,
        '金十数据', 'https://datacenter.jin10.com/reportType/dc_chinese_manufacturing_pmi',
        'jin10', note='月度景气观察；超过 50 天即标记陈旧。', reference='50 为扩张/收缩分界参考。')
    prices = [
        _metric('china-cpi-yoy', '中国 CPI 同比', cpi.get('value'), '%', cpi_day, today, 50,
                '金十数据', 'https://datacenter.jin10.com/reportType/dc_chinese_cpi_yoy', 'jin10',
                note='月度价格观察；不作为实时市场事实。'),
        _metric('china-ppi-yoy', '中国 PPI 同比', ppi.get('value'), '%', ppi_day, today, 50,
                '金十数据', 'https://datacenter.jin10.com/reportType/dc_chinese_ppi_yoy', 'jin10',
                note='月度价格观察；不作为实时市场事实。'),
    ]
    rates = [
        _metric('china-lpr-1y', 'LPR 1 年期', lpr.get('oneYear'), '%', lpr_day, today, 45,
                '东方财富', 'https://data.eastmoney.com/cjsj/globalRateLPR.html', 'eastmoney',
                note='AKShare 的上游仍是东方财富，不算独立于东方财富的互证。'),
        _metric('china-lpr-5y', 'LPR 5 年期以上', lpr.get('fiveYear'), '%', lpr_day, today, 45,
                '东方财富', 'https://data.eastmoney.com/cjsj/globalRateLPR.html', 'eastmoney',
                note='AKShare 的上游仍是东方财富，不算独立于东方财富的互证。'),
        _metric('china-gov-10y', '中国 10 年国债收益率', bond.get('china10Y'), '%', bond_day, today, 7,
                '东方财富', 'https://data.eastmoney.com/cjsj/zmgzsyl.html', 'eastmoney'),
        _metric('us-gov-10y', '美国 10 年国债收益率', bond.get('us10Y'), '%', bond_day, today, 7,
                '东方财富', 'https://data.eastmoney.com/cjsj/zmgzsyl.html', 'eastmoney'),
    ]
    if bond.get('china10Y') is not None and bond.get('us10Y') is not None:
        rates.append(_metric(
            'us-cn-10y-spread', '美中 10 年国债利差',
            round(bond['us10Y'] - bond['china10Y'], 4), '个百分点', bond_day, today, 7,
            '东方财富', 'https://data.eastmoney.com/cjsj/zmgzsyl.html', 'eastmoney',
            note='由同一行美债 10Y 减中国国债 10Y 计算。'))

    modules = [
        _module('growth', '增长景气', [growth], '为市场风格研究提供宏观背景，不推导交易方向。'),
        _module('prices', '价格环境', prices, '观察通胀与工业品价格背景，不替代官方统计发布。'),
        _module('rates', '利率环境', rates, '观察资金价格与跨市场利率背景，不生成买卖信号。'),
    ]
    metrics = [metric for module in modules for metric in module['metrics']]
    counts = {state: sum(row['status'] == state for row in metrics)
              for state in ('current', 'stale', 'unavailable')}
    state = 'ok' if counts['current'] and not errors and not counts['stale'] and not counts['unavailable'] else (
        'degraded' if counts['current'] or counts['stale'] else 'unavailable')
    return {
        'modelVersion': MODEL_VERSION,
        'provider': {'name': 'AKShare', 'version': str(version or ''), 'tier': 'enrichment'},
        'generatedAt': current.isoformat(timespec='seconds'),
        'status': state,
        'summary': {'metrics': len(metrics), **counts},
        'modules': modules,
        'errors': errors,
        'marketBreadth': {
            'status': 'kept-on-primary-chain',
            'statement': '市场宽度继续使用深脉现有东方财富主链与通达信只读复核；不重复包装为 AKShare 独立来源。',
        },
        'lineagePolicy': '保留最终上游；相同 independentGroup 不计为独立互证。',
        'boundary': '研究增强数据不参与情绪温度、仓位区间或提醒触发，不构成投资建议。',
        'includedInEmotionScore': False,
        'automaticTradingAction': False,
    }


def unloaded_snapshot(installed=False, version=''):
    return {
        'modelVersion': MODEL_VERSION,
        'provider': {'name': 'AKShare', 'version': str(version or ''), 'tier': 'enrichment'},
        'generatedAt': None,
        'status': 'not_loaded' if installed else 'not_installed',
        'summary': {'metrics': 0, 'current': 0, 'stale': 0, 'unavailable': 0},
        'modules': [], 'errors': [],
        'marketBreadth': {'status': 'kept-on-primary-chain'},
        'lineagePolicy': '保留最终上游；相同 independentGroup 不计为独立互证。',
        'boundary': '点击后按需读取，不自动进入情绪评分或交易流程。',
        'includedInEmotionScore': False,
        'automaticTradingAction': False,
    }
