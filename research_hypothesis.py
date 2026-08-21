"""DeepPulse research-hypothesis lifecycle.

Pure, dependency-free helpers. A hypothesis records what was knowable when it
was created, a pre-declared review window, falsifiers, and the user's eventual
review. It never creates a trading instruction or a causal claim.
"""

from datetime import datetime, timedelta, timezone
import hashlib


MODEL_VERSION = 'research-hypothesis-v1'
BJC = timezone(timedelta(hours=8))
HORIZONS = (1, 3, 5, 10, 20)
OUTCOMES = {'supported', 'mixed', 'not_supported', 'invalid'}


def _text(value, limit=240):
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


def _due_at(created_at, horizon):
    current = datetime.fromisoformat(_iso(created_at))
    remaining = horizon
    while remaining:
        current += timedelta(days=1)
        if current.weekday() < 5:
            remaining -= 1
    return current.replace(hour=15, minute=30, second=0, microsecond=0).isoformat(timespec='seconds')


def create_hypothesis(event_item, horizon_days=5, note='', now=None):
    if not isinstance(event_item, dict):
        raise ValueError('event item is required')
    event = event_item.get('event') or {}
    title = _text(event.get('title'), 300)
    event_id = _text(event.get('id'), 100)
    if not title or not event_id:
        raise ValueError('event id and title are required')
    try:
        horizon = int(horizon_days)
    except (TypeError, ValueError):
        horizon = 5
    if horizon not in HORIZONS:
        raise ValueError('unsupported review horizon')
    created_at = _iso(now)
    sectors = [_text(row, 80) for row in (event_item.get('sectors') or []) if _text(row, 80)][:8]
    watchlist = []
    for row in (event_item.get('watchlist') or [])[:8]:
        if not isinstance(row, dict):
            continue
        code = _text(row.get('code'), 20)
        if code:
            watchlist.append({'code': code, 'name': _text(row.get('name'), 80),
                              'basis': _text(row.get('basis'), 180)})
    sources = []
    for row in (event.get('sources') or [])[:6]:
        if isinstance(row, dict):
            sources.append({'id': _text(row.get('id'), 80), 'name': _text(row.get('name'), 100),
                            'tier': _text(row.get('tier'), 30), 'url': _text(row.get('url'), 500)})
    sector_text = '、'.join(sectors[:4]) or '相关行业'
    watch_text = '、'.join((row.get('name') or row['code']) for row in watchlist[:3]) or '相关自选'
    digest = hashlib.sha256((event_id + created_at).encode('utf-8')).hexdigest()[:16]
    return {
        'id': 'hypothesis:' + digest,
        'modelVersion': MODEL_VERSION,
        'status': 'observing',
        'createdAt': created_at,
        'reviewDueAt': _due_at(created_at, horizon),
        'horizonTradingDays': horizon,
        'calendarBasis': 'weekday-approximation; review time 15:30 Asia/Shanghai',
        'statement': '观察“%s”在未来 %d 个工作日内是否持续获得独立证据，并在%s及%s中出现可区别于大盘的结构反馈。' % (
            title, horizon, sector_text, watch_text),
        'baseline': {
            'eventId': event_id, 'title': title, 'type': _text(event.get('type'), 30),
            'scheduledAt': _text(event.get('scheduledAt'), 80),
            'observedAt': _text(event.get('observedAt'), 80),
            'importance': event.get('importance'), 'sources': sources,
            'sectors': sectors, 'watchlist': watchlist,
            'quality': {
                'score': (event_item.get('quality') or {}).get('score'),
                'corroborated': (event_item.get('quality') or {}).get('corroborated') is True,
                'meaning': _text((event_item.get('quality') or {}).get('meaning'), 200),
            },
        },
        'observationChecklist': [
            {'id': 'source', 'label': '事件是否被独立来源确认，且未被撤回或修正'},
            {'id': 'sector', 'label': '敏感行业是否出现相对大盘可区分的结构反馈'},
            {'id': 'watchlist', 'label': '自选反馈是否与预先记录的行业路径一致'},
        ],
        'falsifiers': [
            '原始事件被撤回、修正或没有得到独立来源确认',
            '观察窗口内相关行业没有出现可区别于大盘的结构反馈',
            '反馈更合理地由同期大盘变化或新的无关事件解释',
        ],
        'userNote': _text(note, 1000),
        'review': None,
        'contract': {
            'preRegistered': True, 'causalClaim': False, 'directionPrediction': False,
            'automaticTradingAction': False, 'userReviewRequired': True,
        },
    }


def effective_status(item, now=None):
    if not isinstance(item, dict):
        return 'invalid'
    if item.get('status') in {'completed', 'archived'}:
        return item['status']
    try:
        due = datetime.fromisoformat(_iso(item.get('reviewDueAt')))
        current = datetime.fromisoformat(_iso(now))
        return 'review_due' if current >= due else 'observing'
    except ValueError:
        return 'invalid'


def review_hypothesis(item, outcome, note='', now=None, falsifier_hits=None, data_gaps=None):
    if not isinstance(item, dict):
        raise ValueError('hypothesis is required')
    clean_outcome = _text(outcome, 30)
    if clean_outcome not in OUTCOMES:
        raise ValueError('unsupported review outcome')
    result = dict(item)
    result['status'] = 'completed'
    result['review'] = {
        'outcome': clean_outcome,
        'note': _text(note, 2000),
        'falsifierHits': [_text(row, 500) for row in (falsifier_hits or [])
                          if _text(row, 500)][:12],
        'dataGaps': [_text(row, 300) for row in (data_gaps or [])
                     if _text(row, 300)][:12],
        'reviewedAt': _iso(now),
        'userConfirmed': True,
    }
    return result


def hypothesis_snapshot(items, now=None):
    clean = []
    for row in items or []:
        if not isinstance(row, dict) or not row.get('id'):
            continue
        item = dict(row)
        item['effectiveStatus'] = effective_status(item, now)
        clean.append(item)
    clean.sort(key=lambda row: row.get('createdAt') or '', reverse=True)
    counts = {key: sum(1 for row in clean if row['effectiveStatus'] == key)
              for key in ('observing', 'review_due', 'completed', 'archived')}
    return {
        'modelVersion': MODEL_VERSION,
        'items': clean,
        'summary': {'total': len(clean), **counts},
        'boundary': '研究假设用于减少事后偏差；不构成因果证明、方向预测或交易指令。',
    }
