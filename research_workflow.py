"""User-controlled research workflow contracts for DeepPulse.

The model is deliberately pure and dependency free.  It turns a draft into a
deterministic preview, requires explicit permission confirmations before
creation, and records bounded source results without producing a trading
instruction or silently changing the user's research plan.
"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re


MODEL_VERSION = 'research-workflow-v1'
BJC = timezone(timedelta(hours=8))
KINDS = {'one_off', 'template'}
TARGET_TYPES = {'stock', 'market', 'theme', 'custom'}
REVIEW_DAYS = {1, 3, 5, 10, 20}
SOURCE_DEFINITIONS = {
    'official_disclosures': {
        'label': '官方披露', 'tier': 'official', 'access': 'external',
        'purpose': '核对公司公告与法定披露，不用市场转述替代原文。',
    },
    'market_quote': {
        'label': '公开行情主备链', 'tier': 'market', 'access': 'external',
        'purpose': '读取当前行情与基础量价，仅作为公开市场事实。',
    },
    'tdx_local': {
        'label': '通达信 TQ-Local', 'tier': 'local', 'access': 'local_read_only',
        'purpose': '通过本机只读接口复核行情，不开放账户与交易能力。',
    },
    'akshare_macro': {
        'label': 'AKShare 研究增强', 'tier': 'enrichment', 'access': 'external',
        'purpose': '补充宏观、利率与跨市场背景，并保留最终上游。',
    },
    'event_news': {
        'label': '事件与市场快讯', 'tier': 'enrichment', 'access': 'external',
        'purpose': '读取已授权的事件服务结果，只表达关联线索而非因果。',
    },
}
OUTPUT_DEFINITIONS = {
    'dashboard_card': '工作台研究卡片',
    'review_note': '到期复盘记录',
    'deepseek_brief': 'DeepSeek 研究简报上下文',
}
MAX_RUNS = 20


def _text(value, limit=300):
    return str(value or '').strip()[:limit]


def _iso(value=None):
    if isinstance(value, datetime):
        current = value
    elif value:
        try:
            current = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        except ValueError:
            current = datetime.now(BJC)
    else:
        current = datetime.now(BJC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=BJC)
    return current.astimezone(BJC).isoformat(timespec='seconds')


def _working_day_due(created_at, days):
    current = datetime.fromisoformat(_iso(created_at))
    remaining = days
    while remaining:
        current += timedelta(days=1)
        if current.weekday() < 5:
            remaining -= 1
    return current.replace(hour=15, minute=30, second=0, microsecond=0).isoformat(timespec='seconds')


def _unique_allowed(values, allowed, maximum):
    clean = []
    for value in values if isinstance(values, list) else []:
        item = _text(value, 60)
        if item in allowed and item not in clean:
            clean.append(item)
    return clean[:maximum]


def normalize_draft(draft):
    value = draft if isinstance(draft, dict) else {}
    kind = _text(value.get('kind'), 20) or 'one_off'
    target_value = value.get('target') if isinstance(value.get('target'), dict) else {}
    target_type = _text(target_value.get('type'), 20) or 'stock'
    code = re.sub(r'\D', '', _text(target_value.get('code'), 20))[:6]
    target = {
        'type': target_type if target_type in TARGET_TYPES else 'stock',
        'code': code,
        'name': _text(target_value.get('name'), 80),
    }
    try:
        review_days = int(value.get('reviewDays') or 5)
    except (TypeError, ValueError):
        review_days = 5
    if review_days not in REVIEW_DAYS:
        review_days = 5
    title = _text(value.get('title'), 120)
    if not title:
        title = (target['name'] or target['code'] or '未命名对象') + '研究'
    return {
        'kind': kind if kind in KINDS else 'one_off',
        'title': title,
        'target': target,
        'question': _text(value.get('question'), 1200),
        'sources': _unique_allowed(value.get('sources'), SOURCE_DEFINITIONS, 5),
        'reviewDays': review_days,
        'outputs': _unique_allowed(value.get('outputs'), OUTPUT_DEFINITIONS, 3),
        'reminderEnabled': value.get('reminderEnabled') is True,
    }


def _environment_row(environment, source_id):
    source = environment.get(source_id) if isinstance(environment, dict) else None
    source = source if isinstance(source, dict) else {}
    return {
        'status': _text(source.get('status'), 30) or 'unobserved',
        'available': source.get('available') is True,
        'detail': _text(source.get('detail'), 180),
    }


def preview_workflow(draft, environment=None, now=None):
    clean = normalize_draft(draft)
    blockers = []
    target = clean['target']
    if target['type'] == 'stock' and len(target['code']) != 6:
        blockers.append({'field': 'target.code', 'message': '股票研究对象需要 6 位证券代码。'})
    if target['type'] != 'stock' and not target['name']:
        blockers.append({'field': 'target.name', 'message': '请填写市场、主题或自定义研究对象。'})
    stock_only = {'official_disclosures', 'market_quote', 'tdx_local'}.intersection(clean['sources'])
    if stock_only and (target['type'] != 'stock' or len(target['code']) != 6):
        blockers.append({
            'field': 'sources',
            'message': '官方披露、公开行情和通达信来源目前需要 6 位股票代码。',
        })
    if len(clean['question']) < 4:
        blockers.append({'field': 'question', 'message': '请写出一个明确的研究问题。'})
    if not clean['sources']:
        blockers.append({'field': 'sources', 'message': '至少选择一个证据来源。'})
    if not clean['outputs']:
        blockers.append({'field': 'outputs', 'message': '至少选择一种研究输出。'})

    permissions = []
    sources = []
    for source_id in clean['sources']:
        definition = SOURCE_DEFINITIONS[source_id]
        observed = _environment_row(environment or {}, source_id)
        sources.append({'id': source_id, **definition, 'environment': observed})
        permission_id = 'source:' + source_id
        permissions.append({
            'id': permission_id,
            'required': True,
            'scope': definition['access'],
            'label': '允许本次流程读取' + definition['label'],
            'sourceId': source_id,
            'persistent': False,
        })
    if clean['reminderEnabled']:
        permissions.append({
            'id': 'background:review_reminder', 'required': True,
            'scope': 'local_background', 'label': '允许到期后写入本机提醒中心',
            'sourceId': None, 'persistent': True,
        })

    steps = [
        {'order': 1, 'id': 'freeze', 'label': '冻结研究问题与来源范围',
         'automatic': True, 'externalAccess': False},
    ]
    for index, source in enumerate(sources, start=2):
        steps.append({
            'order': index, 'id': 'collect:' + source['id'],
            'label': '按需读取' + source['label'], 'automatic': False,
            'externalAccess': source['access'] == 'external',
            'availability': source['environment']['status'],
        })
    steps.extend([
        {'order': len(steps) + 1, 'id': 'separate', 'label': '区分事实、推断、缺口与反证条件',
         'automatic': True, 'externalAccess': False},
        {'order': len(steps) + 2, 'id': 'deliver', 'label': '生成用户选择的研究输出',
         'automatic': False, 'externalAccess': False},
    ])
    canonical = json.dumps(clean, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    preview_id = 'workflow-preview:' + hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:20]
    return {
        'modelVersion': MODEL_VERSION,
        'previewId': preview_id,
        'generatedAt': _iso(now),
        'draft': clean,
        'sources': sources,
        'outputs': [{'id': item, 'label': OUTPUT_DEFINITIONS[item]} for item in clean['outputs']],
        'permissions': permissions,
        'steps': steps,
        'blockers': blockers,
        'ready': not blockers,
        'contract': {
            'previewOnly': True,
            'automaticExternalAuthorization': False,
            'automaticTradingAction': False,
            'automaticStrategyChange': False,
            'deepSeekMaySuggestOnly': True,
        },
    }


def create_workflow(preview, confirmations=None, now=None):
    if not isinstance(preview, dict) or preview.get('modelVersion') != MODEL_VERSION:
        raise ValueError('有效的研究流程预览是必需的')
    if not preview.get('ready') or preview.get('blockers'):
        raise ValueError('研究流程预览仍有未解决项')
    confirmed = set(confirmations if isinstance(confirmations, list) else [])
    required = {row.get('id') for row in (preview.get('permissions') or [])
                if isinstance(row, dict) and row.get('required') is True}
    missing = sorted(item for item in required if item not in confirmed)
    if missing:
        raise ValueError('仍需确认权限：' + '、'.join(missing))
    if 'confirm:create' not in confirmed:
        raise ValueError('需要明确确认创建研究流程')
    created_at = _iso(now)
    draft = normalize_draft(preview.get('draft'))
    digest_source = preview['previewId'] + created_at
    workflow_id = 'workflow:' + hashlib.sha256(digest_source.encode('utf-8')).hexdigest()[:20]
    is_template = draft['kind'] == 'template'
    return {
        'id': workflow_id,
        'modelVersion': MODEL_VERSION,
        **deepcopy(draft),
        'status': 'template' if is_template else 'active',
        'createdAt': created_at,
        'updatedAt': created_at,
        'dueAt': None if is_template else _working_day_due(created_at, draft['reviewDays']),
        'calendarBasis': 'weekday-approximation; review time 15:30 Asia/Shanghai',
        'lastRunAt': None,
        'runs': [],
        'permissions': deepcopy(preview.get('permissions') or []),
        'permissionConfirmedAt': created_at,
        'contract': {
            'userConfirmed': True,
            'automaticExternalAuthorization': False,
            'automaticTradingAction': False,
            'automaticStrategyChange': False,
            'deepSeekMaySuggestOnly': True,
        },
    }


def effective_status(item, now=None):
    status = _text((item or {}).get('status'), 20)
    if status not in {'active', 'paused', 'template', 'completed'}:
        return 'invalid'
    if status != 'active' or not item.get('dueAt'):
        return status
    try:
        due = datetime.fromisoformat(_iso(item['dueAt']))
        current = datetime.fromisoformat(_iso(now))
        return 'review_due' if current >= due else 'active'
    except ValueError:
        return 'invalid'


def mutate_workflow(item, action, now=None):
    if not isinstance(item, dict) or not item.get('id'):
        raise ValueError('研究流程不存在')
    clean_action = _text(action, 30)
    current = _text(item.get('status'), 20)
    allowed = {
        'pause': ({'active'}, 'paused'),
        'resume': ({'paused'}, 'active'),
        'complete': ({'active', 'paused'}, 'completed'),
    }
    if clean_action not in allowed:
        raise ValueError('不支持的研究流程操作')
    valid_states, next_state = allowed[clean_action]
    if current not in valid_states:
        raise ValueError('当前状态不能执行该操作')
    result = deepcopy(item)
    result['status'] = next_state
    result['updatedAt'] = _iso(now)
    return result


def record_run(item, source_results, now=None):
    if not isinstance(item, dict) or item.get('status') != 'active':
        raise ValueError('只有运行中的研究流程可以执行')
    ran_at = _iso(now)
    clean_results = []
    selected = set(item.get('sources') or [])
    for row in source_results if isinstance(source_results, list) else []:
        if not isinstance(row, dict):
            continue
        source_id = _text(row.get('sourceId'), 60)
        if source_id not in selected:
            continue
        clean_results.append({
            'sourceId': source_id,
            'status': _text(row.get('status'), 30) or 'unavailable',
            'fetchedAt': _text(row.get('fetchedAt'), 50) or ran_at,
            'summary': _text(row.get('summary'), 600),
            'upstream': _text(row.get('upstream'), 120),
            'error': _text(row.get('error'), 240),
            'evidence': deepcopy((row.get('evidence') if isinstance(row.get('evidence'), list) else [])[:20]),
        })
    digest = hashlib.sha256((str(item.get('id')) + ran_at).encode('utf-8')).hexdigest()[:16]
    run = {
        'id': 'workflow-run:' + digest,
        'ranAt': ran_at,
        'results': clean_results,
        'summary': {
            'selected': len(selected),
            'ok': sum(row['status'] == 'ok' for row in clean_results),
            'degraded': sum(row['status'] not in {'ok'} for row in clean_results),
        },
        'automaticConclusion': False,
        'automaticTradingAction': False,
    }
    result = deepcopy(item)
    result['runs'] = (list(result.get('runs') or []) + [run])[-MAX_RUNS:]
    result['lastRunAt'] = ran_at
    result['updatedAt'] = ran_at
    return result, run


def workflow_snapshot(items, now=None):
    clean = []
    for row in items if isinstance(items, list) else []:
        if not isinstance(row, dict) or not row.get('id'):
            continue
        item = deepcopy(row)
        item['effectiveStatus'] = effective_status(item, now)
        clean.append(item)
    clean.sort(key=lambda row: row.get('updatedAt') or row.get('createdAt') or '', reverse=True)
    states = ('active', 'review_due', 'paused', 'template', 'completed', 'invalid')
    return {
        'modelVersion': MODEL_VERSION,
        'items': clean,
        'summary': {'total': len(clean), **{
            state: sum(row['effectiveStatus'] == state for row in clean) for state in states
        }},
        'sourceDefinitions': deepcopy(SOURCE_DEFINITIONS),
        'outputDefinitions': deepcopy(OUTPUT_DEFINITIONS),
        'boundary': '研究流程只组织证据收集与复盘，不会连接交易账户、自动下单或擅自授权外部访问。',
    }
