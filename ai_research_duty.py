"""Bounded, revocable AI drafting for material research-watch changes.

This module contains no network or profile I/O.  The server owns those side
effects and calls these rules only after a user has independently authorized a
running research watch.  AI output is always a review draft, never evidence or
a research conclusion.
"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from urllib.parse import urlparse


MODEL_VERSION = 'ai-research-duty-v1'
PROMPT_VERSION = 'ai-research-draft-v1'
BJC = timezone(timedelta(hours=8))
TRIGGER_KINDS = {'new_official_evidence'}
MAX_DAYS = 31
MAX_RUNS_PER_DAY = 3
GLOBAL_RUNS_PER_DAY = 3
MIN_TOKENS = 400
MAX_TOKENS = 2400


def _now(value=None):
    current = value if isinstance(value, datetime) else datetime.now(BJC)
    return current.astimezone(BJC) if current.tzinfo else current.replace(tzinfo=BJC)


def _iso(value=None):
    return _now(value).isoformat(timespec='seconds')


def _parse_time(value):
    parsed = datetime.fromisoformat(str(value or '').replace('Z', '+00:00'))
    return parsed.astimezone(BJC) if parsed.tzinfo else parsed.replace(tzinfo=BJC)


def provider_status(config):
    value = config if isinstance(config, dict) else {}
    key_configured = bool(str(value.get('deepseek_api_key') or '').strip())
    base = str(value.get('deepseek_base_url') or 'https://api.deepseek.com').strip()
    parsed = urlparse(base)
    secure = parsed.scheme == 'https' or (parsed.scheme == 'http' and parsed.hostname in {'127.0.0.1', 'localhost', '::1'})
    ready = key_configured and secure
    host = parsed.hostname or ''
    if parsed.port:
        host += ':' + str(parsed.port)
    model = str(value.get('deepseek_model') or 'deepseek-chat')[:120]
    provider_fingerprint = hashlib.sha256((host + '|' + model).encode('utf-8')).hexdigest()[:20]
    return {
        'provider': 'deepseek_api',
        'ready': ready,
        'model': model, 'host': host, 'fingerprint': provider_fingerprint,
        'reason': (None if ready else
                   'DeepSeek API 地址只允许 HTTPS 或本机 loopback HTTP。' if key_configured and not secure else
                   '独立版尚未配置 DeepSeek API；Harness 会话不能用于后台值班。'),
        'credentialStoredInProfile': False,
        'harnessSessionAllowed': False,
    }


def source_fingerprint(workflow):
    value = workflow if isinstance(workflow, dict) else {}
    canonical = {'sources': list(value.get('sources') or []),
                 'watchSources': list((value.get('watch') or {}).get('sources') or [])}
    encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()[:24]


def preview_delegation(workflow, options=None, provider=None, now=None):
    item = workflow if isinstance(workflow, dict) else {}
    value = options if isinstance(options, dict) else {}
    current = _now(now)
    blockers = []
    watch = item.get('watch') if isinstance(item.get('watch'), dict) else {}
    if item.get('status') != 'active' or item.get('kind') == 'template':
        blockers.append('只有运行中的一次性研究流程可以开启 AI 值班。')
    if not watch or watch.get('status') != 'active' or not watch.get('enabled'):
        blockers.append('请先为这条流程开启研究值守。')
    if not item.get('runs'):
        blockers.append('研究值守尚未建立首次基线。')
    if 'official_disclosures' not in (item.get('sources') or []):
        blockers.append('首版 AI 值班只支持包含官方披露来源的研究流程。')
    if 'deepseek_brief' not in (item.get('outputs') or []):
        blockers.append('这条流程未选择 DeepSeek 简报输出，请复制为新草稿并重新授权。')
    provider_value = provider if isinstance(provider, dict) else {}
    if not provider_value.get('ready'):
        blockers.append(str(provider_value.get('reason') or 'DeepSeek API 当前不可用。'))

    daily = 1
    tokens = 900
    expires_text = str(value.get('expiresAt') or '')
    try:
        expires = _parse_time(expires_text) if expires_text else current + timedelta(days=7)
    except ValueError:
        expires = current + timedelta(days=7)
    if expires <= current:
        blockers.append('AI 值班结束时间必须晚于当前时间。')
    if expires > current + timedelta(days=MAX_DAYS):
        blockers.append('AI 值班最长 31 天，到期后需要重新授权。')
    try:
        watch_expires = _parse_time(watch.get('expiresAt'))
        if expires > watch_expires:
            blockers.append('AI 值班到期日不能晚于研究值守到期日。')
    except ValueError:
        blockers.append('研究值守到期时间无效，请先重新授权值守。')
    triggers = [row for row in value.get('triggerKinds', list(TRIGGER_KINDS)) if row in TRIGGER_KINDS]
    triggers = sorted(set(triggers)) or sorted(TRIGGER_KINDS)
    method = str(watch.get('methodFingerprint') or '')
    canonical = {
        'workflowId': str(item.get('id') or ''), 'methodFingerprint': method,
        'sourceFingerprint': source_fingerprint(item), 'triggerKinds': triggers,
        'maxRunsPerDay': daily, 'maxTokensPerRun': tokens,
        'expiresAt': expires.isoformat(timespec='seconds'), 'delivery': 'center_only',
        'promptVersion': PROMPT_VERSION,
        'providerFingerprint': str(provider_value.get('fingerprint') or ''),
    }
    encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return {
        'modelVersion': MODEL_VERSION,
        'previewId': 'ai-duty-preview:' + hashlib.sha256(encoded.encode('utf-8')).hexdigest()[:20],
        'generatedAt': current.isoformat(timespec='seconds'), **canonical,
        'provider': deepcopy(provider_value), 'blockers': blockers, 'ready': not blockers,
        'permissions': [
            {'id': 'ai-duty:material-change', 'label': '仅在研究值守确认的实质变化后调用 DeepSeek'},
            {'id': 'ai-duty:frozen-evidence', 'label': '只发送本流程已授权来源的当次冻结证据'},
            {'id': 'ai-duty:budget', 'label': '同意按上述每日次数、单次长度与到期日使用模型'},
            {'id': 'ai-duty:center-only', 'label': 'AI 草稿默认只进入本机提醒中心'},
        ],
        'contract': {
            'separateFromResearchWatch': True, 'harnessSessionAllowed': False,
            'newSourcesAllowed': False, 'automaticConclusion': False,
            'automaticTradingAction': False, 'draftIsEvidence': False,
        },
    }


def confirm_delegation(workflow, preview, confirmations=None, now=None):
    if not isinstance(preview, dict) or preview.get('modelVersion') != MODEL_VERSION:
        raise ValueError('有效的 AI 值班预览是必需的')
    if not preview.get('ready') or preview.get('blockers'):
        raise ValueError('AI 值班预览仍有未解决项')
    if preview.get('methodFingerprint') != str((workflow.get('watch') or {}).get('methodFingerprint') or ''):
        raise ValueError('研究方法已经变化，请重新预览 AI 值班范围')
    if preview.get('sourceFingerprint') != source_fingerprint(workflow):
        raise ValueError('研究来源已经变化，请重新预览 AI 值班范围')
    confirmed = set(confirmations if isinstance(confirmations, list) else [])
    required = {row['id'] for row in preview.get('permissions') or []}
    missing = sorted(required - confirmed)
    if missing:
        raise ValueError('仍需确认 AI 值班权限：' + '、'.join(missing))
    if 'confirm:ai-duty' not in confirmed:
        raise ValueError('需要明确确认开启 AI 值班')
    current = _now(now)
    return {
        'id': 'ai-duty:' + str(workflow.get('id') or ''),
        'modelVersion': MODEL_VERSION, 'workflowId': str(workflow.get('id') or ''),
        'methodFingerprint': preview['methodFingerprint'],
        'sourceFingerprint': preview['sourceFingerprint'],
        'triggerKinds': list(preview.get('triggerKinds') or []),
        'promptVersion': preview.get('promptVersion') or PROMPT_VERSION,
        'providerFingerprint': str(preview.get('providerFingerprint') or ''),
        'providerHost': str((preview.get('provider') or {}).get('host') or '')[:180],
        'model': str((preview.get('provider') or {}).get('model') or '')[:120],
        'maxRunsPerDay': int(preview.get('maxRunsPerDay') or 1),
        'maxTokensPerRun': int(preview.get('maxTokensPerRun') or 1000),
        'delivery': 'center_only', 'createdAt': current.isoformat(timespec='seconds'),
        'expiresAt': preview.get('expiresAt'), 'status': 'active', 'revision': 1,
        'boundary': '仅整理已冻结证据，输出是未核验草稿，不是事实、结论、建议或交易动作。',
    }


def delegation_state(delegation, workflow, now=None, provider=None):
    value = delegation if isinstance(delegation, dict) else {}
    item = workflow if isinstance(workflow, dict) else {}
    if not value:
        return 'off'
    status = str(value.get('status') or 'invalid')
    if status in {'paused', 'revoked'}:
        return status
    try:
        if _now(now) >= _parse_time(value.get('expiresAt')):
            return 'expired'
    except ValueError:
        return 'invalid'
    watch = item.get('watch') if isinstance(item.get('watch'), dict) else {}
    if item.get('status') != 'active' or watch.get('status') != 'active' or not watch.get('enabled'):
        return 'suspended_watch'
    if value.get('methodFingerprint') != watch.get('methodFingerprint'):
        return 'suspended_reconfirm'
    if value.get('sourceFingerprint') != source_fingerprint(item):
        return 'suspended_reconfirm'
    if isinstance(provider, dict) and value.get('providerFingerprint') != provider.get('fingerprint'):
        return 'suspended_reconfirm'
    return 'active'


def eligible_trigger(change):
    value = change if isinstance(change, dict) else {}
    if not value.get('changed') or not value.get('fingerprint'):
        return None
    kinds = []
    source_ids = []
    for row in value.get('changes') or []:
        if not isinstance(row, dict) or row.get('kind') != 'evidence_set':
            continue
        added_count = int(row.get('addedCount') if row.get('addedCount') is not None
                          else max(0, int(row.get('currentCount') or 0) - int(row.get('previousCount') or 0)))
        if added_count <= 0:
            continue
        source = str(row.get('sourceId') or '')
        if source != 'official_disclosures':
            continue
        source_ids.append(source)
        kinds.append('new_official_evidence')
    if not kinds:
        return None
    return {'kinds': sorted(set(kinds)), 'sourceIds': sorted(set(source_ids)),
            'changeFingerprint': str(value.get('fingerprint'))}


def daily_usage(jobs, workflow_id, now=None):
    day = _now(now).date().isoformat()
    rows = [row for row in (jobs or []) if isinstance(row, dict)]
    completed_states = {'queued', 'running', 'completed_draft', 'failed_provider',
                        'interrupted', 'discarded_after_revocation'}
    per_workflow = sum(1 for row in rows if row.get('workflowId') == workflow_id
                       and str(row.get('createdAt') or '')[:10] == day
                       and row.get('status') in completed_states)
    global_count = sum(1 for row in rows if str(row.get('createdAt') or '')[:10] == day
                       and row.get('status') in completed_states)
    return {'date': day, 'workflow': per_workflow, 'global': global_count}


def create_job(delegation, workflow, run, change, existing_jobs=None, now=None):
    trigger = eligible_trigger(change)
    if not trigger:
        return None, 'ineligible_change'
    if delegation_state(delegation, workflow, now) != 'active':
        return None, 'delegation_inactive'
    if not set(trigger['kinds']).intersection(set(delegation.get('triggerKinds') or [])):
        return None, 'trigger_not_authorized'
    key_text = '%s|%s|%s' % (workflow.get('id'), trigger['changeFingerprint'],
                              delegation.get('promptVersion') or PROMPT_VERSION)
    key = hashlib.sha256(key_text.encode('utf-8')).hexdigest()[:24]
    rows = [row for row in (existing_jobs or []) if isinstance(row, dict)]
    if any(row.get('idempotencyKey') == key for row in rows):
        return None, 'duplicate'
    usage = daily_usage(rows, str(workflow.get('id') or ''), now)
    if usage['workflow'] >= int(delegation.get('maxRunsPerDay') or 1):
        return None, 'workflow_budget'
    if usage['global'] >= GLOBAL_RUNS_PER_DAY:
        return None, 'global_budget'
    source_ids = set(trigger['sourceIds'])
    frozen = []
    for result in (run or {}).get('results') or []:
        if not isinstance(result, dict) or str(result.get('sourceId') or '') not in source_ids:
            continue
        refs = []
        for evidence in (result.get('evidence') or [])[:12]:
            if not isinstance(evidence, dict):
                continue
            refs.append({name: str(evidence.get(name) or '')[:500]
                         for name in ('id', 'title', 'date', 'publishedAt', 'source', 'summary')
                         if evidence.get(name) is not None})
        frozen.append({
            'sourceId': str(result.get('sourceId') or ''), 'status': str(result.get('status') or ''),
            'upstream': str(result.get('upstream') or '')[:160],
            'summary': str(result.get('summary') or '')[:600],
            'evidence': refs,
        })
    current = _now(now)
    return {
        'id': 'ai-job:' + key, 'idempotencyKey': key, 'modelVersion': MODEL_VERSION,
        'workflowId': str(workflow.get('id') or ''), 'delegationId': delegation.get('id'),
        'changeFingerprint': trigger['changeFingerprint'], 'triggerKinds': trigger['kinds'],
        'promptVersion': delegation.get('promptVersion') or PROMPT_VERSION,
        'maxTokens': int(delegation.get('maxTokensPerRun') or 1000),
        'runId': str((run or {}).get('id') or ''),
        'evidenceAsOf': str((run or {}).get('ranAt') or current.isoformat(timespec='seconds')),
        'sourceEvidenceIds': trigger['sourceIds'], 'frozenEvidence': frozen,
        'question': str(workflow.get('question') or '')[:1200],
        'target': deepcopy(workflow.get('target') or {}),
        'createdAt': current.isoformat(timespec='seconds'), 'status': 'queued',
        'attempts': 0, 'delivery': 'center_only',
    }, 'queued'


def build_messages(job):
    evidence = json.dumps(job.get('frozenEvidence') or [], ensure_ascii=False,
                          sort_keys=True, separators=(',', ':'))
    system = (
        '你是 DeepPulse 的研究草稿整理器。只使用给定冻结证据，输出严格 JSON 对象，字段为 '
        'summary,facts,inferences,gaps,falsifiers,citations；summary 是字符串，其余字段都是字符串数组。事实必须能由 citations 中的 '
        'sourceId 支持；推断必须使用保留语气。不要给出买卖、仓位、收益承诺或自动行动。'
    )
    user = ('研究对象：%s\n原问题：%s\n证据时点：%s\n本次冻结证据：%s' % (
        json.dumps(job.get('target') or {}, ensure_ascii=False), job.get('question') or '',
        job.get('evidenceAsOf') or '', evidence))
    return [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}]


def parse_draft(content):
    text = str(content or '').strip()
    if len(text.encode('utf-8')) > 16 * 1024:
        raise ValueError('AI 草稿超过结构化输出上限')
    if text.startswith('```'):
        text = text.strip('`').strip()
        if text.lower().startswith('json'):
            text = text[4:].strip()
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError('AI 草稿不是对象')
    summary = str(value.get('summary') or '').strip()[:1200]
    if not summary:
        raise ValueError('AI 草稿缺少结构字段：summary')
    keys = ('facts', 'inferences', 'gaps', 'falsifiers', 'citations')
    draft = {'summary': summary}
    for key in keys:
        rows = value.get(key)
        if not isinstance(rows, list):
            raise ValueError('AI 草稿缺少结构字段：' + key)
        limit = 12 if key == 'citations' else 6
        draft[key] = [str(row)[:300] for row in rows[:limit] if str(row).strip()]
    return draft
