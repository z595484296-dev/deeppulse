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


MODEL_VERSION = 'research-workflow-v4'
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
MAX_TIMELINE_ITEMS = 80


def _template_text(value, target):
    """Replace the original subject with explicit parameters, never old conclusions."""
    text = _text(value, 1200)
    name = _text((target or {}).get('name'), 100)
    code = _text((target or {}).get('code'), 20)
    if name:
        text = text.replace(name, '{{target.name}}')
    if code:
        text = text.replace(code, '{{target.code}}')
    return text


def build_template_spec(draft):
    value = draft if isinstance(draft, dict) else {}
    target = value.get('target') if isinstance(value.get('target'), dict) else {}
    return {
        'modelVersion': 'research-template-parameters-v1',
        'parameters': [
            {'id': 'target.name', 'label': '标的名称', 'required': True, 'type': 'text'},
            {'id': 'target.code', 'label': '证券代码', 'required': target.get('type') == 'stock',
             'type': 'security_code'},
        ],
        'titleTemplate': _template_text(value.get('title'), target),
        'questionTemplate': _template_text(value.get('question'), target),
        'originalTargetType': _text(target.get('type'), 30),
        'requiresFreshPreview': True,
        'inheritsRuns': False,
        'inheritsResultCard': False,
        'inheritsConclusion': False,
        'boundary': '套用模板只复用研究方法、来源和输出设置；新标的必须重新预览与授权，旧运行、旧证据和旧结论不会继承。',
    }


def _lineage_key(value):
    text = _text(value, 120).lower()
    if not text:
        return ''
    aliases = (
        ('eastmoney', ('eastmoney', '东方财富')),
        ('cninfo', ('cninfo', '巨潮')),
        ('tencent', ('tencent', '腾讯')),
        ('tdx-local', ('tdx', '通达信')),
        ('akshare', ('akshare',)),
        ('wallstreetcn', ('wallstreetcn', '华尔街见闻')),
    )
    for key, needles in aliases:
        if any(needle in text for needle in needles):
            return key
    return re.sub(r'[^a-z0-9_-]+', '-', text).strip('-')[:60]


def _walk_lineage(value, groups, dates, freshness, depth=0):
    """Collect bounded provenance hints from provider payloads without trusting prose."""
    if depth > 5:
        return
    if isinstance(value, dict):
        source = value.get('source') if isinstance(value.get('source'), dict) else {}
        for candidate in (
                value.get('independentGroup'), value.get('upstream'),
                source.get('independentGroup'), source.get('upstream')):
            key = _lineage_key(candidate)
            if key:
                groups.add(key)
        for key in ('asOf', 'date', 'publishedAt', 'observedAt', 'fetchedAt'):
            text = _text(value.get(key), 80)
            if text:
                dates.add(text)
        status = _text(value.get('status'), 30).lower()
        if status in {'current', 'stale', 'unavailable', 'degraded'}:
            freshness[status] = freshness.get(status, 0) + 1
        for nested in value.values():
            if isinstance(nested, (dict, list)):
                _walk_lineage(nested, groups, dates, freshness, depth + 1)
    elif isinstance(value, list):
        for nested in value[:40]:
            _walk_lineage(nested, groups, dates, freshness, depth + 1)


