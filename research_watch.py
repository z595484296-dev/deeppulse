"""Pure rules for user-authorized, per-workflow research watch.

The watch never chooses new sources, reaches a trading account, or asks an AI.
It only schedules re-reading the sources already frozen in a research workflow
and describes observable collection changes.
"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json


MODEL_VERSION = 'research-watch-v1'
BJC = timezone(timedelta(hours=8))
FREQUENCIES = {'daily', 'close'}
DELIVERIES = {'center_only', 'digest'}


def _now(value=None):
    current = value if isinstance(value, datetime) else datetime.now(BJC)
    return current.astimezone(BJC) if current.tzinfo else current.replace(tzinfo=BJC)


def _iso(value=None):
    return _now(value).isoformat(timespec='seconds')


def method_fingerprint(workflow):
    value = workflow if isinstance(workflow, dict) else {}
    canonical = {
        'id': str(value.get('id') or ''),
        'target': value.get('target') if isinstance(value.get('target'), dict) else {},
        'question': str(value.get('question') or ''),
        'sources': list(value.get('sources') or []),
    }
    text = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:24]


def next_check_at(now=None, frequency='close'):
    current = _now(now)
    hour, minute = (15, 20) if frequency == 'close' else (9, 5)
    candidate = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= current:
        candidate += timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate.isoformat(timespec='seconds')


def preview_watch(workflow, options=None, now=None):
    item = workflow if isinstance(workflow, dict) else {}
    value = options if isinstance(options, dict) else {}
    current = _now(now)
    blockers = []
    if item.get('status') != 'active' or item.get('kind') == 'template':
        blockers.append('只有运行中的一次性研究流程可以开启值守。')
    sources = [str(row) for row in (item.get('sources') or []) if str(row)]
    if not sources:
        blockers.append('研究流程没有已确认的来源。')
    frequency = str(value.get('frequency') or 'close')
    if frequency not in FREQUENCIES:
        frequency = 'close'
    delivery = str(value.get('delivery') or 'center_only')
    if delivery not in DELIVERIES:
        delivery = 'center_only'
    expires_text = str(value.get('expiresAt') or item.get('dueAt') or '')
    try:
        expires = datetime.fromisoformat(expires_text.replace('Z', '+00:00'))
        expires = expires.astimezone(BJC) if expires.tzinfo else expires.replace(tzinfo=BJC)
    except ValueError:
        expires = current + timedelta(days=5)
    if expires <= current:
        blockers.append('值守结束时间必须晚于当前时间。')
    if expires > current + timedelta(days=31):
        blockers.append('单次值守最长 31 天，到期后可重新授权。')
    canonical = {
        'workflowId': str(item.get('id') or ''),
        'methodFingerprint': method_fingerprint(item),
        'sources': sources,
        'frequency': frequency,
        'expiresAt': expires.isoformat(timespec='seconds'),
        'delivery': delivery,
    }
    encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    preview_id = 'watch-preview:' + hashlib.sha256(encoded.encode('utf-8')).hexdigest()[:20]
    return {
        'modelVersion': MODEL_VERSION,
        'previewId': preview_id,
        'generatedAt': current.isoformat(timespec='seconds'),
        **canonical,
        'nextCheckAt': next_check_at(current, frequency),
        'estimatedChecks': max(1, min(31, (expires.date() - current.date()).days + 1)),
        'permissions': [
            {'id': 'watch:source:' + source, 'sourceId': source,
             'label': '允许值守期间重复读取已确认来源：' + source,
             'persistent': True}
            for source in sources
        ] + [{
            'id': 'watch:attention', 'sourceId': None,
            'label': ('只在有实质变化时写入提醒中心' if delivery == 'center_only'
                      else '有实质变化时按已授权终端发送摘要'),
            'persistent': True,
        }],
        'blockers': blockers,
        'ready': not blockers,
        'contract': {
            'perWorkflowOptIn': True,
            'newSourcesAllowed': False,
            'automaticAI': False,
            'automaticConclusion': False,
            'automaticTradingAction': False,
        },
    }


def confirm_watch(workflow, preview, confirmations=None, now=None):
    if not isinstance(preview, dict) or preview.get('modelVersion') != MODEL_VERSION:
        raise ValueError('有效的研究值守预览是必需的')
    if not preview.get('ready') or preview.get('blockers'):
        raise ValueError('研究值守预览仍有未解决项')
    if preview.get('methodFingerprint') != method_fingerprint(workflow):
        raise ValueError('研究方法已经变化，请重新预览值守范围')
    confirmed = set(confirmations if isinstance(confirmations, list) else [])
    required = {row['id'] for row in preview.get('permissions') or []}
    missing = sorted(required - confirmed)
    if missing:
        raise ValueError('仍需确认值守权限：' + '、'.join(missing))
    if 'confirm:watch' not in confirmed:
        raise ValueError('需要明确确认开启研究值守')
    current = _now(now)
    result = deepcopy(workflow)
    result['watch'] = {
        'modelVersion': MODEL_VERSION,
        'enabled': True,
        'status': 'active',
        'methodFingerprint': preview['methodFingerprint'],
        'sources': list(preview.get('sources') or []),
        'frequency': preview.get('frequency'),
        'delivery': preview.get('delivery'),
        'startedAt': current.isoformat(timespec='seconds'),
        'expiresAt': preview.get('expiresAt'),
        'nextCheckAt': next_check_at(current, preview.get('frequency')),
        'lastCheckedAt': None,
        'lastChangeAt': None,
        'lastChangeFingerprint': None,
        'sourceFailures': {},
        'pausedSources': [],
        'boundary': '只重复读取本流程已冻结来源；无变化不提醒，不自动调用 DeepSeek、下结论或交易。',
    }
    result['updatedAt'] = current.isoformat(timespec='seconds')
    return result


def watch_state(workflow, now=None):
    item = workflow if isinstance(workflow, dict) else {}
    watch = item.get('watch') if isinstance(item.get('watch'), dict) else {}
    if not watch:
        return 'off'
    if item.get('status') != 'active':
        return 'workflow_inactive'
    if watch.get('methodFingerprint') != method_fingerprint(item):
        return 'reauthorization_required'
    try:
        expires = datetime.fromisoformat(str(watch.get('expiresAt') or '').replace('Z', '+00:00'))
        expires = expires.astimezone(BJC) if expires.tzinfo else expires.replace(tzinfo=BJC)
        if _now(now) >= expires:
            return 'expired'
    except ValueError:
        return 'invalid'
    return str(watch.get('status') or ('active' if watch.get('enabled') else 'off'))


def is_due(workflow, now=None):
    if watch_state(workflow, now) != 'active':
        return False
    watch = workflow.get('watch') or {}
    try:
        due = datetime.fromisoformat(str(watch.get('nextCheckAt') or '').replace('Z', '+00:00'))
        due = due.astimezone(BJC) if due.tzinfo else due.replace(tzinfo=BJC)
        return _now(now) >= due
    except ValueError:
        return False


def _evidence_identity(source_id, evidence):
    rows = evidence if isinstance(evidence, list) else []
    identities = []
    for row in rows[:40]:
        if not isinstance(row, dict):
            continue
        if source_id in {'official_disclosures', 'event_news'}:
            value = (row.get('id'), row.get('title'), row.get('publishedAt') or row.get('date'))
        elif source_id == 'akshare_macro':
            metrics = row.get('metrics') if isinstance(row.get('metrics'), list) else []
            if metrics:
                for metric in metrics[:30]:
                    if isinstance(metric, dict):
                        identities.append((metric.get('id'), metric.get('status'),
                                           metric.get('asOf') or metric.get('date')))
                continue
            value = (row.get('id') or row.get('packId'), row.get('status'),
                     row.get('asOf') or row.get('date'))
        else:
            continue
        identities.append(value)
    return sorted(identities, key=lambda value: repr(value))


def material_change(previous_run, current_run):
    before = previous_run if isinstance(previous_run, dict) else {}
    after = current_run if isinstance(current_run, dict) else {}
    before_rows = {row.get('sourceId'): row for row in before.get('results') or [] if isinstance(row, dict)}
    after_rows = {row.get('sourceId'): row for row in after.get('results') or [] if isinstance(row, dict)}
    changes = []
    for source_id in sorted(set(before_rows) | set(after_rows)):
        old = before_rows.get(source_id) or {}
        new = after_rows.get(source_id) or {}
        old_status = str(old.get('status') or 'missing')
        new_status = str(new.get('status') or 'missing')
        if old_status != new_status:
            changes.append({'sourceId': source_id, 'kind': 'source_status',
                            'previous': old_status, 'current': new_status})
        old_ids = _evidence_identity(source_id, old.get('evidence'))
        new_ids = _evidence_identity(source_id, new.get('evidence'))
        if old_ids != new_ids:
            old_set = {repr(value) for value in old_ids}
            new_set = {repr(value) for value in new_ids}
            changes.append({'sourceId': source_id, 'kind': 'evidence_set',
                            'previousCount': len(old_ids), 'currentCount': len(new_ids),
                            'addedCount': len(new_set - old_set),
                            'removedCount': len(old_set - new_set)})
    canonical = json.dumps(changes, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    fingerprint = hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:20] if changes else ''
    return {
        'modelVersion': MODEL_VERSION,
        'changed': bool(changes),
        'changes': changes[:20],
        'fingerprint': fingerprint,
        'automaticConclusion': False,
        'automaticTradingAction': False,
        'boundary': '变化只描述来源状态和候选证据集合，不代表利好、利空或研究问题成立。',
    }
