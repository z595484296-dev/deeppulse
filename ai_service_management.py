"""Product-facing control plane for bounded AI research duty.

Pure rules only: no file, network, thread, or secret access.  The server owns
side effects and uses this module to keep onboarding and global budget state
consistent across every client surface.
"""

from datetime import datetime, timedelta, timezone
import hashlib
import json


MODEL_VERSION = 'ai-service-management-v1'
BJC = timezone(timedelta(hours=8))
HARD_DAILY_LIMIT = 3
PLAN_TTL_MINUTES = 10


def _now(value=None):
    current = value if isinstance(value, datetime) else datetime.now(BJC)
    return current.astimezone(BJC) if current.tzinfo else current.replace(tzinfo=BJC)


def normalize_preferences(value=None):
    raw = value if isinstance(value, dict) else {}
    try:
        limit = int(raw.get('dailyLimit', HARD_DAILY_LIMIT))
    except (TypeError, ValueError):
        limit = HARD_DAILY_LIMIT
    return {
        'schema': 1,
        'paused': raw.get('paused') is True,
        'dailyLimit': max(0, min(HARD_DAILY_LIMIT, limit)),
        'updatedAt': str(raw.get('updatedAt') or '')[:40] or None,
    }


def onboarding(provider, workflows):
    provider_value = provider if isinstance(provider, dict) else {}
    workflow_value = workflows if isinstance(workflows, dict) else {}
    items = [row for row in (workflow_value.get('items') or []) if isinstance(row, dict)]
    with_baseline = [row for row in items if bool(row.get('runs'))]
    with_watch = [row for row in with_baseline
                  if (row.get('watch') or {}).get('effectiveStatus') == 'active']
    with_duty = [row for row in with_watch
                 if (row.get('aiDuty') or {}).get('effectiveStatus') == 'active']
    with_reviewed_draft = [
        row for row in with_duty
        if any(job.get('reviewStatus') in {'evidence_opened', 'verified', 'staged'}
               or job.get('status') == 'dismissed'
               for job in (row.get('aiDrafts') or []))]
    steps = [
        {'id': 'provider', 'label': '连接并验证独立 DeepSeek API',
         'done': provider_value.get('ready') is True},
        {'id': 'workflow', 'label': '创建一条研究流程', 'done': bool(items)},
        {'id': 'baseline', 'label': '手动执行一次，建立证据基线',
         'done': bool(with_baseline)},
        {'id': 'watch', 'label': '单独开启研究值守',
         'done': bool(with_watch)},
        {'id': 'duty', 'label': '单独授权 AI 研判值班',
         'done': bool(with_duty)},
        {'id': 'draft', 'label': '核对第一份 AI 草稿',
         'done': bool(with_reviewed_draft)},
    ]
    first = next((row for row in steps if not row['done']), None)
    actions = {
        'provider': {'label': '连接并验证', 'page': 'overview', 'action': 'open_provider'},
        'workflow': {'label': '创建研究流程', 'page': 'strategy', 'action': 'open_workflow'},
        'baseline': {'label': '建立证据基线', 'page': 'strategy', 'action': 'open_workflow'},
        'watch': {'label': '开启研究值守', 'page': 'strategy', 'action': 'open_workflow'},
        'duty': {'label': '授权 AI 值班', 'page': 'strategy', 'action': 'open_workflow'},
        'draft': {'label': '核对 AI 草稿', 'page': 'strategy', 'action': 'open_ai_draft'},
    }
    return {
        'complete': first is None,
        'completed': sum(1 for row in steps if row['done']),
        'total': len(steps),
        'steps': steps,
        'next': actions.get(first['id']) if first else {
            'label': '查看 AI 值班', 'page': 'strategy', 'action': 'open_workflow'},
    }


