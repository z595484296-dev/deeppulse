"""Build a traceable, user-selectable AKShare research snapshot.

AKShare is an adapter over multiple upstream websites.  The snapshot keeps the
final upstream, interface, observation date and freshness for every metric.
It deliberately stays outside the emotion score and every trading trigger.
"""

from calendar import monthrange
from copy import deepcopy
from datetime import date, datetime, timedelta
import math
import re


MODEL_VERSION = 'akshare-research-v2'
PACK_CATALOG = (
    {
        'id': 'growth', 'label': '增长景气',
        'description': '制造业、非制造业与 GDP 背景',
        'interfaces': ['macro_china_pmi', 'macro_china_gdp'], 'defaultEnabled': True,
    },
    {
        'id': 'prices', 'label': '价格环境',
        'description': 'CPI 与 PPI 月度价格变化',
        'interfaces': ['macro_china_cpi', 'macro_china_ppi'], 'defaultEnabled': True,
    },
    {
        'id': 'liquidity', 'label': '流动性',
        'description': 'M1、M2 与银行间资金价格',
        'interfaces': ['macro_china_money_supply', 'macro_china_shibor_all'], 'defaultEnabled': True,
    },
    {
        'id': 'rates', 'label': '利率环境',
        'description': 'LPR、中美长端利率与利差',
        'interfaces': ['macro_china_lpr', 'bond_zh_us_rate'], 'defaultEnabled': True,
    },
    {
        'id': 'reserves', 'label': '外储与黄金',
        'description': '外汇储备与央行黄金储备背景',
        'interfaces': ['macro_china_foreign_exchange_gold'], 'defaultEnabled': False,
    },
)
DEFAULT_PACK_IDS = tuple(row['id'] for row in PACK_CATALOG if row['defaultEnabled'])
PACK_IDS = tuple(row['id'] for row in PACK_CATALOG)


def pack_catalog():
    return deepcopy(list(PACK_CATALOG))


def normalize_pack_ids(value=None):
    if value is None:
        return list(DEFAULT_PACK_IDS)
    rows = value if isinstance(value, (list, tuple, set)) else []
    selected = []
    for pack_id in PACK_IDS:
        if pack_id in rows and pack_id not in selected:
            selected.append(pack_id)
    return selected or list(DEFAULT_PACK_IDS)


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
    text = str(value).strip()
    for fmt in ('%Y-%m-%d', '%Y/%m/%d', '%Y.%m.%d'):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            pass
    match = re.match(r'^(\d{4})年(\d{1,2})月份?$', text)
    if not match:
        match = re.match(r'^(\d{4})[.-](\d{1,2})$', text)
    if match:
        year, month = int(match.group(1)), int(match.group(2))
        if 1 <= month <= 12:
            return date(year, month, monthrange(year, month)[1])
    quarter = re.match(r'^(\d{4})年第(?:\d-)?(\d)季度$', text)
    if quarter:
        year, number = int(quarter.group(1)), int(quarter.group(2))
        if 1 <= number <= 4:
            month = number * 3
            return date(year, month, monthrange(year, month)[1])
    return None


