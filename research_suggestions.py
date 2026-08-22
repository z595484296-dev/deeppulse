"""Deterministic, user-controlled research suggestion inbox.

Suggestions are derived only from explicit local records.  They may prepare a
workflow draft, but never authorize a source, create/run a workflow, infer a
trading goal, or produce a trading action.
"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json


MODEL_VERSION = 'research-suggestions-v2'
BJC = timezone(timedelta(hours=8))
MAX_ITEMS = 200
TTL_DAYS = 7
VISIBLE_LIMIT = 8


def _text(value, limit=180):
    return str(value or '').strip()[:limit]


def _iso(value):
    return value.astimezone(BJC).isoformat(timespec='seconds')


def _parse_time(value):
    try:
        return datetime.fromisoformat(str(value or '').replace('Z', '+00:00')).astimezone(BJC)
    except (TypeError, ValueError):
        return None


def _stable_id(kind, source_id):
    raw = ('%s|%s' % (kind, source_id)).encode('utf-8')
    return 'research-suggestion:' + hashlib.sha256(raw).hexdigest()[:20]


def _workflow_draft(title, target, question, review_days=5):
    return {
        'kind': 'one_off',
        'title': _text(title, 120),
        'target': {
            'type': _text((target or {}).get('type'), 30) or 'stock',
            'code': _text((target or {}).get('code'), 20),
            'name': _text((target or {}).get('name'), 80),
        },
        'question': _text(question, 1200),
        # Deliberately use only the two baseline public sources. Optional local,
        # AKShare, and event sources remain separate user choices in preview.
        'sources': ['official_disclosures', 'market_quote'],
        'reviewDays': review_days,
        'outputs': ['dashboard_card', 'review_note', 'deepseek_brief'],
        'reminderEnabled': True,
    }


def _candidate(kind, source_id, title, reason, gaps, draft, now, role):
    return {
        'id': _stable_id(kind, source_id),
        'sourceType': kind,
        'sourceId': _text(source_id, 180),
        'role': role,
        'title': _text(title, 180),
        'reason': _text(reason, 360),
        'evidenceGaps': [_text(row, 120) for row in (gaps or []) if _text(row, 120)][:5],
        'proposedDraft': draft,
        'state': 'pending',
        'generatedAt': _iso(now),
        'expiresAt': _iso(now + timedelta(days=TTL_DAYS)),
        'contract': {
            'requiresWorkflowPreview': True,
            'requiresExplicitConfirmation': True,
            'automaticWorkflowCreation': False,
            'automaticExternalAuthorization': False,
            'automaticTradingAction': False,
        },
    }


def _journey(row, workflows):
    """Describe only explicit progress; never infer that passive viewing is adoption."""
    state = row.get('state')
    workflow_id = _text(row.get('workflowId'), 180)
    workflow = next((item for item in workflows
                     if _text(item.get('id'), 180) == workflow_id), None)
    runs = list((workflow or {}).get('runs') or [])
    if state == 'accepted' and workflow:
        if runs:
            return {
                'stage': 'ran', 'label': '已手动运行', 'nextLabel': '查看结果并复盘',
                'workflowId': workflow_id, 'runCount': len(runs),
                'lastChangedAt': _text((runs[-1] or {}).get('ranAt'), 80)
                    or _text(row.get('acceptedAt'), 80),
            }
        return {
            'stage': 'created', 'label': '已创建流程', 'nextLabel': '检查后手动运行',
            'workflowId': workflow_id, 'runCount': 0,
            'lastChangedAt': _text(row.get('acceptedAt'), 80),
        }
    if state == 'accepted':
        return {
            'stage': 'created', 'label': '已转为流程', 'nextLabel': '查看流程状态',
            'workflowId': workflow_id, 'runCount': 0,
            'lastChangedAt': _text(row.get('acceptedAt'), 80),
        }
    if state == 'dismissed':
        return {
            'stage': 'dismissed', 'label': '已忽略', 'nextLabel': '需要时恢复',
            'workflowId': '', 'runCount': 0,
            'lastChangedAt': _text(row.get('dismissedAt'), 80),
        }
    if state == 'expired':
        return {
            'stage': 'expired', 'label': '已失效', 'nextLabel': '等待新的明确依据',
            'workflowId': '', 'runCount': 0,
            'lastChangedAt': _text(row.get('expiresAt'), 80),
        }
    if row.get('previewedAt'):
        return {
            'stage': 'previewed', 'label': '已预览范围', 'nextLabel': '逐项确认后创建',
            'workflowId': '', 'runCount': 0,
            'lastChangedAt': _text(row.get('previewedAt'), 80),
        }
    if row.get('preparedAt'):
        return {
            'stage': 'drafted', 'label': '草稿已载入', 'nextLabel': '继续检查并预览',
            'workflowId': '', 'runCount': 0,
            'lastChangedAt': _text(row.get('preparedAt'), 80),
        }
    return {
        'stage': 'suggested', 'label': '待你决定', 'nextLabel': '查看并载入草稿',
        'workflowId': '', 'runCount': 0,
        'lastChangedAt': _text(row.get('generatedAt'), 80),
    }


def generate_candidates(profile_data, hypothesis_items, now=None):
    """Build bounded candidates from watchlist and saved hypotheses only."""
    current = (now or datetime.now(BJC)).astimezone(BJC)
    data = profile_data if isinstance(profile_data, dict) else {}
    hypotheses = [row for row in (hypothesis_items or []) if isinstance(row, dict)]
    active_hypothesis_codes = {
        _text(watch.get('code'), 20)
        for row in hypotheses if row.get('effectiveStatus') in {'observing', 'review_due'}
        for watch in (((row.get('baseline') or {}).get('watchlist')) or [])
        if isinstance(watch, dict) and _text(watch.get('code'), 20)
    }
    active_workflow_codes = {
        _text((row.get('target') or {}).get('code'), 20)
        for row in (data.get('research_workflows') or []) if isinstance(row, dict)
        and row.get('effectiveStatus', row.get('status')) in {'active', 'review_due'}
    }
    candidates = []

    for row in hypotheses:
        status = row.get('effectiveStatus')
        errors = list((row.get('evidenceState') or {}).get('errors') or [])[:3]
        if status != 'review_due' and not errors:
            continue
        source_id = _text(row.get('id'), 180)
        watches = [watch for watch in (((row.get('baseline') or {}).get('watchlist')) or [])
                   if isinstance(watch, dict)]
        first = watches[0] if watches else {}
        target = {'type': 'stock' if first.get('code') else 'custom',
                  'code': first.get('code'), 'name': first.get('name')}
        statement = _text(row.get('statement'), 600) or '这条已保存研究假设是否仍获事实支持？'
        if status == 'review_due':
            reason = '你预设的观察窗口已经结束，适合按原问题补齐证据后复盘。'
            gaps = errors or ['到期后的新公告', '观察窗口内的量价变化']
            title = '复盘：' + (_text(first.get('name'), 60) or _text(statement, 60))
            role = '研究员'
        else:
            reason = '已保存假设存在明确的数据缺口；建议只补证据，不改写原假设。'
            gaps = errors
            title = '补证：' + (_text(first.get('name'), 60) or _text(statement, 60))
            role = '数据核验'
        draft = _workflow_draft(title, target, statement, 3 if status == 'review_due' else 5)
        candidates.append(_candidate('hypothesis', source_id, title, reason, gaps, draft, current, role))

    watches = [row for row in (data.get('watchlist') or []) if isinstance(row, dict)]
    watches.sort(key=lambda row: int(row.get('added') or 0), reverse=True)
    for row in watches:
        code = _text(row.get('code'), 20)
        if not code or code in active_hypothesis_codes or code in active_workflow_codes:
            continue
        name = _text(row.get('name'), 80) or code
        note = _text(row.get('note'), 360)
        title = '%s基础研究' % name
        question = (('%s：' % note) if note else '') + '该标的近期变化是否得到官方披露与公开行情的共同支持？'
        draft = _workflow_draft(title, {'type': 'stock', 'code': code, 'name': name}, question, 5)
        candidates.append(_candidate(
            'watchlist', code, title,
            '它来自你的自选列表，但尚无进行中的研究假设或研究流程。',
            ['明确要验证的问题', '官方披露', '基础量价'], draft, current, '产品研究'))
    return candidates[:20]


def build_snapshot(profile_data, hypothesis_items, stored=None, now=None):
    current = (now or datetime.now(BJC)).astimezone(BJC)
    candidates = {row['id']: row for row in generate_candidates(profile_data, hypothesis_items, current)}
    workflows = [row for row in (profile_data or {}).get('research_workflows') or []
                 if isinstance(row, dict)]
    previous = {str(row.get('id')): deepcopy(row) for row in (stored or [])
                if isinstance(row, dict) and row.get('id')}
    merged = []
    for suggestion_id, candidate in candidates.items():
        old = previous.get(suggestion_id) or {}
        expires = _parse_time(old.get('expiresAt'))
        state = old.get('state') if old.get('state') in {'pending', 'dismissed', 'accepted'} else 'pending'
        reopened = bool(expires and expires <= current)
        if reopened:
            state = 'pending'
        candidate['state'] = state
        candidate['dismissedAt'] = old.get('dismissedAt') if state == 'dismissed' else None
        candidate['acceptedAt'] = old.get('acceptedAt') if state == 'accepted' else None
        candidate['workflowId'] = old.get('workflowId') if state == 'accepted' else None
        candidate['preparedAt'] = (old.get('preparedAt')
                                   if not reopened and state in {'pending', 'dismissed', 'accepted'} else None)
        candidate['previewedAt'] = (old.get('previewedAt')
                                    if not reopened and state in {'pending', 'accepted'} else None)
        candidate['journey'] = _journey(candidate, workflows)
        merged.append(candidate)
    for suggestion_id, old in previous.items():
        if suggestion_id in candidates:
            continue
        row = deepcopy(old)
        if row.get('state') not in {'accepted', 'dismissed'}:
            row['state'] = 'expired'
        row['journey'] = _journey(row, workflows)
        merged.append(row)
    rank = {'pending': 0, 'dismissed': 1, 'accepted': 2, 'expired': 3}
    merged.sort(key=lambda row: (rank.get(row.get('state'), 4), row.get('generatedAt') or ''), reverse=False)
    merged = merged[-MAX_ITEMS:] if len(merged) > MAX_ITEMS else merged
    summary = {key: sum(1 for row in merged if row.get('state') == key)
               for key in ('pending', 'dismissed', 'accepted', 'expired')}
    summary['total'] = len(merged)
    return {
        'modelVersion': MODEL_VERSION,
        'generatedAt': _iso(current),
        'summary': summary,
        'items': merged,
        'visible': [row for row in merged if row.get('state') == 'pending'][:VISIBLE_LIMIT],
        'contract': {
            'explicitRecordsOnly': True,
            'requiresWorkflowPreview': True,
            'requiresExplicitConfirmation': True,
            'automaticWorkflowCreation': False,
            'automaticExternalAuthorization': False,
            'automaticGoalInference': False,
            'automaticTradingAction': False,
        },
        'boundary': '建议只来自你的自选、已保存假设和明确证据缺口；载入只填写草稿，仍需你预览并逐项确认权限。',
    }


def mutate_item(item, action, now=None, workflow_id=''):
    row = deepcopy(item if isinstance(item, dict) else {})
    current = (now or datetime.now(BJC)).astimezone(BJC)
    if action == 'dismiss':
        row['state'] = 'dismissed'
        row['dismissedAt'] = _iso(current)
    elif action == 'restore':
        row['state'] = 'pending'
        row['dismissedAt'] = None
    elif action == 'accept':
        row['state'] = 'accepted'
        row['acceptedAt'] = _iso(current)
        row['workflowId'] = _text(workflow_id, 180)
    elif action == 'prepare':
        if row.get('state') not in {'pending', 'dismissed'}:
            raise ValueError('这条研究建议当前不能载入')
        row['preparedAt'] = _iso(current)
    elif action == 'preview':
        if row.get('state') != 'pending':
            raise ValueError('这条研究建议当前不能记录预览')
        row['preparedAt'] = row.get('preparedAt') or _iso(current)
        row['previewedAt'] = _iso(current)
    else:
        raise ValueError('不支持的研究建议操作')
    return row


def draft_fingerprint(draft):
    raw = json.dumps(draft if isinstance(draft, dict) else {}, ensure_ascii=False,
                     sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()
