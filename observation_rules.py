#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic, user-confirmed composite observation rules for DeepPulse."""

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone


MODEL_VERSION = 'observation-rules-v1'
BJ = timezone(timedelta(hours=8))

SIGNALS = {
    'emotion.temperature': {'label': '情绪温度', 'kind': 'number', 'source': '情绪引擎'},
    'emotion.phase': {'label': '情绪阶段', 'kind': 'text', 'source': '情绪引擎'},
    'emotion.break_rate': {'label': '炸板率', 'kind': 'number', 'source': '情绪事实层'},
    'quote.price': {'label': '现价', 'kind': 'number', 'source': '行情主备链'},
    'quote.pct': {'label': '涨跌幅', 'kind': 'number', 'source': '行情主备链'},
}
PHASES = ('冰点期', '修复期', '发酵期', '高潮期', '退潮期')
NUMERIC_OPERATORS = {'gte', 'lte'}
TEXT_OPERATORS = {'eq', 'neq'}


def _now(value=None):
    return value if isinstance(value, datetime) else datetime.now(BJ)


def _iso(value):
    return value.isoformat(timespec='seconds')


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fingerprint(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _target(text, watchlist):
    compact = re.sub(r'\s+', '', str(text or ''))
    matches = []
    for row in watchlist or []:
        code = str(row.get('code') or '')
        name = str(row.get('name') or '')
        if (code and code in compact) or (len(name) >= 2 and name in compact):
            matches.append({'code': code, 'name': name or code})
    unique = {row['code']: row for row in matches if re.fullmatch(r'\d{6}', row['code'])}
    return list(unique.values())


def _numeric_clause(text, signal, nouns, percent=False):
    noun = '(?:' + '|'.join(nouns) + ')'
    patterns = (
        ('gte', r'%s[^，。；]{0,10}(?:高于|超过|不低于|达到|上破)\s*(-?\d+(?:\.\d+)?)\s*%%?' % noun),
        ('lte', r'%s[^，。；]{0,10}(?:低于|跌破|不高于|降到|回落到)\s*(-?\d+(?:\.\d+)?)\s*%%?' % noun),
    )
    for operator, pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            value = float(match.group(1))
            if percent and abs(value) > 1:
                value /= 100.0
            return {'signal': signal, 'operator': operator, 'value': value}
    return None


def parse_intent(text, watchlist=None, now=None):
    """Parse a deliberately small Chinese whitelist without network or writes."""
    clean = ' '.join(str(text or '').strip().split())[:300]
    if not clean:
        raise ValueError('请先描述希望深脉观察的条件')
    blocked = []
    if re.search(r'买入|卖出|下单|撤单|加仓|减仓|调仓|自动交易', clean):
        blocked.append('观察规则不能包含交易执行指令')
    has_and = bool(re.search(r'并且|同时|而且|且', clean))
    has_or = bool(re.search(r'或者|任一|任意一个|任一条件', clean))
    if has_and and has_or:
        blocked.append('一句话中混合了“全部”和“任一”，请只选择一种关系')
    logic = 'any' if has_or else 'all'
    targets = _target(clean, watchlist or [])
    if len(targets) > 1:
        blocked.append('识别到多个自选标的，请一次只设置一只股票')
    target = targets[0] if len(targets) == 1 else None

    clauses = []
    for clause in (
        _numeric_clause(clean, 'emotion.temperature', ('情绪温度', '市场温度', '温度')),
        _numeric_clause(clean, 'emotion.break_rate', ('炸板率',), percent=True),
        _numeric_clause(clean, 'quote.price', ('股价', '价格', '现价')),
        _numeric_clause(clean, 'quote.pct', ('涨跌幅', '涨幅')),
    ):
        if clause and clause not in clauses:
            clauses.append(clause)
    down = re.search(r'(?:跌幅|下跌)[^，。；]{0,8}(?:超过|达到|高于)\s*(\d+(?:\.\d+)?)\s*%?', clean)
    if down:
        clauses.append({'signal': 'quote.pct', 'operator': 'lte', 'value': -float(down.group(1))})
    for phase in PHASES:
        if phase in clean:
            operator = 'neq' if re.search(r'(?:不是|不在|避开)[^，。；]{0,5}' + phase, clean) else 'eq'
            clauses.append({'signal': 'emotion.phase', 'operator': operator, 'value': phase})
            break
    deduped = []
    for row in clauses:
        key = (row['signal'], row['operator'], row['value'])
        if key not in {(x['signal'], x['operator'], x['value']) for x in deduped}:
            deduped.append(row)
    clauses = deduped[:3]
    if not clauses:
        blocked.append('没有识别到温度、阶段、炸板率、价格或涨跌幅条件')
    if any(row['signal'].startswith('quote.') for row in clauses) and not target:
        blocked.append('个股条件必须明确指向一只已在自选中的股票')
    if len(clauses) == 1 and clauses[0]['signal'] == 'quote.price':
        blocked.append('单一价格条件请使用现有“价格提醒”')

    current = _now(now)
    expires = current + timedelta(days=5)
    draft = {
        'title': ('观察%s的组合条件' % target['name']) if target else '观察市场组合条件',
        'logic': logic,
        'target': target,
        'clauses': clauses,
        'schedule': {'marketHoursOnly': True, 'intervalSeconds': 60},
        'delivery': 'center_only',
        'expiresAt': _iso(expires),
        'cooldownSeconds': 4 * 60 * 60,
    }
    return {
        'modelVersion': MODEL_VERSION, 'input': clean, 'draft': draft,
        'understood': [describe_clause(row, target) for row in clauses],
        'blockers': blocked, 'requiresConfirmation': True,
        'boundary': 'DeepSeek 只整理草稿；确认后由本机确定性规则判断，不执行交易。',
    }


def normalize_draft(value, watchlist=None, now=None):
    source = value if isinstance(value, dict) else {}
    logic = source.get('logic') if source.get('logic') in ('all', 'any') else 'all'
    known = {str(row.get('code') or ''): row for row in (watchlist or [])}
    target = source.get('target') if isinstance(source.get('target'), dict) else None
    if target:
        code = str(target.get('code') or '')
        if code not in known:
            raise ValueError('观察标的必须仍在自选中')
        target = {'code': code, 'name': str(known[code].get('name') or code)[:80]}
    clauses = []
    for index, raw in enumerate(source.get('clauses') or []):
        if not isinstance(raw, dict) or raw.get('signal') not in SIGNALS:
            raise ValueError('观察条件包含未授权指标')
        signal = raw['signal']
        operator = raw.get('operator')
        kind = SIGNALS[signal]['kind']
        if operator not in (TEXT_OPERATORS if kind == 'text' else NUMERIC_OPERATORS):
            raise ValueError('观察条件包含不支持的比较方式')
        value_out = str(raw.get('value') or '') if kind == 'text' else _number(raw.get('value'))
        if kind == 'text' and value_out not in PHASES:
            raise ValueError('情绪阶段不在白名单内')
        if kind == 'number' and value_out is None:
            raise ValueError('观察阈值必须是数字')
        if signal.startswith('quote.') and not target:
            raise ValueError('个股条件缺少自选标的')
        clauses.append({'id': 'c%d' % (index + 1), 'signal': signal,
                        'operator': operator, 'value': value_out})
    if not 1 <= len(clauses) <= 3:
        raise ValueError('观察规则需要 1–3 个白名单条件')
    if len(clauses) == 1 and clauses[0]['signal'] == 'quote.price':
        raise ValueError('单一价格条件请使用现有“价格提醒”')
    interval = int((source.get('schedule') or {}).get('intervalSeconds') or 60)
    interval = max(30, min(600, interval))
    cooldown = max(900, min(86400, int(source.get('cooldownSeconds') or 14400)))
    current = _now(now)
    try:
        expires = datetime.fromisoformat(str(source.get('expiresAt') or ''))
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=BJ)
    except ValueError:
        expires = current + timedelta(days=5)
    if expires <= current or expires > current + timedelta(days=31):
        raise ValueError('观察规则到期时间必须在未来 31 天内')
    normalized = {
        'title': str(source.get('title') or '组合观察规则')[:100],
        'logic': logic, 'target': target, 'clauses': clauses,
        'schedule': {'marketHoursOnly': True, 'intervalSeconds': interval},
        'delivery': 'center_only' if source.get('delivery') != 'digest' else 'digest',
        'expiresAt': _iso(expires), 'cooldownSeconds': cooldown,
    }
    normalized['configFingerprint'] = _fingerprint(normalized)
    return normalized