def _latest(rows, date_keys, value_keys, today):
    candidates = []
    for row in _records(rows):
        observed = None
        for key in date_keys:
            observed = _day(row.get(key))
            if observed:
                break
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
            interface, upstream, upstream_url, independent_group, frequency,
            note='', reference=''):
    if observed is None or value is None:
        status, age = 'unavailable', None
    else:
        age = max(0, (today - observed).days)
        status = 'current' if age <= max_age_days else 'stale'
    return {
        'id': metric_id, 'label': label, 'value': value, 'unit': unit,
        'asOf': observed.isoformat() if observed else None,
        'stalenessDays': age, 'maxAgeDays': max_age_days, 'status': status,
        'frequency': frequency, 'note': note, 'reference': reference,
        'source': {
            'provider': 'AKShare', 'interface': interface, 'upstream': upstream,
            'upstreamUrl': upstream_url, 'tier': 'enrichment',
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


def build_snapshot(fetcher, version='', now=None, selected_packs=None):
    """Fetch only user-selected research packs and isolate every interface failure."""
    current = now or datetime.now()
    today = current.date()
    selection = normalize_pack_ids(selected_packs)
    errors = []
    datasets = {}

    def fetch(name, **kwargs):
        if name in datasets:
            return datasets[name]
        try:
            datasets[name] = fetcher(name, **kwargs)
        except Exception as exc:
            errors.append({'interface': name, 'error': str(exc)[:180]})
            datasets[name] = []
        return datasets[name]

    modules = []
    if 'growth' in selection:
        pmi_day, pmi = _latest(fetch('macro_china_pmi'), ('月份', '日期', 'date'), {
            'manufacturing': '制造业-指数', 'nonManufacturing': '非制造业-指数'}, today)
        gdp_day, gdp = _latest(fetch('macro_china_gdp'), ('季度', '日期', 'date'), {
            'value': '国内生产总值-同比增长'}, today)
        modules.append(_module('growth', '增长景气', [
            _metric('china-pmi-manufacturing', '制造业 PMI', pmi.get('manufacturing'), '点',
                    pmi_day, today, 50, 'macro_china_pmi', '东方财富',
                    'https://data.eastmoney.com/cjsj/pmi.html', 'eastmoney', 'monthly',
                    reference='50 为扩张/收缩分界参考。'),
            _metric('china-pmi-non-manufacturing', '非制造业 PMI', pmi.get('nonManufacturing'), '点',
                    pmi_day, today, 50, 'macro_china_pmi', '东方财富',
                    'https://data.eastmoney.com/cjsj/pmi.html', 'eastmoney', 'monthly'),
            _metric('china-gdp-yoy', 'GDP 同比', gdp.get('value'), '%', gdp_day, today, 130,
                    'macro_china_gdp', '东方财富', 'https://data.eastmoney.com/cjsj/gdp.html',
                    'eastmoney', 'quarterly'),
        ], '观察增长与服务业背景，不推导市场方向。'))

    if 'prices' in selection:
        cpi_day, cpi = _latest(fetch('macro_china_cpi'), ('月份', '日期', 'date'), {
            'value': '全国-同比增长'}, today)
        ppi_day, ppi = _latest(fetch('macro_china_ppi'), ('月份', '日期', 'date'), {
            'value': '当月同比增长'}, today)
        modules.append(_module('prices', '价格环境', [
            _metric('china-cpi-yoy', 'CPI 同比', cpi.get('value'), '%', cpi_day, today, 50,
                    'macro_china_cpi', '东方财富', 'https://data.eastmoney.com/cjsj/cpi.html',
                    'eastmoney', 'monthly'),
            _metric('china-ppi-yoy', 'PPI 同比', ppi.get('value'), '%', ppi_day, today, 50,
                    'macro_china_ppi', '东方财富', 'https://data.eastmoney.com/cjsj/ppi.html',
                    'eastmoney', 'monthly'),
        ], '观察通胀与工业品价格背景，不替代官方统计发布。'))

    if 'liquidity' in selection:
        money_day, money = _latest(fetch('macro_china_money_supply'), ('月份', '日期', 'date'), {
            'm1': '货币(M1)-同比增长', 'm2': '货币和准货币(M2)-同比增长'}, today)
        shibor_day, shibor = _latest(fetch('macro_china_shibor_all'), ('日期', 'date'), {
            'overnight': 'O/N-定价', 'oneWeek': '1W-定价'}, today)
        modules.append(_module('liquidity', '流动性', [
            _metric('china-m1-yoy', 'M1 同比', money.get('m1'), '%', money_day, today, 50,
                    'macro_china_money_supply', '东方财富',
                    'https://data.eastmoney.com/cjsj/hbgyl.html', 'eastmoney', 'monthly'),
            _metric('china-m2-yoy', 'M2 同比', money.get('m2'), '%', money_day, today, 50,
                    'macro_china_money_supply', '东方财富',
                    'https://data.eastmoney.com/cjsj/hbgyl.html', 'eastmoney', 'monthly'),
            _metric('shibor-overnight', 'SHIBOR 隔夜', shibor.get('overnight'), '%',
                    shibor_day, today, 7, 'macro_china_shibor_all', '金十数据',
                    'https://datacenter.jin10.com/reportType/dc_shibor', 'jin10', 'daily'),
            _metric('shibor-one-week', 'SHIBOR 1 周', shibor.get('oneWeek'), '%',
                    shibor_day, today, 7, 'macro_china_shibor_all', '金十数据',
                    'https://datacenter.jin10.com/reportType/dc_shibor', 'jin10', 'daily'),
        ], '观察货币供应与短端资金价格，不生成仓位建议。'))

    if 'rates' in selection:
        lpr_day, lpr = _latest(fetch('macro_china_lpr'), ('TRADE_DATE', '日期'), {
            'oneYear': 'LPR1Y', 'fiveYear': 'LPR5Y'}, today)
        bond_day, bond = _latest(fetch('bond_zh_us_rate', start_date=(today - timedelta(days=120)).strftime('%Y%m%d')),
                                 ('日期', 'date'), {
                                     'china10Y': '中国国债收益率10年',
                                     'us10Y': '美国国债收益率10年'}, today)
        rate_metrics = [
            _metric('china-lpr-1y', 'LPR 1 年期', lpr.get('oneYear'), '%', lpr_day, today, 45,
                    'macro_china_lpr', '东方财富',
                    'https://data.eastmoney.com/cjsj/globalRateLPR.html', 'eastmoney', 'monthly'),
            _metric('china-lpr-5y', 'LPR 5 年期以上', lpr.get('fiveYear'), '%', lpr_day, today, 45,
                    'macro_china_lpr', '东方财富',
                    'https://data.eastmoney.com/cjsj/globalRateLPR.html', 'eastmoney', 'monthly'),
            _metric('china-gov-10y', '中国 10 年国债收益率', bond.get('china10Y'), '%',
                    bond_day, today, 7, 'bond_zh_us_rate', '东方财富',
                    'https://data.eastmoney.com/cjsj/zmgzsyl.html', 'eastmoney', 'daily'),
            _metric('us-gov-10y', '美国 10 年国债收益率', bond.get('us10Y'), '%',
                    bond_day, today, 7, 'bond_zh_us_rate', '东方财富',
                    'https://data.eastmoney.com/cjsj/zmgzsyl.html', 'eastmoney', 'daily'),
        ]
        if bond.get('china10Y') is not None and bond.get('us10Y') is not None:
            rate_metrics.append(_metric(
                'us-cn-10y-spread', '美中 10 年国债利差',
                round(bond['us10Y'] - bond['china10Y'], 4), '个百分点', bond_day, today, 7,
                'bond_zh_us_rate', '东方财富',
                'https://data.eastmoney.com/cjsj/zmgzsyl.html', 'eastmoney', 'daily',
                note='由同一行美债 10Y 减中国国债 10Y 计算。'))
        modules.append(_module('rates', '利率环境', rate_metrics,
                               '观察资金价格与跨市场长端利率，不生成买卖信号。'))

    if 'reserves' in selection:
        reserve_day, reserve = _latest(fetch('macro_china_foreign_exchange_gold'),
                                       ('统计时间', '日期', 'date'), {
                                           'gold': '黄金储备', 'fx': '国家外汇储备'}, today)
        modules.append(_module('reserves', '外储与黄金', [
            _metric('china-fx-reserves', '国家外汇储备', reserve.get('fx'), '亿美元',
                    reserve_day, today, 50, 'macro_china_foreign_exchange_gold', '新浪财经',
                    'https://finance.sina.com.cn/mac/', 'sina', 'monthly'),
            _metric('china-gold-reserves', '央行黄金储备', reserve.get('gold'), '万盎司',
                    reserve_day, today, 50, 'macro_china_foreign_exchange_gold', '新浪财经',
                    'https://finance.sina.com.cn/mac/', 'sina', 'monthly'),
        ], '观察储备资产背景，不据此推断短期行情。'))

    metrics = [metric for module in modules for metric in module['metrics']]
    counts = {state: sum(row['status'] == state for row in metrics)
              for state in ('current', 'stale', 'unavailable')}
    groups = sorted({row['source']['independentGroup'] for row in metrics
                     if row.get('source', {}).get('independentGroup')})
    state = 'ok' if counts['current'] and not errors and not counts['stale'] and not counts['unavailable'] else (
        'degraded' if counts['current'] or counts['stale'] else 'unavailable')
    requested = [name for row in PACK_CATALOG if row['id'] in selection for name in row['interfaces']]
    return {
        'modelVersion': MODEL_VERSION,
        'provider': {'name': 'AKShare', 'version': str(version or ''), 'tier': 'enrichment'},
        'generatedAt': current.isoformat(timespec='seconds'), 'status': state,
        'selection': selection, 'catalog': pack_catalog(), 'interfacesRequested': requested,
        'summary': {'metrics': len(metrics), **counts, 'sourceGroups': len(groups)},
        'sourceGroups': groups, 'modules': modules, 'errors': errors,
        'marketBreadth': {
            'status': 'kept-on-primary-chain',
            'statement': '市场宽度继续使用深脉行情主链与通达信只读复核；不重复包装为 AKShare 独立来源。',
        },
        'lineagePolicy': 'AKShare 只是适配器；相同 independentGroup 只算一个最终上游。',
        'boundary': '研究增强数据不参与情绪温度、仓位区间、提醒或交易触发，不构成投资建议。',
        'includedInEmotionScore': False, 'automaticTradingAction': False,
    }


def unloaded_snapshot(installed=False, version='', selected_packs=None):
    selection = normalize_pack_ids(selected_packs)
    return {
        'modelVersion': MODEL_VERSION,
        'provider': {'name': 'AKShare', 'version': str(version or ''), 'tier': 'enrichment'},
        'generatedAt': None, 'status': 'not_loaded' if installed else 'not_installed',
        'selection': selection, 'catalog': pack_catalog(), 'interfacesRequested': [],
        'summary': {'metrics': 0, 'current': 0, 'stale': 0, 'unavailable': 0, 'sourceGroups': 0},
        'sourceGroups': [], 'modules': [], 'errors': [], 'interfaceHealth': [],
        'marketBreadth': {'status': 'kept-on-primary-chain'},
        'lineagePolicy': 'AKShare 只是适配器；相同 independentGroup 只算一个最终上游。',
        'boundary': '点击后按所选数据包读取，不自动进入情绪评分、提醒或交易流程。',
        'includedInEmotionScore': False, 'automaticTradingAction': False,
    }
