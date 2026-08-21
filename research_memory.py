"""Explainable research memory built from user-confirmed hypothesis reviews."""

from collections import Counter


MODEL_VERSION = 'research-memory-v1'
OUTCOME_LABELS = {
    'supported': '支持', 'mixed': '混合',
    'not_supported': '不支持', 'invalid': '事件失效',
}


def _text(value, limit=240):
    return str(value or '').strip()[:limit]


def _readable(value, fallback, limit=240):
    text = _text(value, limit)
    suspicious = '�' in text or text.count('?') >= max(3, int(len(text) * .12))
    return _text(fallback, limit) if not text or suspicious else text


def _texts(values, limit=12, text_limit=240):
    result = []
    for value in values or []:
        clean = _text(value, text_limit)
        if clean and clean not in result:
            result.append(clean)
        if len(result) >= limit:
            break
    return result


def normalize_preferences(source=None):
    value = source if isinstance(source, dict) else {}
    hidden = _texts(value.get('hiddenMemoryIds'), 300, 180)
    notes = value.get('notes') if isinstance(value.get('notes'), dict) else {}
    clean_notes = {}
    for memory_id, note in list(notes.items())[-200:]:
        clean_id = _text(memory_id, 180)
        clean_note = _text(note, 1000)
        if clean_id and clean_note:
            clean_notes[clean_id] = clean_note
    return {
        'schema': 1,
        'enabled': value.get('enabled') is not False,
        'hiddenMemoryIds': hidden,
        'notes': clean_notes,
    }


def _baseline_of(item):
    return item.get('baseline') if isinstance(item.get('baseline'), dict) else {}


def _review_of(item):
    return item.get('review') if isinstance(item.get('review'), dict) else {}


def _memory_of(item, preferences):
    review = _review_of(item)
    if item.get('status') not in {'completed', 'archived'} or review.get('userConfirmed') is not True:
        return None
    outcome = _text(review.get('outcome'), 30)
    if outcome not in OUTCOME_LABELS:
        return None
    baseline = _baseline_of(item)
    hypothesis_id = _text(item.get('id'), 160)
    if not hypothesis_id:
        return None
    memory_id = 'memory:' + hypothesis_id
    watches = []
    for row in (baseline.get('watchlist') or [])[:12]:
        if not isinstance(row, dict):
            continue
        code = _text(row.get('code'), 20)
        if code:
            watches.append({'code': code, 'name': _text(row.get('name'), 80)})
    evidence = item.get('evidenceCandidates') if isinstance(item.get('evidenceCandidates'), list) else []
    return {
        'id': memory_id,
        'sourceHypothesisId': hypothesis_id,
        'title': _readable(baseline.get('title'), '已确认的研究复盘', 300),
        'eventType': _text(baseline.get('type'), 40),
        'reviewedAt': _text(review.get('reviewedAt'), 80),
        'outcome': outcome, 'outcomeLabel': OUTCOME_LABELS[outcome],
        'conclusion': _readable(review.get('note'), '已确认复盘（历史文本编码不可读）', 2000),
        'falsifierHits': [row for row in _texts(review.get('falsifierHits'), 12, 500)
                           if _readable(row, '', 500)],
        'dataGaps': [row for row in _texts(review.get('dataGaps'), 12, 300)
                     if _readable(row, '', 300)],
        'sectors': _texts(baseline.get('sectors'), 12, 80),
        'watchlist': watches, 'evidenceCount': len(evidence),
        'lesson': preferences['notes'].get(memory_id, ''),
        'hidden': memory_id in preferences['hiddenMemoryIds'],
        'sourceImmutable': True,
        'basis': 'user-confirmed-hypothesis-review',
    }


def _similarity(current, memory):
    baseline = _baseline_of(current)
    reasons = []
    score = 0
    current_type = _text(baseline.get('type'), 40)
    if current_type and current_type == memory['eventType']:
        score += 3
        reasons.append('事件类型相同')
    current_sectors = set(_texts(baseline.get('sectors'), 12, 80))
    sector_overlap = sorted(current_sectors.intersection(memory['sectors']))
    if sector_overlap:
        score += min(6, len(sector_overlap) * 2)
        reasons.append('共同行业：' + '、'.join(sector_overlap[:3]))
    current_codes = {
        _text(row.get('code'), 20) for row in (baseline.get('watchlist') or [])
        if isinstance(row, dict) and _text(row.get('code'), 20)
    }
    memory_codes = {row['code'] for row in memory['watchlist']}
    watch_overlap = sorted(current_codes.intersection(memory_codes))
    if watch_overlap:
        score += min(6, len(watch_overlap) * 3)
        reasons.append('共同自选：' + '、'.join(watch_overlap[:3]))
    return score, reasons


def build_snapshot(hypotheses, preferences=None):
    prefs = normalize_preferences(preferences)
    rows = [row for row in (hypotheses or []) if isinstance(row, dict)]
    memories = [memory for memory in (_memory_of(row, prefs) for row in rows) if memory]
    memories.sort(key=lambda row: row.get('reviewedAt') or '', reverse=True)
    visible = [row for row in memories if not row['hidden']]
    related = {}
    if prefs['enabled']:
        for current in rows:
            if current.get('status') in {'completed', 'archived'} or not current.get('id'):
                continue
            matches = []
            for memory in visible:
                score, reasons = _similarity(current, memory)
                if score >= 3:
                    matches.append({
                        'memoryId': memory['id'], 'title': memory['title'],
                        'outcomeLabel': memory['outcomeLabel'], 'reviewedAt': memory['reviewedAt'],
                        'lesson': memory['lesson'], 'dataGaps': memory['dataGaps'][:3],
                        'similarityScore': score, 'reasons': reasons,
                    })
            matches.sort(key=lambda row: (-row['similarityScore'], row.get('reviewedAt') or ''))
            if matches:
                related[_text(current.get('id'), 160)] = matches[:3]

    outcomes = Counter(row['outcome'] for row in visible)
    gaps = Counter(gap for row in visible for gap in row['dataGaps'])
    return {
        'modelVersion': MODEL_VERSION, 'preferences': prefs,
        'summary': {
            'total': len(memories), 'visible': len(visible),
            'hidden': len(memories) - len(visible),
            'withLesson': sum(1 for row in visible if row['lesson']),
            'withDataGaps': sum(1 for row in visible if row['dataGaps']),
        },
        'items': memories, 'relatedByHypothesis': related,
        'patterns': {
            'basis': 'user-confirmed-records-only',
            'outcomeDistribution': [
                {'outcome': key, 'label': OUTCOME_LABELS[key], 'count': outcomes.get(key, 0)}
                for key in ('supported', 'mixed', 'not_supported', 'invalid')
            ],
            'frequentDataGaps': [
                {'label': label, 'count': count} for label, count in gaps.most_common(8)
            ],
            'falsifierHitCount': sum(len(row['falsifierHits']) for row in visible),
            'minimumSampleForPattern': 3,
        },
        'boundary': '只回看你明确确认的研究结论、反证命中和数据缺口；不统计交易胜率，不根据收益倒推因果，不自动修改策略。',
        'automaticCausalInference': False,
        'automaticStrategyChange': False,
        'automaticTradingAction': False,
    }
