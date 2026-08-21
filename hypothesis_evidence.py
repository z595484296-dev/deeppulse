"""Candidate-evidence collection for DeepPulse research hypotheses.

The collector freezes a market baseline, then appends timestamped observations
from quotes, a broad-market benchmark, and official disclosures.  It deliberately
does not decide whether a hypothesis is supported and never emits trading advice.
"""

from datetime import datetime, timedelta, timezone
import hashlib


MODEL_VERSION = 'hypothesis-evidence-v1'
BJC = timezone(timedelta(hours=8))
MAX_CANDIDATES = 80


def _text(value, limit=300):
    return str(value or '').strip()[:limit]


def _number(value):
    try:
        number = float(value)
        return number if number == number else None
    except (TypeError, ValueError):
        return None


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


def _source(row, default_id='market', default_name='市场行情', default_tier='market'):
    source_id = _text((row or {}).get('source'), 60) or default_id
    names = {'tdx_local': '通达信 TQ-Local', 'em': '东方财富', 'tq': '腾讯行情'}
    tiers = {'tdx_local': 'local', 'em': 'market', 'tq': 'market'}
    return {
        'id': source_id,
        'name': names.get(source_id, default_name),
        'tier': tiers.get(source_id, default_tier),
        'url': _text((row or {}).get('url'), 500) or None,
    }


def _benchmark(rows):
    candidates = rows if isinstance(rows, list) else []
    row = next((item for item in candidates if str((item or {}).get('code')) == '000001'), None)
    row = row or (candidates[0] if candidates else None)
    if not isinstance(row, dict) or _number(row.get('price')) in (None, 0):
        raise ValueError('broad-market benchmark unavailable')
    return {
        'code': _text(row.get('code'), 20),
        'name': _text(row.get('name'), 80) or '市场基准',
        'price': _number(row.get('price')),
        'source': _source(row, 'em:index', '公开指数行情', 'market'),
    }


def capture_market_baseline(item, quote_loader, benchmark_loader, now=None):
    """Freeze quote and benchmark values without mutating the input item."""
    if not isinstance(item, dict) or not item.get('id'):
        raise ValueError('hypothesis is required')
    result = dict(item)
    captured_at = _iso(now)
    errors = []
    baseline = {'capturedAt': captured_at, 'benchmark': None, 'watchlist': [],
                'contract': {'pointInTime': True, 'closePriceGuaranteed': False}}
    try:
        baseline['benchmark'] = _benchmark(benchmark_loader())
    except Exception as exc:
        errors.append('benchmark: ' + _text(exc, 180))
    for watch in ((item.get('baseline') or {}).get('watchlist') or [])[:8]:
        code = _text((watch or {}).get('code'), 20)
        if not code:
            continue
        try:
            quote = quote_loader(code) or {}
            price = _number(quote.get('price'))
            if price in (None, 0):
                raise ValueError('price unavailable')
            baseline['watchlist'].append({
                'code': code, 'name': _text(quote.get('name') or watch.get('name'), 80) or code,
                'price': price, 'source': _source(quote),
            })
        except Exception as exc:
            errors.append('%s: %s' % (code, _text(exc, 160)))
    result['marketBaseline'] = baseline
    result.setdefault('evidenceCandidates', [])
    result['evidenceState'] = {
        'modelVersion': MODEL_VERSION, 'status': 'baseline_ready' if baseline.get('benchmark') else 'partial',
        'lastCheckedAt': captured_at, 'candidateCount': len(result['evidenceCandidates']),
        'errors': errors[:12], 'automaticConclusion': False,
    }
    return result


def _return_pct(current, base):
    current_value, base_value = _number(current), _number(base)
    if current_value is None or base_value in (None, 0):
        return None
    return round((current_value / base_value - 1) * 100, 2)


def _candidate_id(*parts):
    payload = '|'.join(_text(part, 300) for part in parts)
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()[:20]


def _knowable_in_window(published, created_at, checked_at):
    """Require evidence to have become knowable after creation and not in the future."""
    text = _text(published, 80)
    if not text:
        return True
    if len(text) <= 10:
        # A date without time cannot prove it was published after a same-day creation.
        return _text(created_at, 80)[:10] < text <= _text(checked_at, 80)[:10]
    try:
        moment = datetime.fromisoformat(text.replace('Z', '+00:00'))
        created = datetime.fromisoformat(_iso(created_at))
        checked = datetime.fromisoformat(_iso(checked_at))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=BJC)
        return created <= moment.astimezone(BJC) <= checked
    except ValueError:
        return False


def _merge(existing, incoming):
    rows = {str(row.get('id')): dict(row) for row in (existing or [])
            if isinstance(row, dict) and row.get('id')}
    for row in incoming:
        previous = rows.get(row['id']) or {}
        row['firstObservedAt'] = previous.get('firstObservedAt') or row.get('observedAt')
        rows[row['id']] = row
    merged = list(rows.values())
    merged.sort(key=lambda row: (row.get('knowableAt') or '', row.get('id') or ''), reverse=True)
    return merged[:MAX_CANDIDATES]


