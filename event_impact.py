#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DeepPulse event impact model.

Pure, dependency-free normalization and rule matching.  Fetching and user
authorization remain in server.py so this module can be tested with fixtures.
All links produced here are sensitivity hypotheses, never causal claims.
"""

import hashlib
import math
import re
from datetime import datetime


MODEL_VERSION = 'event-impact-v1'


IMPACT_RULES = (
    {
        'id': 'ai-compute',
        'keywords': ('人工智能', 'ai', '算力', '数据中心', '服务器', '英伟达', '芯片', '半导体', '光模块', 'cpo'),
        'sectors': ('通信设备', '计算机设备', '半导体', '消费电子', '电子'),
        'reason': '算力、芯片与数据中心事件可能改变相关硬件和基础设施行业的预期。',
    },
    {
        'id': 'rates-credit',
        'keywords': ('利率', '降息', '加息', '央行', 'lpr', '货币供应', '社融', '信贷', '国债收益率'),
        'sectors': ('银行', '证券', '保险', '房地产'),
        'reason': '利率与信用条件通常是金融和高杠杆行业的重要敏感变量。',
    },
    {
        'id': 'inflation',
        'keywords': ('cpi', 'ppi', '通胀', '物价', '消费价格', '生产者价格'),
        'sectors': ('食品饮料', '商贸零售', '基础化工', '有色金属', '煤炭'),
        'reason': '价格指标可能影响原材料成本、消费能力与定价预期。',
    },
    {
        'id': 'manufacturing',
        'keywords': ('pmi', '制造业', '工业增加值', '工业生产', '固定资产投资', '设备更新'),
        'sectors': ('机械设备', '自动化设备', '专用设备', '工业金属', '通信设备'),
        'reason': '制造业景气和资本开支变化可能影响设备、工业品与企业订单预期。',
    },
    {
        'id': 'oil-gas',
        'keywords': ('原油', '石油', '油价', '天然气', '钻井', 'opec', '霍尔木兹'),
        'sectors': ('石油石化', '油气开采', '化学原料', '航运港口', '航空机场'),
        'reason': '能源供需与运输事件可能影响油气价格、化工成本及运输预期。',
    },
    {
        'id': 'metals-gold',
        'keywords': ('黄金', '白银', '铜价', '铝价', '金价', '贵金属', '伦敦金属'),
        'sectors': ('贵金属', '工业金属', '有色金属', '金属新材料'),
        'reason': '金属价格与库存变化可能影响资源品盈利和避险预期。',
    },
    {
        'id': 'currency-export',
        'keywords': ('汇率', '人民币', '美元指数', '美元兑', '外汇储备', '关税', '出口', '贸易'),
        'sectors': ('消费电子', '家用电器', '纺织服饰', '航运港口', '汽车零部件'),
        'reason': '汇率与贸易条件可能影响出口收入、进口成本和跨境需求。',
    },
    {
        'id': 'property',
        'keywords': ('房地产', '房价', '商品房', '住房', '按揭', '地产政策'),
        'sectors': ('房地产开发', '房地产服务', '装修建材', '家居用品', '水泥'),
        'reason': '房地产销售与融资变化可能传导至开发、建材和家居需求预期。',
    },
    {
        'id': 'auto-energy',
        'keywords': ('汽车', '新能源车', '电动车', '电池', '锂', '充电桩', '自动驾驶'),
        'sectors': ('汽车整车', '汽车零部件', '电池', '能源金属', '电网设备'),
        'reason': '汽车需求、技术和能源链变化可能影响整车、零部件与电池产业。',
    },
    {
        'id': 'power-grid',
        'keywords': ('电力', '电网', '发电', '用电量', '核电', '光伏', '风电', '储能'),
        'sectors': ('电力', '电网设备', '光伏设备', '风电设备', '其他电源设备'),
        'reason': '能源政策与电力供需变化可能影响发电、输配电和新能源设备预期。',
    },
    {
        'id': 'healthcare',
        'keywords': ('医药', '药品', '医疗', '医保', '创新药', '疫苗', '生物科技'),
        'sectors': ('化学制药', '生物制品', '医疗器械', '医疗服务', '中药'),
        'reason': '医疗政策、审批与需求事件可能影响药械和医疗服务预期。',
    },
    {
        'id': 'consumption',
        'keywords': ('消费', '零售', '社零', '白酒', '食品', '旅游', '免税'),
        'sectors': ('食品饮料', '白酒', '商贸零售', '旅游及景区', '酒店餐饮'),
        'reason': '消费数据与政策可能影响可选消费、必选消费和服务消费预期。',
    },
)


def _text(value):
    if isinstance(value, float) and math.isnan(value):
        return ''
    return str(value or '').strip()


def _number(value):
    if value is None or value == '' or (isinstance(value, float) and math.isnan(value)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _compact(value):
    return re.sub(r'[^0-9a-z\u4e00-\u9fff]+', '', _text(value).lower())


def _iso_time(value, data_date):
    raw = _text(value)
    if not raw:
        return None
    if re.fullmatch(r'\d{1,2}:\d{2}(?::\d{2})?', raw):
        raw = '%s %s' % (data_date, raw)
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y/%m/%d %H:%M:%S'):
        try:
            return datetime.strptime(raw[:19], fmt).isoformat(timespec='seconds') + '+08:00'
        except ValueError:
            continue
    return raw[:40]


def _source(source_id, name, tier, url=''):
    return {'id': source_id, 'name': name, 'tier': tier, 'url': _text(url)[:500] or None}


def _event_id(source_type, scheduled_at, title):
    raw = '%s|%s|%s' % (source_type, scheduled_at or '', _compact(title))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]


def _event_similarity(left, right):
    a, b = _compact(left), _compact(right)
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return min(len(a), len(b)) / max(len(a), len(b))
    pairs_a = {a[i:i + 2] for i in range(max(1, len(a) - 1))}
    pairs_b = {b[i:i + 2] for i in range(max(1, len(b) - 1))}
    return len(pairs_a & pairs_b) / max(1, len(pairs_a | pairs_b))


def _matched_rules(title):
    compact = _compact(title)
    result = []
    for rule in IMPACT_RULES:
        hits = [word for word in rule['keywords'] if _compact(word) in compact]
        if hits:
            result.append({
                'id': rule['id'], 'matchedKeywords': hits[:4],
                'sectors': list(rule['sectors']), 'reason': rule['reason'],
                'relation': 'rule-based-sensitivity', 'causal': False,
            })
    return result


def _quality(event):
    sources = event.get('sources') or []
    score = 35
    if event.get('scheduledAt'):
        score += 10
    if event.get('actual') is not None or event.get('status') == 'headline':
        score += 10
    if any(row.get('url') for row in sources):
        score += 10
    if len(sources) > 1:
        score += 20
    if event.get('title') and len(event['title']) >= 8:
        score += 10
    missing = []
    if not event.get('scheduledAt'):
        missing.append('scheduledAt')
    if not any(row.get('url') for row in sources):
        missing.append('sourceUrl')
    if event.get('status') == 'scheduled' and event.get('actual') is None:
        missing.append('actual')
    return {
        'score': min(100, score), 'corroborated': len(sources) > 1,
        'sourceCount': len(sources), 'missing': missing,
        'meaning': '衡量字段完整性与来源互证，不代表方向预测准确率',
    }


def normalize_events(calendar_rows=None, corroboration_rows=None, market_rows=None,
                     data_date='', observed_at=''):
    """Normalize AKShare macro calendars and market headlines into one fact list."""
    events = []
    for row in calendar_rows or []:
        title = _text(row.get('事件'))
        if not title:
            continue
        scheduled = _iso_time(row.get('时间'), data_date)
        actual = _number(row.get('今值'))
        item = {
            'id': _event_id('macro', scheduled, title), 'type': 'macro',
            'title': title[:240], 'region': _text(row.get('地区'))[:40] or None,
            'scheduledAt': scheduled, 'observedAt': observed_at,
            'importance': max(1, min(3, int(_number(row.get('重要性')) or 1))),
            'actual': actual, 'expected': _number(row.get('预期')),
            'previous': _number(row.get('前值')),
            'status': 'released' if actual is not None else 'scheduled',
            'sources': [_source('akshare:macro_info_ws', 'AKShare·华尔街见闻宏观日历',
                                'enrichment', row.get('链接'))],
        }
        events.append(item)
    for row in corroboration_rows or []:
        title = _text(row.get('事件'))
        if not title:
            continue
        scheduled = _iso_time(row.get('时间'), data_date)
        candidate = max((item for item in events
                         if item['type'] == 'macro'
                         and (not item.get('region') or not row.get('地区')
                              or _text(row.get('地区')) in item.get('region', '')
                              or item.get('region', '') in _text(row.get('地区')))
                         and (not scheduled or not item.get('scheduledAt')
                              or scheduled[11:16] == item['scheduledAt'][11:16])),
                        key=lambda item: _event_similarity(item['title'], title), default=None)
        if candidate and _event_similarity(candidate['title'], title) >= 0.18:
            if not any(src['id'] == 'akshare:news_economic_baidu' for src in candidate['sources']):
                candidate['sources'].append(_source(
                    'akshare:news_economic_baidu', 'AKShare·百度全球宏观事件', 'enrichment'))
            continue
        actual = _number(row.get('公布'))
        events.append({
            'id': _event_id('macro', scheduled, title), 'type': 'macro',
            'title': title[:240], 'region': _text(row.get('地区'))[:40] or None,
            'scheduledAt': scheduled, 'observedAt': observed_at,
            'importance': max(1, min(3, int(_number(row.get('重要性')) or 1))),
            'actual': actual, 'expected': _number(row.get('预期')),
            'previous': _number(row.get('前值')),
            'status': 'released' if actual is not None else 'scheduled',
            'sources': [_source('akshare:news_economic_baidu',
                                'AKShare·百度全球宏观事件', 'enrichment')],
        })
    for row in market_rows or []:
        title = _text(row.get('title'))
        if not title:
            continue
        scheduled = _iso_time(row.get('time'), data_date)
        events.append({
            'id': _event_id('headline', scheduled, title), 'type': 'headline',
            'title': title[:240], 'region': None, 'scheduledAt': scheduled,
            'observedAt': observed_at, 'importance': 1,
            'actual': None, 'expected': None, 'previous': None, 'status': 'headline',
            'sources': [_source('eastmoney:news', row.get('source_name') or '东方财富快讯',
                                row.get('source_tier') or 'market', row.get('url'))],
        })
    for item in events:
        item['rules'] = _matched_rules(item['title'])
        item['quality'] = _quality(item)
    # Exact duplicates from fallback sources are collapsed while preserving sources.
    merged = {}
    for item in events:
        key = (item['type'], item.get('scheduledAt'), _compact(item['title']))
        if key not in merged:
            merged[key] = item
            continue
        known = {source['id'] for source in merged[key]['sources']}
        merged[key]['sources'].extend(source for source in item['sources'] if source['id'] not in known)
        merged[key]['quality'] = _quality(merged[key])
    return list(merged.values())


def _sector_matches(industry, sectors):
    compact = _compact(industry).replace('ⅱ', '').replace('ⅰ', '')
    if not compact:
        return []
    return [sector for sector in sectors
            if compact in _compact(sector) or _compact(sector) in compact
            or any(len(token) >= 2 and token in compact
                   for token in re.findall(r'[\u4e00-\u9fff]{2,}', sector))]


def build_event_impact(calendar_rows=None, corroboration_rows=None, market_rows=None,
                       watchlist=None, stock_catalog=None, data_date='', observed_at=''):
    """Build the four-layer event contract: facts, rules, quality and explanations."""
    events = normalize_events(calendar_rows, corroboration_rows, market_rows,
                              data_date=data_date, observed_at=observed_at)
    catalog = {str(row.get('code') or ''): row for row in (stock_catalog or [])}
    watches = []
    for row in watchlist or []:
        code = str(row.get('code') or '')
        base = catalog.get(code) or {}
        watches.append({
            'code': code, 'name': _text(row.get('name') or base.get('name') or code)[:80],
            'industry': _text(row.get('industry') or base.get('industry'))[:80] or None,
            'group': _text(row.get('group'))[:60] or None,
            'note': _text(row.get('note'))[:200] or None,
        })
    impacts = []
    for event in events:
        sectors = []
        for rule in event['rules']:
            sectors.extend(sector for sector in rule['sectors'] if sector not in sectors)
        matches = []
        title_key = _compact(event['title'])
        for watch in watches:
            direct = bool((watch['code'] and watch['code'] in title_key)
                          or (len(_compact(watch['name'])) >= 3 and _compact(watch['name']) in title_key))
            sector_hits = _sector_matches(watch.get('industry'), sectors)
            context = _compact('%s %s' % (watch.get('group') or '', watch.get('note') or ''))
            context_hits = [sector for sector in sectors if _compact(sector) in context]
            if direct or sector_hits or context_hits:
                matches.append({
                    **watch,
                    'match': 'direct' if direct else 'sector',
                    'matchedSectors': list(dict.fromkeys(sector_hits + context_hits)),
                    'basis': ('事件标题直接出现标的名称或代码' if direct
                              else '自选行业、分组或备注与规则行业重合'),
                })
        surprise = None
        if event.get('actual') is not None and event.get('expected') is not None:
            surprise = event['actual'] - event['expected']
        explanation = ('事件直接提及你的关注标的，建议先核对原文与公告。' if any(
            row['match'] == 'direct' for row in matches) else
            ('透明规则识别出行业敏感性，并命中 %d 只自选；这不是因果或方向判断。' % len(matches)
             if matches else
             ('透明规则识别出可能敏感的行业，但当前自选没有直接命中。'
              if sectors else '尚无足够规则证据建立行业或自选关联。')))
        impacts.append({
            'event': {key: event.get(key) for key in (
                'id', 'type', 'title', 'region', 'scheduledAt', 'observedAt', 'importance',
                'actual', 'expected', 'previous', 'status', 'sources')},
            'sectors': sectors, 'watchlist': matches, 'surprise': surprise,
            'rules': event['rules'], 'quality': event['quality'],
            'explanation': explanation,
            'contract': {'facts': True, 'rules': True, 'quality': True,
                         'aiExplanationOptional': True, 'causalClaim': False},
        })
    impacts.sort(key=lambda item: (
        bool(item['watchlist']),
        any(row['match'] == 'direct' for row in item['watchlist']),
        item['event'].get('importance') or 0,
        item['quality'].get('score') or 0,
        item['event'].get('scheduledAt') or '',
    ), reverse=True)
    linked = [item for item in impacts if item['sectors'] or item['watchlist']]
    return {
        'modelVersion': MODEL_VERSION,
        'dataDate': data_date,
        'generatedAt': observed_at,
        'summary': {
            'events': len(impacts), 'linkedEvents': len(linked),
            'watchMatches': sum(len(item['watchlist']) for item in impacts),
            'highImportance': sum(1 for item in impacts if item['event'].get('importance', 0) >= 3),
        },
        'items': (linked + [item for item in impacts if item not in linked])[:24],
        'method': {
            'relation': 'rule-based-sensitivity', 'causal': False,
            'statement': '先记录事件事实，再用公开规则寻找行业和自选敏感性；相关性不等于因果。',
        },
    }