def build_status(provider, workflows, preferences=None, profile_revision=0):
    prefs = normalize_preferences(preferences)
    workflow_value = workflows if isinstance(workflows, dict) else {}
    summary = dict(workflow_value.get('aiDutySummary') or {})
    summary['hardDailyLimit'] = HARD_DAILY_LIMIT
    summary['userDailyLimit'] = prefs['dailyLimit']
    summary['paused'] = prefs['paused']
    recent = []
    for workflow in workflow_value.get('items') or []:
        if not isinstance(workflow, dict):
            continue
        for job in workflow.get('aiDutyJobs') or []:
            if not isinstance(job, dict):
                continue
            recent.append({**job, 'workflowId': workflow.get('id'),
                           'workflowTitle': workflow.get('title')})
    recent.sort(key=lambda row: str(row.get('finishedAt') or row.get('startedAt')
                                    or row.get('createdAt') or ''), reverse=True)
    return {
        'modelVersion': MODEL_VERSION,
        'provider': provider if isinstance(provider, dict) else {},
        'preferences': prefs,
        'summary': summary,
        'onboarding': onboarding(provider, workflow_value),
        'recentJobs': recent[:8],
        'profileRevision': int(profile_revision or 0),
        'boundary': ('全局暂停只阻止新的 AI 调用；研究值守、确定性规则和原始变化提醒仍继续。'
                     'AI 草稿不会自动保存为结论或触发交易。'),
    }


def preview_preferences(current, proposed, profile_revision, now=None):
    before = normalize_preferences(current)
    after = normalize_preferences(proposed)
    generated = _now(now)
    changes = []
    if before['paused'] != after['paused']:
        changes.append({'field': 'paused', 'from': before['paused'], 'to': after['paused'],
                        'label': '暂停所有新的 AI 调用' if after['paused'] else '恢复新的 AI 调用'})
    if before['dailyLimit'] != after['dailyLimit']:
        changes.append({'field': 'dailyLimit', 'from': before['dailyLimit'],
                        'to': after['dailyLimit'],
                        'label': '每日调用上限 %s → %s' %
                                 (before['dailyLimit'], after['dailyLimit'])})
    canonical = {'before': before, 'after': after,
                 'profileRevision': int(profile_revision or 0),
                 'generatedAt': generated.isoformat(timespec='seconds')}
    encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True,
                         separators=(',', ':'))
    return {
        'modelVersion': MODEL_VERSION,
        'planId': 'ai-service-plan:' + hashlib.sha256(encoded.encode('utf-8')).hexdigest()[:22],
        'generatedAt': canonical['generatedAt'],
        'expiresAt': (generated + timedelta(minutes=PLAN_TTL_MINUTES)).isoformat(timespec='seconds'),
        'profileRevision': canonical['profileRevision'],
        'before': before, 'after': after, 'changes': changes,
        'ready': bool(changes),
        'confirmations': [
            {'id': 'ai-service:budget',
             'label': '确认该设置只约束后台 AI 调用，不会停止研究值守或删除历史草稿'},
        ],
    }


def validate_plan(plan, plan_id, profile_revision, confirmations=None, now=None):
    if not isinstance(plan, dict) or plan.get('modelVersion') != MODEL_VERSION:
        raise ValueError('AI 服务调整预览不存在，请重新预览')
    if str(plan.get('planId') or '') != str(plan_id or ''):
        raise ValueError('AI 服务调整预览已变化，请重新预览')
    if int(plan.get('profileRevision') or -1) != int(profile_revision or 0):
        raise ValueError('用户档案已变化，请重新预览 AI 服务设置')
    try:
        if _now(now) >= datetime.fromisoformat(str(plan.get('expiresAt')).replace('Z', '+00:00')):
            raise ValueError('AI 服务调整预览已过期，请重新预览')
    except (TypeError, ValueError) as error:
        if isinstance(error, ValueError) and '已过期' in str(error):
            raise
        raise ValueError('AI 服务调整预览已失效，请重新预览')
    confirmed = set(confirmations if isinstance(confirmations, list) else [])
    if 'ai-service:budget' not in confirmed or 'confirm:ai-service' not in confirmed:
        raise ValueError('请确认 AI 服务预算与暂停边界')
    if not plan.get('ready') or not plan.get('changes'):
        raise ValueError('AI 服务设置没有变化')
    return normalize_preferences(plan.get('after'))
