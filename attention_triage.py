#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic attention-center grouping.

The triage layer changes presentation, never the underlying evidence.  Every
raw attention item remains addressable and is returned inside its group.
"""

import hashlib
import re
import time
from datetime import datetime, timedelta, timezone


MODEL_VERSION = 'attention-triage-v1'
BJ = timezone(timedelta(hours=8))

TOPICS = (
    ('ai_compute', 'AI 算力', ('ai', '人工智能', '算力', '服务器', '数据中心', 'cpo', '光模块', '英伟达')),
    ('semiconductor', '半导体', ('半导体', '芯片', '存储', '晶圆', '封装')),
    ('macro_rates_fx', '宏观与汇率', ('利率', '降息', '加息', '央行', 'lpr', '美元', '人民币', '汇率', '关税', '贸易')),
    ('energy', '能源与原材料', ('原油', '石油', '油价', '天然气', '黄金', '白银', '铜价', '铝价')),
    ('manufacturing', '制造业景气', ('pmi', '制造业', '工业生产', '设备更新', '固定资产投资')),
    ('company', '公司动态', ('公告', '业绩', '订单', '中标', '回购', '增持', '减持', '分红')),
)


def _text(value):
    return str(value or '').strip()


def _topic(item):
    title = _text(item.get('title')).lower()
    for topic_id, label, words in TOPICS:
        if any(word in title for word in words):
            return topic_id, label
    sectors = ' '.join(_text(value) for value in ((item.get('eventImpact') or {}).get('sectors') or []))
    return ('industry', sectors[:24]) if sectors else ('market', '市场动态')


def _target(item):
    impact = item.get('eventImpact') if isinstance(item.get('eventImpact'), dict) else {}
    codes = sorted({_text(value) for value in (impact.get('watchlist') or []) if _text(value)})
    match = re.search(r'命中自选：([^；。]+)', _text(item.get('detail')))
    labels = [part.strip() for part in re.split(r'[、,，]', match.group(1)) if part.strip()] if match else []
    key = ','.join(codes) or ','.join(labels) or 'market'
    label = '、'.join(labels[:2]) or ('自选标的 ' + '、'.join(codes[:2]) if codes else '全市场')
    return key[:120], label[:48]


def _day(timestamp):
    value = int(timestamp or 0) / 1000
    try:
        return datetime.fromtimestamp(value, BJ).strftime('%Y-%m-%d')
    except (OverflowError, OSError, ValueError):
        return 'unknown'


def _expired(item, now_ms):
    return int(item.get('expiresAt') or 0) > 0 and now_ms >= int(item.get('expiresAt') or 0)


def _cluster_id(key):
    digest = hashlib.sha256('|'.join(key).encode('utf-8')).hexdigest()[:18]
    return 'triage:' + digest


def build_attention_triage(items=None, now_ms=None):
    """Return stable groups without mutating or discarding raw attention items."""
    now_ms = int(now_ms or time.time() * 1000)
    raw = [row for row in (items or []) if isinstance(row, dict) and _text(row.get('id'))]
    event_buckets = {}
    singles = []
    for item in raw:
        if item.get('kind') != 'event':
            singles.append(item)
            continue
        topic_id, topic_label = _topic(item)
        target_key, target_label = _target(item)
        key = (_day(item.get('createdAt')), target_key, topic_id)
        bucket = event_buckets.setdefault(key, {
            'topic': topic_label, 'target': target_label, 'items': [],
        })
        bucket['items'].append(item)

    groups = []
    for key, bucket in event_buckets.items():
        members = sorted(bucket['items'], key=lambda row: int(row.get('createdAt') or 0), reverse=True)
        if len(members) == 1:
            singles.append(members[0])
            continue
        active_unread = [row for row in members if not row.get('readAt') and not _expired(row, now_ms)]
        latest = members[0]
        sources = []
        for row in members:
            reason = _text(row.get('reason'))
            match = re.search(r'来源\s*([^，；]+)', reason)
            if match and match.group(1).strip() not in sources:
                sources.append(match.group(1).strip())
        groups.append({
            'id': _cluster_id(key), 'type': 'cluster', 'kind': 'event',
            'memberIds': [_text(row.get('id')) for row in members],
            'count': len(members), 'unreadCount': len(active_unread),
            'priority': 'high' if any(row.get('priority') == 'high' for row in members) else 'medium',
            'title': '%s · %s动态（%d 条）' % (bucket['target'], bucket['topic'], len(members)),
            'detail': '新增 %d 条待核事件；已按同一关注标的、主题和日期合并。' % len(active_unread),
            'reason': '仅整理展示，不合并证据独立性；可展开查看每条原始标题、来源与时点。',
            'page': latest.get('page') or 'overview',
            'createdAt': max(int(row.get('createdAt') or 0) for row in members),
            'expiresAt': max(int(row.get('expiresAt') or 0) for row in members),
            'readAt': None if active_unread else max(int(row.get('readAt') or 0) for row in members),
            'feedback': latest.get('feedback'), 'sources': sources[:4], 'items': members,
            'traceability': {'rawCount': len(members), 'evidencePreserved': True, 'causalClaim': False},
        })

    for item in singles:
        groups.append({
            'id': _text(item.get('id')), 'type': 'item', 'kind': item.get('kind') or 'system',
            'memberIds': [_text(item.get('id'))], 'count': 1,
            'unreadCount': 0 if item.get('readAt') or _expired(item, now_ms) else 1,
            'priority': item.get('priority') or 'medium', 'title': item.get('title') or '市场更新',
            'detail': item.get('detail') or '', 'reason': item.get('reason') or '',
            'page': item.get('page'), 'createdAt': int(item.get('createdAt') or 0),
            'expiresAt': int(item.get('expiresAt') or 0), 'readAt': item.get('readAt'),
            'feedback': item.get('feedback'), 'items': [item],
            'traceability': {'rawCount': 1, 'evidencePreserved': True, 'causalClaim': False},
        })
    groups.sort(key=lambda row: (bool(row.get('unreadCount')), int(row.get('createdAt') or 0)), reverse=True)
    return {
        'modelVersion': MODEL_VERSION, 'generatedAt': now_ms,
        'rawCount': len(raw), 'groupCount': len(groups),
        'unreadRawCount': sum(1 for row in raw if not row.get('readAt') and not _expired(row, now_ms)),
        'unreadGroupCount': sum(1 for row in groups if row.get('unreadCount')),
        'groups': groups,
        'policy': {
            'groupingOnly': True, 'rawEvidencePreserved': True,
            'highPriorityPriceAlertsStayIndividual': True,
            'statement': '未读数按可处理主题计算；原始事件不删除、不改写，也不据此自动交易。',
        },
    }