def compare(operator, actual, expected):
    if actual is None:
        return None
    if operator == 'gte':
        return actual >= expected
    if operator == 'lte':
        return actual <= expected
    if operator == 'eq':
        return str(actual) == str(expected)
    if operator == 'neq':
        return str(actual) != str(expected)
    return None


def evaluate(rule, values):
    results = []
    for clause in rule.get('clauses') or []:
        actual = values.get(clause['signal'])
        truth = compare(clause['operator'], actual, clause['value'])
        results.append({**clause, 'actual': actual, 'truth': truth})
    truths = [row['truth'] for row in results]
    overall = None if not truths or any(value is None for value in truths) else (
        all(truths) if rule.get('logic') == 'all' else any(truths))
    return {'truth': overall, 'clauses': results}


def describe_clause(clause, target=None):
    label = SIGNALS.get(clause.get('signal'), {}).get('label') or clause.get('signal')
    if clause.get('signal', '').startswith('quote.') and target:
        label = '%s%s' % (target.get('name') or target.get('code'), label)
    operator = {'gte': '≥', 'lte': '≤', 'eq': '是', 'neq': '不是'}.get(clause.get('operator'), '?')
    value = clause.get('value')
    if clause.get('signal') == 'emotion.break_rate' and isinstance(value, (int, float)):
        value = '%.2f%%' % (value * 100)
    elif clause.get('signal') == 'quote.pct' and isinstance(value, (int, float)):
        value = '%.2f%%' % value
    return '%s %s %s' % (label, operator, value)