def build_result_card(item, run):
    """Build a factual, traceable card; never decide whether the thesis is true."""
    workflow = item if isinstance(item, dict) else {}
    value = run if isinstance(run, dict) else {}
    selected = list(workflow.get('sources') or [])[:5]
    rows = []
    group_sources = {}
    gaps = []
    evidence_total = 0
    stale_total = 0
    for result in value.get('results') if isinstance(value.get('results'), list) else []:
        if not isinstance(result, dict):
            continue
        source_id = _text(result.get('sourceId'), 60)
        if source_id not in selected:
            continue
        evidence = (result.get('evidence') if isinstance(result.get('evidence'), list) else [])[:20]
        groups, dates, freshness = set(), set(), {}
        upstream_key = _lineage_key(result.get('upstream'))
        if upstream_key:
            groups.add(upstream_key)
        _walk_lineage(evidence, groups, dates, freshness)
        # AKShare is an adapter. Prefer disclosed final upstream groups when present.
        if source_id == 'akshare_macro' and len(groups) > 1:
            groups.discard('akshare')
        for group in groups:
            group_sources.setdefault(group, set()).add(source_id)
        status = _text(result.get('status'), 30) or 'unavailable'
        evidence_count = len(evidence)
        evidence_total += evidence_count
        stale_total += int(freshness.get('stale') or 0)
        if status != 'ok':
            gaps.append({
                'sourceId': source_id, 'kind': 'source_' + status,
                'message': _text(result.get('error'), 240) or '该来源本次未形成可用结果。',
            })
        elif not evidence_count:
            gaps.append({
                'sourceId': source_id, 'kind': 'no_evidence',
                'message': '来源读取成功，但没有返回可展示证据。',
            })
        rows.append({
            'sourceId': source_id, 'status': status,
            'summary': _text(result.get('summary'), 600),
            'upstream': _text(result.get('upstream'), 120),
            'fetchedAt': _text(result.get('fetchedAt'), 80),
            'evidenceCount': evidence_count,
            'lineageGroups': sorted(groups)[:8],
            'evidenceDates': sorted(dates, reverse=True)[:5],
            'freshness': {
                'current': int(freshness.get('current') or 0),
                'stale': int(freshness.get('stale') or 0),
                'unavailable': int(freshness.get('unavailable') or 0),
            },
        })
    observed = {row['sourceId'] for row in rows}
    for source_id in selected:
        if source_id not in observed:
            gaps.append({
                'sourceId': source_id, 'kind': 'missing_result',
                'message': '已选择该来源，但本次没有返回执行结果。',
            })
    duplicates = [
        {'group': group, 'sourceIds': sorted(source_ids),
         'message': '这些结果披露了相同最终上游，不能计作独立互证。'}
        for group, source_ids in sorted(group_sources.items()) if len(source_ids) > 1
    ]
    usable = sum(row['status'] == 'ok' for row in rows)
    target = workflow.get('target') if isinstance(workflow.get('target'), dict) else {}
    card = {
        'modelVersion': 'research-result-card-v1',
        'workflowId': _text(workflow.get('id'), 180),
        'runId': _text(value.get('id'), 180),
        'generatedAt': _text(value.get('ranAt'), 80) or _iso(),
        'title': _text(workflow.get('title'), 240),
        'question': _text(workflow.get('question'), 1200),
        'target': {
            'type': _text(target.get('type'), 30),
            'code': _text(target.get('code'), 20),
            'name': _text(target.get('name'), 100),
        },
        'summary': {
            'selectedSources': len(selected), 'returnedSources': len(rows),
            'usableSources': usable, 'degradedSources': len(rows) - usable,
            'evidenceItems': evidence_total, 'staleItems': stale_total,
            'gapCount': len(gaps), 'sameUpstreamGroups': len(duplicates),
        },
        'sources': rows,
        'gaps': gaps[:12],
        'sameUpstream': duplicates[:8],
        'reviewState': 'waiting_for_user',
        'automaticConclusion': False,
        'automaticTradingAction': False,
        'boundary': '本卡片只整理本次可见事实、来源血缘与缺口，不判断研究问题是否成立。',
    }
    card['reviewDraft'] = '\n'.join([
        '【研究流程复盘草稿】',
        '研究问题：' + (card['question'] or '待补充'),
        '本次结果：%d/%d 个来源可用，共 %d 条候选证据。' % (
            usable, len(selected), evidence_total),
        '需核对：%d 项缺口，%d 组相同最终上游，%d 条陈旧指标。' % (
            len(gaps), len(duplicates), stale_total),
        '我的结论：待填写（请区分事实、推断与反证条件）',
    ])
    return card


