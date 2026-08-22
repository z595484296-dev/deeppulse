#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic attention-center grouping.

The triage layer changes presentation, never the underlying evidence.  Every
raw attention item remains addressable and is returned inside its group.
"""

import hashlib
import json
import re
import time
from datetime import datetime, timedelta, timezone


MODEL_VERSION = 'attention-triage-v1'
BJ = timezone(timedelta(hours=8))

DISPOSITION_STATES = {'pending', 'opened', 'in_progress', 'resolved', 'snoozed', 'dismissed', 'superseded'}
TARGET_PAGES = {'overview', 'emotion', 'market', 'ladder', 'watch', 'strategy', 'epaper', 'datasrc', 'about'}
TARGET_ENTITY_TYPES = {
    'attention', 'research_workflow', 'research_hypothesis', 'research_suggestion',
    'security', 'data_component', 'service_recommendation', 'review_day',
}


def _disposition(item):
    raw = item.get('disposition') if isinstance(item.get('disposition'), dict) else {}
    status = _text(raw.get('status'))
    if status not in DISPOSITION_STATES:
        status = 'resolved' if item.get('doneAt') else 'pending'
    return {
        'status': status,
        'openedAt': raw.get('openedAt') or item.get('readAt'),
        'startedAt': raw.get('startedAt'),
        'resolvedAt': raw.get('resolvedAt') or item.get('doneAt'),
        'updatedAt': raw.get('updatedAt') or item.get('feedbackAt') or item.get('readAt'),
        'surface': _text(raw.get('surface'))[:40] or None,
    }


def _target_fingerprint(target, version_key):
    body = {
        'page': target.get('page'), 'entityType': target.get('entityType'),
        'entityId': target.get('entityId'), 'view': target.get('view'),
        'version': _text(version_key)[:1000],
    }
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()[:24]


def _typed_target(item, attention_id=None, version_key=''):
    """Build a navigation-only target from trusted item fields.

    Targets never contain URLs, selectors, commands, source permissions or execution flags.
    """
    item_id = _text(attention_id or item.get('id'))[:160]
    page = _text(item.get('page')).lower()
    page = page if page in TARGET_PAGES else 'overview'
    entity_type, entity_id, view = 'attention', item_id, 'evidence'

    workflow_id = _text(item.get('workflowId'))[:180]
    hypothesis_id = _text(item.get('hypothesisId'))[:180]
    suggestion_id = _text(item.get('suggestionId'))[:180]
    component_id = _text(item.get('componentId') or item.get('diagnosticId'))[:120]
    recommendation_id = _text(item.get('recommendationId') or item.get('effectId'))[:160]
    review_day = _text(item.get('dataDate'))[:20]
    impact = item.get('eventImpact') if isinstance(item.get('eventImpact'), dict) else {}
    watchlist = [_text(value) for value in (impact.get('watchlist') or []) if re.fullmatch(r'\d{6}', _text(value))]
    code = _text(item.get('code'))
    id_code = re.search(r'(?<!\d)(\d{6})(?!\d)', item_id)
    code = code if re.fullmatch(r'\d{6}', code) else (
        watchlist[0] if len(watchlist) == 1 else (id_code.group(1) if id_code else ''))

    if workflow_id:
        page, entity_type, entity_id = 'strategy', 'research_workflow', workflow_id
        view = 'latest_change' if item.get('kind') == 'research_watch' else 'latest_result'
    elif hypothesis_id:
        page, entity_type, entity_id, view = 'strategy', 'research_hypothesis', hypothesis_id, 'review'
    elif suggestion_id:
        page, entity_type, entity_id, view = 'strategy', 'research_suggestion', suggestion_id, 'detail'
    elif component_id:
        page, entity_type, entity_id, view = 'datasrc', 'data_component', component_id, 'diagnostics'
    elif recommendation_id:
        page, entity_type, entity_id, view = 'overview', 'service_recommendation', recommendation_id, 'service_manager'
    elif review_day and item.get('kind') in {'routine', 'review'}:
        page, entity_type, entity_id, view = 'strategy', 'review_day', review_day, 'calendar'
    elif code:
        entity_type, entity_id = 'security', code
        page = 'watch' if item.get('kind') == 'price' else page
        view = 'alert' if item.get('kind') == 'price' else 'context'

    if entity_type not in TARGET_ENTITY_TYPES:
        entity_type, entity_id, view = 'attention', item_id, 'evidence'
    target = {
        'page': page, 'entityType': entity_type, 'entityId': entity_id,
        'view': view, 'attentionId': item_id,
    }
    change = item.get('watchChange') if isinstance(item.get('watchChange'), dict) else {}
    run_id = _text(item.get('runId') or change.get('currentRunId'))[:180]
    if run_id:
        target['runId'] = run_id
    target['fingerprint'] = _target_fingerprint(
        target, version_key or item.get('targetVersion') or item.get('runId')
        or item.get('createdAt') or item_id)
    return target


def _group_disposition(items):
    rows = [_disposition(item) for item in items]
    states = [row['status'] for row in rows]
    if states and all(state == 'resolved' for state in states):
        status = 'resolved'
    elif 'in_progress' in states:
        status = 'in_progress'
    elif any(state == 'opened' for state in states):
        status = 'opened'
    elif states and all(state == 'dismissed' for state in states):
        status = 'dismissed'
    elif states and all(state == 'superseded' for state in states):
        status = 'superseded'
    elif 'snoozed' in states and not any(state in {'pending', 'opened', 'in_progress'} for state in states):
        status = 'snoozed'
    else:
        status = 'pending'
    latest = max(rows, key=lambda row: int(row.get('updatedAt') or 0), default={})
    return {**latest, 'status': status}

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
        group_id = _cluster_id(key)
        target = _typed_target(latest, group_id, ','.join(_text(row.get('id')) for row in members))
        disposition = _group_disposition(members)
        groups.append({
            'id': group_id, 'type': 'cluster', 'kind': 'event',
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
            'target': target, 'disposition': disposition,
            'traceability': {'rawCount': len(members), 'evidencePreserved': True, 'causalClaim': False},
        })

    for item in singles:
        disposition = _disposition(item)
        target = _typed_target(item)
        groups.append({
            'id': _text(item.get('id')), 'type': 'item', 'kind': item.get('kind') or 'system',
            'memberIds': [_text(item.get('id'))], 'count': 1,
            'unreadCount': 0 if item.get('readAt') or _expired(item, now_ms) else 1,
            'priority': item.get('priority') or 'medium', 'title': item.get('title') or '市场更新',
            'detail': item.get('detail') or '', 'reason': item.get('reason') or '',
            'page': item.get('page'), 'createdAt': int(item.get('createdAt') or 0),
            'expiresAt': int(item.get('expiresAt') or 0), 'readAt': item.get('readAt'),
            'feedback': item.get('feedback'), 'items': [item],
            'target': target, 'disposition': disposition,
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
            'typedTargetsServerGenerated': True,
            'deliveryReadDispositionSeparated': True,
            'statement': '未读数按可处理主题计算；原始事件不删除、不改写，也不据此自动交易。',
        },
    }