def collect_candidate_evidence(item, quote_loader, benchmark_loader, disclosure_loader, now=None):
    """Append observations. First call only freezes the point-in-time baseline."""
    if not isinstance(item, dict) or not item.get('id'):
        raise ValueError('hypothesis is required')
    if not isinstance(item.get('marketBaseline'), dict):
        return capture_market_baseline(item, quote_loader, benchmark_loader, now)
    result = dict(item)
    checked_at = _iso(now)
    day = checked_at[:10]
    errors, incoming = [], []
    market_baseline = item.get('marketBaseline') or {}
    baseline_benchmark = market_baseline.get('benchmark') or {}
    current_benchmark = None
    benchmark_return = None
    try:
        current_benchmark = _benchmark(benchmark_loader())
        benchmark_return = _return_pct(current_benchmark.get('price'), baseline_benchmark.get('price'))
        incoming.append({
            'id': 'market:' + _candidate_id(item['id'], day, 'benchmark'),
            'kind': 'market_context', 'label': '同期市场基准',
            'knowableAt': checked_at, 'observedAt': checked_at,
            'source': current_benchmark.get('source'),
            'facts': ['%s 相对基线变动 %s%%' % (
                current_benchmark.get('name') or '市场基准',
                '--' if benchmark_return is None else ('%+.2f' % benchmark_return))],
            'metrics': {'benchmarkCode': current_benchmark.get('code'),
                        'benchmarkName': current_benchmark.get('name'),
                        'baselinePrice': baseline_benchmark.get('price'),
                        'currentPrice': current_benchmark.get('price'),
                        'benchmarkReturnPct': benchmark_return},
            'interpretation': '这是同期大盘背景，用于避免把市场共同波动误判为事件影响。',
        })
    except Exception as exc:
        errors.append('benchmark: ' + _text(exc, 180))

    baseline_quotes = {str(row.get('code')): row for row in (market_baseline.get('watchlist') or [])
                       if isinstance(row, dict) and row.get('code')}
    for watch in ((item.get('baseline') or {}).get('watchlist') or [])[:8]:
        code = _text((watch or {}).get('code'), 20)
        base_quote = baseline_quotes.get(code) or {}
        if not code or not base_quote:
            continue
        try:
            quote = quote_loader(code) or {}
            stock_return = _return_pct(quote.get('price'), base_quote.get('price'))
            excess = (round(stock_return - benchmark_return, 2)
                      if stock_return is not None and benchmark_return is not None else None)
            incoming.append({
                'id': 'relative:' + _candidate_id(item['id'], day, code),
                'kind': 'relative_performance', 'label': (quote.get('name') or watch.get('name') or code) + ' 相对表现',
                'knowableAt': checked_at, 'observedAt': checked_at,
                'source': _source(quote),
                'facts': [
                    '标的相对基线变动 %s%%' % ('--' if stock_return is None else ('%+.2f' % stock_return)),
                    '同期基准变动 %s%%' % ('--' if benchmark_return is None else ('%+.2f' % benchmark_return)),
                    '相对基准差值 %s 个百分点' % ('--' if excess is None else ('%+.2f' % excess)),
                ],
                'metrics': {'code': code, 'name': quote.get('name') or watch.get('name'),
                            'baselinePrice': base_quote.get('price'), 'currentPrice': _number(quote.get('price')),
                            'stockReturnPct': stock_return, 'benchmarkReturnPct': benchmark_return,
                            'excessReturnPct': excess,
                            'benchmarkCode': (current_benchmark or baseline_benchmark).get('code')},
                'interpretation': '相对表现仅是候选证据；差值本身不能证明事件因果。',
            })
        except Exception as exc:
            errors.append('%s: %s' % (code, _text(exc, 160)))

        try:
            disclosures = disclosure_loader(code) or {}
            for row in (disclosures.get('items') or [])[:12]:
                published = _text(row.get('published_at') or row.get('date'), 80)
                if not _knowable_in_window(published, item.get('createdAt'), checked_at):
                    continue
                source = disclosures.get('source') or {}
                incoming.append({
                    'id': 'official:' + _candidate_id(item['id'], code, row.get('id') or row.get('pdf_url') or row.get('title')),
                    'kind': 'official_disclosure', 'label': _text(row.get('title'), 300) or '官方披露',
                    'knowableAt': published or checked_at, 'observedAt': checked_at,
                    'source': {'id': _text(source.get('id'), 60) or 'cninfo',
                               'name': _text(source.get('name'), 100) or '巨潮资讯',
                               'tier': 'official',
                               'url': _text(row.get('pdf_url') or row.get('official_url'), 500) or None},
                    'facts': ['%s 发布官方公告：%s' % (row.get('date') or published[:10], row.get('title') or '未命名公告')],
                    'metrics': {'code': code, 'announcementId': _text(row.get('id'), 100)},
                    'interpretation': '官方披露是可核验事实，是否与原事件有关仍需逐条判断。',
                })
        except Exception as exc:
            errors.append('%s disclosure: %s' % (code, _text(exc, 140)))

    merged = _merge(item.get('evidenceCandidates'), incoming)
    result['evidenceCandidates'] = merged
    result['evidenceState'] = {
        'modelVersion': MODEL_VERSION,
        'status': 'ok' if not errors else ('partial' if incoming else 'unavailable'),
        'lastCheckedAt': checked_at, 'candidateCount': len(merged),
        'errors': errors[:12], 'automaticConclusion': False,
    }
    result['evidenceContract'] = {
        'candidateOnly': True, 'pointInTime': True, 'benchmarkAdjusted': True,
        'causalClaim': False, 'automaticOutcome': False, 'automaticTradingAction': False,
        'userReviewRequired': True,
    }
    return result