def compare_runs(previous, current):
    """Compare observable collection quality; never infer thesis direction."""
    before = previous if isinstance(previous, dict) else {}
    after = current if isinstance(current, dict) else {}
    before_card = before.get('resultCard') if isinstance(before.get('resultCard'), dict) else {}
    after_card = after.get('resultCard') if isinstance(after.get('resultCard'), dict) else {}
    if not before_card or not after_card:
        return None
    before_summary = before_card.get('summary') or {}
    after_summary = after_card.get('summary') or {}
    metric_keys = (
        'usableSources', 'degradedSources', 'evidenceItems', 'staleItems',
        'gapCount', 'sameUpstreamGroups',
    )
    deltas = {}
    for key in metric_keys:
        try:
            deltas[key] = int(after_summary.get(key) or 0) - int(before_summary.get(key) or 0)
        except (TypeError, ValueError):
            deltas[key] = 0
    before_sources = {
        row.get('sourceId'): row for row in (before_card.get('sources') or [])
        if isinstance(row, dict) and row.get('sourceId')
    }
    after_sources = {
        row.get('sourceId'): row for row in (after_card.get('sources') or [])
        if isinstance(row, dict) and row.get('sourceId')
    }
    source_changes = []
    for source_id in sorted(set(before_sources) | set(after_sources)):
        old = before_sources.get(source_id) or {}
        new = after_sources.get(source_id) or {}
        old_status = _text(old.get('status'), 30) or 'missing'
        new_status = _text(new.get('status'), 30) or 'missing'
        old_evidence = int(old.get('evidenceCount') or 0)
        new_evidence = int(new.get('evidenceCount') or 0)
        old_stale = int((old.get('freshness') or {}).get('stale') or 0)
        new_stale = int((new.get('freshness') or {}).get('stale') or 0)
        if old_status != new_status or old_evidence != new_evidence or old_stale != new_stale:
            source_changes.append({
                'sourceId': source_id,
                'previousStatus': old_status,
                'currentStatus': new_status,
                'evidenceDelta': new_evidence - old_evidence,
                'staleDelta': new_stale - old_stale,
            })
    return {
        'modelVersion': 'research-run-comparison-v1',
        'previousRunId': _text(before.get('id'), 180),
        'currentRunId': _text(after.get('id'), 180),
        'previousRanAt': _text(before.get('ranAt'), 80),
        'currentRanAt': _text(after.get('ranAt'), 80),
        'deltas': deltas,
        'sourceChanges': source_changes[:10],
        'changedSourceCount': len(source_changes),
        'automaticConclusion': False,
        'automaticTradingAction': False,
        'boundary': '本对比只说明两次收集结果的数量、状态、陈旧度和同源变化，不判断研究假设变好或变坏。',
    }


def _method_changes(previous, current, origin_kind):
    before = previous if isinstance(previous, dict) else {}
    after = current if isinstance(current, dict) else {}
    changes = []
    before_target = before.get('target') if isinstance(before.get('target'), dict) else {}
    after_target = after.get('target') if isinstance(after.get('target'), dict) else {}
    if before_target != after_target:
        changes.append('研究对象已重新填写' if origin_kind == 'template_instance' else '研究对象已变更')
    for key, label in (('question', '研究问题'), ('sources', '证据来源'), ('outputs', '输出方式'),
                       ('reviewDays', '复盘周期'), ('reminderEnabled', '到期提醒')):
        if before.get(key) != after.get(key):
            changes.append(label + '已变更')
    if before.get('title') != after.get('title') and not changes:
        changes.append('流程标题已变更')
    return changes[:8] or ['沿用原方法设置，仅创建新的独立流程记录']


def attach_workflow_lineage(created, source=None, existing=None, origin_kind='new'):
    """Attach immutable method ancestry from a server-resolved source workflow."""
    result = deepcopy(created if isinstance(created, dict) else {})
    parent = source if isinstance(source, dict) else None
    rows = existing if isinstance(existing, list) else []
    allowed_origin = origin_kind if origin_kind in {'copy', 'template_instance'} else 'new'
    if not parent:
        result['lineage'] = {
            'modelVersion': 'research-workflow-lineage-v1',
            'familyId': _text(result.get('id'), 180), 'methodVersion': 1,
            'originKind': 'new', 'originWorkflowId': None, 'originMethodVersion': None,
            'changeSummary': ['首次创建研究方法'], 'historyImmutable': True,
            'automaticConclusion': False,
        }
        return result
    parent_lineage = parent.get('lineage') if isinstance(parent.get('lineage'), dict) else {}
    family_id = _text(parent_lineage.get('familyId'), 180) or _text(parent.get('id'), 180)
    versions = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        lineage = row.get('lineage') if isinstance(row.get('lineage'), dict) else {}
        row_family = _text(lineage.get('familyId'), 180) or _text(row.get('id'), 180)
        if row_family == family_id:
            try:
                versions.append(int(lineage.get('methodVersion') or 1))
            except (TypeError, ValueError):
                versions.append(1)
    try:
        parent_version = int(parent_lineage.get('methodVersion') or 1)
    except (TypeError, ValueError):
        parent_version = 1
    result['lineage'] = {
        'modelVersion': 'research-workflow-lineage-v1',
        'familyId': family_id,
        'methodVersion': max(versions + [parent_version]) + 1,
        'originKind': allowed_origin,
        'originWorkflowId': _text(parent.get('id'), 180),
        'originMethodVersion': parent_version,
        'originCreatedAt': _text(parent.get('createdAt'), 80),
        'changeSummary': _method_changes(parent, result, allowed_origin),
        'historyImmutable': True,
        'automaticConclusion': False,
    }
    return result


def build_evidence_timeline(item):
    """Create a bounded observation timeline without rewriting stored evidence."""
    workflow = item if isinstance(item, dict) else {}
    entries = []
    source_ids = set()
    stale_count = 0
    runs = workflow.get('runs') if isinstance(workflow.get('runs'), list) else []
    for run in runs[-MAX_RUNS:]:
        if not isinstance(run, dict):
            continue
        run_id = _text(run.get('id'), 180)
        observed_at = _text(run.get('ranAt'), 80)
        for result in run.get('results') if isinstance(run.get('results'), list) else []:
            if not isinstance(result, dict):
                continue
            source_id = _text(result.get('sourceId'), 60)
            if source_id:
                source_ids.add(source_id)
            fetched_at = _text(result.get('fetchedAt'), 80) or observed_at
            evidence = result.get('evidence') if isinstance(result.get('evidence'), list) else []
            visible = evidence[:20] or [None]
            for index, evidence_item in enumerate(visible):
                evidence_row = evidence_item if isinstance(evidence_item, dict) else {}
                evidence_source = evidence_row.get('source') if isinstance(evidence_row.get('source'), dict) else {}
                status = _text(evidence_row.get('status'), 30) or _text(result.get('status'), 30) or 'unavailable'
                stale_count += int(status == 'stale')
                data_at = ''
                for key in ('asOf', 'dataDate', 'date', 'publishedAt', 'period'):
                    data_at = _text(evidence_row.get(key), 80)
                    if data_at:
                        break
                label = ''
                for key in ('title', 'name', 'label', 'id'):
                    label = _text(evidence_row.get(key), 180)
                    if label:
                        break
                if not label:
                    label = _text(result.get('summary'), 180) or '本次来源执行记录'
                upstream = (_text(evidence_row.get('upstream'), 100) or
                            _text(evidence_source.get('upstream'), 100) or
                            _text(result.get('upstream'), 100))
                seed = '|'.join((run_id, source_id, str(index), label, data_at, observed_at, fetched_at))
                entries.append({
                    'id': 'evidence-time:' + hashlib.sha256(seed.encode('utf-8')).hexdigest()[:16],
                    'runId': run_id, 'sourceId': source_id, 'observedAt': observed_at,
                    'fetchedAt': fetched_at, 'dataAt': data_at, 'status': status,
                    'label': label, 'upstream': upstream,
                    'independentGroup': (_text(evidence_row.get('independentGroup'), 80) or
                                         _text(evidence_source.get('independentGroup'), 80) or
                                         _lineage_key(upstream)),
                })
    entries.sort(key=lambda row: (row.get('observedAt') or '', row.get('fetchedAt') or ''), reverse=True)
    return {
        'modelVersion': 'research-evidence-timeline-v1',
        'items': entries[:MAX_TIMELINE_ITEMS],
        'summary': {'runs': len(runs), 'items': len(entries), 'sources': len(source_ids),
                    'staleItems': stale_count, 'truncated': len(entries) > MAX_TIMELINE_ITEMS},
        'historyImmutable': True, 'automaticConclusion': False,
        'boundary': '时间轴只记录证据何时被观察、对应的数据时点和最终上游；后续运行不会覆盖历史证据，也不据此自动判断方向。',
    }


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
    item = {
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
    if is_template:
        item['templateSpec'] = build_template_spec(draft)
    return item


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
    if 'dashboard_card' in (item.get('outputs') or []) or 'review_note' in (item.get('outputs') or []):
        run['resultCard'] = build_result_card(item, run)
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
        if not isinstance(item.get('lineage'), dict):
            item = attach_workflow_lineage(item)
        item['effectiveStatus'] = effective_status(item, now)
        runs = item.get('runs') if isinstance(item.get('runs'), list) else []
        if runs:
            latest = runs[-1]
            card_output = ('dashboard_card' in (item.get('outputs') or []) or
                           'review_note' in (item.get('outputs') or []))
            if (card_output and isinstance(latest, dict) and
                    not isinstance(latest.get('resultCard'), dict)):
                latest['resultCard'] = build_result_card(item, latest)
            item['latestRun'] = latest
            if len(runs) >= 2:
                comparison = compare_runs(runs[-2], runs[-1])
                if comparison:
                    item['runComparison'] = comparison
            item['evidenceTimeline'] = build_evidence_timeline(item)
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
