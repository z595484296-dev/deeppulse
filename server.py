#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
深脉 DeepPulse · 金融工作台 — 本地数据服务
====================================================
由 DeepSeek 为自己打造的「身体」的后端神经中枢。

· 零第三方依赖（仅 Python 标准库）
· 行情数据源：通达信 TQ-Local（可选本地增强）→ 东方财富 → 腾讯备援
· 官方披露源：巨潮资讯结构化公告；上交所、深交所、证监会作为一级查验入口
· 内置：情绪周期策略引擎（emotion.py）、每日情绪快照记忆（data/history.json）
· 内置：多级缓存 + 上游限频，礼貌访问上游

启动：python server.py
（自动在 8971~8980 中选择可用端口，并将实际端口写入 data/port.txt）
"""

import json
import hashlib
import hmac
import io
import importlib
import importlib.util
import os
import re
import secrets
import socket
import struct
import sys
import threading
import time
import urllib.request
import urllib.parse
import zipfile
from datetime import datetime, timezone, timedelta, date as _date
from http.client import RemoteDisconnected
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    import tdx_local as tdx_local_api
except Exception:
    tdx_local_api = None

try:
    from event_impact import build_event_impact, MODEL_VERSION as EVENT_IMPACT_MODEL_VERSION
except Exception:
    build_event_impact = None
    EVENT_IMPACT_MODEL_VERSION = 'event-impact-unavailable'

try:
    from attention_triage import (build_attention_triage,
                                  MODEL_VERSION as ATTENTION_TRIAGE_MODEL_VERSION)
except Exception:
    build_attention_triage = None
    ATTENTION_TRIAGE_MODEL_VERSION = 'attention-triage-unavailable'

try:
    from research_hypothesis import (create_hypothesis, review_hypothesis,
                                     hypothesis_snapshot,
                                     MODEL_VERSION as HYPOTHESIS_MODEL_VERSION)
except Exception:
    create_hypothesis = review_hypothesis = hypothesis_snapshot = None
    HYPOTHESIS_MODEL_VERSION = 'research-hypothesis-unavailable'

try:
    from research_memory import (build_snapshot as build_research_memory_snapshot,
                                 normalize_preferences as normalize_research_memory_preferences,
                                 MODEL_VERSION as RESEARCH_MEMORY_MODEL_VERSION)
except Exception:
    build_research_memory_snapshot = normalize_research_memory_preferences = None
    RESEARCH_MEMORY_MODEL_VERSION = 'research-memory-unavailable'

try:
    from research_workflow import (attach_workflow_lineage, create_workflow, mutate_workflow, preview_workflow,
                                   record_run as record_workflow_run, workflow_snapshot,
                                   MODEL_VERSION as RESEARCH_WORKFLOW_MODEL_VERSION)
except Exception:
    attach_workflow_lineage = create_workflow = mutate_workflow = preview_workflow = None
    record_workflow_run = workflow_snapshot = None
    RESEARCH_WORKFLOW_MODEL_VERSION = 'research-workflow-unavailable'

try:
    from research_suggestions import (build_snapshot as build_research_suggestion_snapshot,
                                      draft_fingerprint as research_suggestion_draft_fingerprint,
                                      mutate_item as mutate_research_suggestion_item,
                                      MODEL_VERSION as RESEARCH_SUGGESTION_MODEL_VERSION)
except Exception:
    build_research_suggestion_snapshot = research_suggestion_draft_fingerprint = None
    mutate_research_suggestion_item = None
    RESEARCH_SUGGESTION_MODEL_VERSION = 'research-suggestions-unavailable'

try:
    from akshare_research import (build_snapshot as build_akshare_research_snapshot,
                                  unloaded_snapshot as unloaded_akshare_research_snapshot,
                                  normalize_pack_ids as normalize_akshare_pack_ids,
                                  pack_catalog as akshare_pack_catalog,
                                  MODEL_VERSION as AKSHARE_RESEARCH_MODEL_VERSION)
except Exception:
    build_akshare_research_snapshot = unloaded_akshare_research_snapshot = None
    normalize_akshare_pack_ids = akshare_pack_catalog = None
    AKSHARE_RESEARCH_MODEL_VERSION = 'akshare-research-unavailable'

try:
    from hypothesis_evidence import (collect_candidate_evidence,
                                     MODEL_VERSION as HYPOTHESIS_EVIDENCE_MODEL_VERSION)
except Exception:
    collect_candidate_evidence = None
    HYPOTHESIS_EVIDENCE_MODEL_VERSION = 'hypothesis-evidence-unavailable'

_akshare_module = None
_akshare_error = None

# ---------------------------------------------------------------- 基础配置
BASE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(BASE, 'web')
DATA = os.path.join(BASE, 'data')
HISTORY_FILE = os.path.join(DATA, 'history.json')
PROFILE_FILE = os.path.join(DATA, 'profile.json')
SECTOR_HISTORY_FILE = os.path.join(DATA, 'sector_history.json')
DEVICE_CONFIG_FILE = os.path.join(DATA, 'device_config.json')
PORT_FILE = os.path.join(DATA, 'port.txt')
LOG_FILE = os.path.join(DATA, 'server.log')
DIAGNOSTICS_HISTORY_FILE = os.path.join(DATA, 'diagnostics_history.json')
os.makedirs(DATA, exist_ok=True)

BJC = timezone(timedelta(hours=8))  # 北京时间（A股时区）

UA_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/126.0 Safari/537.36',
    'Accept': '*/*',
    'Referer': 'https://quote.eastmoney.com/',
}

EM_UT = '7eea3edcaed734bea9cbfc24409ed989'  # 东财公开 token
TDX_ENABLED = os.environ.get('DEEPPULSE_TDX_ENABLED', '1').strip().lower() not in ('0', 'false', 'off')
TDX_HOST = '127.0.0.1:17709'
VERSION = '1.30.0'

_desktop_heartbeat_lock = threading.Lock()
_desktop_heartbeat = {
    'last_seen': None,
    'app_version': None,
    'product_version': None,
    'service_ownership': None,
    'process_lifetime_protected': None,
}
_diagnostics_history_lock = threading.Lock()

try:
    from emotion import (compute_emotion, DEFAULT_WEIGHTS, load_weights,  # 情绪引擎
                         save_weights, INDICATORS)
except Exception:  # 引擎不可用时降级
    compute_emotion = None
    DEFAULT_WEIGHTS = {}
    INDICATORS = []
    load_weights = lambda: {}
    save_weights = lambda w: {}

# ---------------------------------------------------------------- 工具函数

class DeepPulseHTTPServer(ThreadingHTTPServer):
    """Avoid Windows' permissive HTTPServer port sharing semantics."""

    allow_reuse_address = False


def port_is_listening(host, port, timeout=0.2):
    """Return True when another local service already accepts connections."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.settimeout(timeout)
        return sock.connect_ex((host, port)) == 0
    finally:
        sock.close()


def log(msg):
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write('[%s] %s\n' % (now_bj().strftime('%Y-%m-%d %H:%M:%S'), msg))
    except Exception:
        pass


def now_bj():
    return datetime.now(BJC)


def today_str():
    return now_bj().strftime('%Y-%m-%d')


# 上游请求限频：全局串行（同一时刻只发一个上游请求，至少间隔 0.2s）
# ——实测并行突发会触发上游限流丢包，串行礼貌访问最稳（2026-08-15 夜测结论）
_host_lock = threading.Lock()
_last_req = {}
_source_lock = threading.Lock()
_source_stats = {}


SOURCE_CATALOG = [
    {
        'id': 'tdx_local', 'name': '通达信 TQ-Local', 'tier': 'local',
        'role': '本机实时行情、K线与市场情绪统计交叉验证（严格只读）',
        'homepage': 'https://help.tdx.com.cn/quant/',
        'hosts': [TDX_HOST], 'mode': 'local',
    },
    {
        'id': 'cninfo', 'name': '巨潮资讯', 'tier': 'official',
        'role': '上市公司法定信息披露与公告原文',
        'homepage': 'https://www.cninfo.com.cn/new/index',
        'hosts': ['www.cninfo.com.cn'], 'mode': 'live',
    },
    {
        'id': 'sse', 'name': '上海证券交易所', 'tier': 'official',
        'role': '沪市上市公司公告查验入口',
        'homepage': 'https://www.sse.com.cn/disclosure/listedinfo/announcement/',
        'hosts': ['www.sse.com.cn'], 'mode': 'reference',
    },
    {
        'id': 'szse', 'name': '深圳证券交易所', 'tier': 'official',
        'role': '深市上市公司公告查验入口',
        'homepage': 'https://www.szse.cn/disclosure/notice/company/index.html',
        'hosts': ['www.szse.cn'], 'mode': 'reference',
    },
    {
        'id': 'csrc', 'name': '中国证监会', 'tier': 'official',
        'role': '监管政策、行政许可与监管信息查验入口',
        'homepage': 'https://www.csrc.gov.cn/',
        'hosts': ['www.csrc.gov.cn'], 'mode': 'reference',
    },
    {
        'id': 'eastmoney', 'name': '东方财富', 'tier': 'market',
        'role': '实时行情、K线、资金流、涨跌停池与市场快讯',
        'homepage': 'https://quote.eastmoney.com/',
        'hosts': ['push2.eastmoney.com', 'push2delay.eastmoney.com',
                  'push2his.eastmoney.com', 'push2ex.eastmoney.com',
                  'newsapi.eastmoney.com'],
        'mode': 'live',
    },
    {
        'id': 'tencent', 'name': '腾讯行情', 'tier': 'market',
        'role': '个股行情与K线备援',
        'homepage': 'https://gu.qq.com/',
        'hosts': ['qt.gtimg.cn', 'web.ifzq.gtimg.cn'], 'mode': 'fallback',
    },
    {
        'id': 'akshare', 'name': 'AKShare', 'tier': 'enrichment',
        'role': '交易日历、宏观与跨市场公开数据补充；不作为实时行情或官方披露主源',
        'homepage': 'https://akshare.akfamily.xyz/',
        'hosts': [], 'mode': 'optional_local',
    },
]


def load_akshare():
    global _akshare_module, _akshare_error
    if _akshare_module is not None:
        return _akshare_module
    try:
        _akshare_module = importlib.import_module('akshare')
        _akshare_error = None
        return _akshare_module
    except Exception as exc:
        _akshare_error = str(exc)[:200]
        return None


def akshare_trade_dates():
    module = load_akshare()
    if module is None or not hasattr(module, 'tool_trade_date_hist_sina'):
        raise RuntimeError('AKShare 交易日历接口不可用')
    started = time.monotonic()
    try:
        frame = module.tool_trade_date_hist_sina()
        values = frame['trade_date'].tolist()
        dates = {str(value)[:10] for value in values}
        _record_source('akshare:tool_trade_date_hist_sina', True,
                       (time.monotonic() - started) * 1000)
        return dates
    except Exception as exc:
        _record_source('akshare:tool_trade_date_hist_sina', False,
                       (time.monotonic() - started) * 1000, exc)
        raise


def market_calendar_info(current=None):
    value = (current or now_bj()).astimezone(BJC)
    day = value.strftime('%Y-%m-%d')
    if value.weekday() >= 5:
        return {'date': day, 'is_trade_date': False, 'confirmed': True,
                'basis': 'weekend', 'source': 'system-calendar'}
    if importlib.util.find_spec('akshare') is not None:
        try:
            dates = cached('akshare_trade_calendar', 12 * 3600, akshare_trade_dates)
            return {'date': day, 'is_trade_date': day in dates, 'confirmed': True,
                    'basis': 'AKShare 交易日历', 'source': 'akshare'}
        except Exception as exc:
            return {'date': day, 'is_trade_date': True, 'confirmed': False,
                    'basis': '工作日降级判断', 'source': 'system-calendar',
                    'error': str(exc)[:160]}
    return {'date': day, 'is_trade_date': True, 'confirmed': False,
            'basis': '工作日降级判断', 'source': 'system-calendar',
            'error': 'AKShare 未安装'}


def akshare_status(probe=False):
    installed = importlib.util.find_spec('akshare') is not None
    module = load_akshare() if probe and installed else _akshare_module
    status = 'not_installed' if not installed else 'unobserved'
    calendar = None
    if probe and module is not None:
        calendar = market_calendar_info()
        status = 'ok' if calendar.get('confirmed') else 'degraded'
    else:
        with _source_lock:
            observations = [dict(value) for key, value in _source_stats.items()
                            if key.startswith('akshare:')]
            observation = max(observations, key=lambda row: row.get('last_at') or '') if observations else {}
        if observation:
            status = 'ok' if observation.get('ok') else 'degraded'
    return {
        'installed': installed,
        'version': str(getattr(module, '__version__', '') or '') or None,
        'status': status,
        'calendar': calendar,
        'role': '补充层：交易日历、宏观与跨市场公开数据；不提升为官方或实时主源',
        'interfaces': {
            'trade_calendar': bool(module and hasattr(module, 'tool_trade_date_hist_sina')),
            'macro_calendar': bool(module and hasattr(module, 'macro_info_ws')),
            'macro_corroboration': bool(module and hasattr(module, 'news_economic_baidu')),
            'stock_news': bool(module and hasattr(module, 'stock_news_em')),
            'research_snapshot': bool(module and build_akshare_research_snapshot),
        },
        'event_service': load_event_service_config() if 'load_event_service_config' in globals() else None,
        'error': _akshare_error,
    }


def _record_source(host, ok, latency_ms, error=''):
    """记录最近一次真实上游访问；状态页只展示观测事实，不主动探测或伪报在线。"""
    with _source_lock:
        prev = _source_stats.get(host, {})
        failures = 0 if ok else int(prev.get('failures') or 0) + 1
        _source_stats[host] = {
            'ok': bool(ok), 'latency_ms': int(latency_ms),
            'last_at': now_bj().isoformat(timespec='seconds'),
            'last_ok': now_bj().isoformat(timespec='seconds') if ok else prev.get('last_ok'),
            'failures': failures,
            'error': '' if ok else str(error)[:160],
        }


class UpstreamError(Exception):
    pass


def fetch(url, timeout=9, encoding='utf-8', raw=False, retry=2, referer=None):
    """带限频与重试的上游 GET。返回文本（或 raw 时返回 bytes）。
    上游偶发断开连接（RemoteDisconnected）时放慢重试，礼貌而坚韧。"""
    host = urllib.parse.urlparse(url).netloc
    for attempt in range(retry + 1):
        with _host_lock:
            wait = 0.2 - (time.monotonic() - _last_req.get(host, 0))
            if wait > 0:
                time.sleep(wait)
            _last_req[host] = time.monotonic()
        req = urllib.request.Request(url)
        for k, v in UA_HEADERS.items():
            req.add_header(k, v)
        if referer:
            req.add_header('Referer', referer)
        started = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read()
            _record_source(host, True, (time.monotonic() - started) * 1000)
            if raw:
                return body
            return body.decode(encoding, 'replace')
        except RemoteDisconnected:
            time.sleep(1.2 * (attempt + 1))  # 上游断连：逐步放慢再试
        except Exception as e:
            if attempt >= retry:
                _record_source(host, False, (time.monotonic() - started) * 1000, e)
                raise UpstreamError('%s: %s' % (host, e))
            time.sleep(0.5 * (attempt + 1))
    raise UpstreamError(host + ': remote disconnected')


def json_loads(text):
    """容忍 JSONP 包装（如 var ajaxResult={...};）"""
    t = text.strip()
    if t.startswith('var '):
        i = t.find('=')
        t = t[i + 1:].strip()
    t = t.rstrip(';').strip()
    return json.loads(t)


def source_catalog():
    """返回来源分级与最近观测状态；未被请求的查验入口保持“未观测”。"""
    with _source_lock:
        stats = dict(_source_stats)
    now_mono = time.monotonic()
    items = []
    for src in SOURCE_CATALOG:
        observations = [stats[h] for h in src['hosts'] if h in stats]
        last = max(observations, key=lambda x: x.get('last_at') or '') if observations else None
        circuit = 0
        if '_host_down' in globals():
            circuit = max([max(0, int(_host_down.get(h, 0) - now_mono))
                           for h in src['hosts']] or [0])
        environment = None
        if src['id'] == 'tdx_local':
            if not TDX_ENABLED:
                status = 'disabled'
            elif not tdx_local_api:
                status = 'unavailable'
            else:
                environment = tdx_local_api.environment_status()
                env_status = environment.get('status')
                if env_status in ('unsupported', 'not_installed', 'not_running'):
                    status = env_status
                elif last is not None:
                    status = 'ok' if last.get('ok') else 'unavailable'
                else:
                    status = 'unobserved'
        elif src['id'] == 'akshare':
            environment = akshare_status(probe=False)
            status = environment['status']
            akshare_observations = [value for key, value in stats.items()
                                    if key.startswith('akshare:')]
            if akshare_observations:
                last = max(akshare_observations,
                           key=lambda row: row.get('last_at') or '')
        elif src['mode'] == 'reference':
            status = 'reference'
        elif circuit:
            status = 'degraded'
        elif last is None:
            status = 'unobserved'
        else:
            status = 'ok' if last.get('ok') else 'degraded'
        item = dict(src)
        item.update({
            'status': status,
            'last_observed': last.get('last_at') if last else None,
            'last_ok': last.get('last_ok') if last else None,
            'latency_ms': last.get('latency_ms') if last else None,
            'failures': last.get('failures') if last else 0,
            'latest_ok': bool(last.get('ok')) if last else None,
            'circuit_seconds': circuit,
        })
        if environment is not None:
            item['environment'] = environment
        items.append(item)
    return {
        'generated_at': now_bj().isoformat(timespec='seconds'),
        'items': items,
        'policy': '官方披露优先；通达信用于本地只读行情增强；AKShare 只作交易日历、宏观与跨市场补充；市场聚合用于备援与线索；未观测不等于可用。',
    }


# ---------------------------------------------------------------- 缓存

_cache_lock = threading.Lock()
_cache = {}


def cached(key, ttl, fn):
    """TTL 内存缓存：key -> (expire_ts, value)"""
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and hit[0] > now:
            return hit[1]
    value = fn()
    with _cache_lock:
        _cache[key] = (now + ttl, value)
    return value


def cache_drop(prefix):
    with _cache_lock:
        for k in [k for k in _cache if k.startswith(prefix)]:
            del _cache[k]


def normalize_akshare_research_preferences(value=None):
    raw = value if isinstance(value, dict) else {}
    selected = raw.get('enabledPacks')
    if normalize_akshare_pack_ids:
        selected = normalize_akshare_pack_ids(selected)
    else:
        selected = ['growth', 'prices', 'liquidity', 'rates']
    return {
        'modelVersion': 'akshare-research-preferences-v1',
        'enabledPacks': selected,
        'manualOnly': True,
        'includedInEmotionScore': False,
        'automaticTradingAction': False,
    }


def load_akshare_research_preferences():
    profile = load_profile().get('data') or {}
    return normalize_akshare_research_preferences(profile.get('akshare_research_preferences'))


def save_akshare_research_preferences(value):
    preferences = normalize_akshare_research_preferences(value)
    saved = save_profile({'akshare_research_preferences': preferences})
    cache_drop('akshare_research_snapshot_v2')
    return {'profile': saved, 'preferences': preferences,
            'catalog': akshare_pack_catalog() if akshare_pack_catalog else []}


def _akshare_interface_health(interface_names):
    with _source_lock:
        stats = dict(_source_stats)
    rows = []
    for name in interface_names:
        observation = stats.get('akshare:' + str(name)) or {}
        rows.append({
            'interface': str(name),
            'status': ('ok' if observation.get('ok') is True else
                       'failed' if observation.get('ok') is False else 'unobserved'),
            'lastObserved': observation.get('last_at'), 'lastOk': observation.get('last_ok'),
            'latencyMs': observation.get('latency_ms'),
            'failures': int(observation.get('failures') or 0),
        })
    return rows


def akshare_research_snapshot(refresh=False, selected_packs=None):
    """Return a cached, on-demand snapshot for user-selected research packs."""
    preferences = load_akshare_research_preferences()
    selected = (normalize_akshare_pack_ids(selected_packs)
                if normalize_akshare_pack_ids and selected_packs is not None
                else preferences['enabledPacks'])
    module = load_akshare()
    version = str(getattr(module, '__version__', '') or '') if module else ''
    if module is None or build_akshare_research_snapshot is None:
        if unloaded_akshare_research_snapshot:
            return unloaded_akshare_research_snapshot(False, version, selected)
        return {'modelVersion': AKSHARE_RESEARCH_MODEL_VERSION, 'status': 'unavailable',
                'modules': [], 'errors': [{'interface': 'akshare', 'error': _akshare_error or '不可用'}],
                'includedInEmotionScore': False, 'automaticTradingAction': False}

    key = 'akshare_research_snapshot_v2:' + ','.join(selected)
    if refresh:
        cache_drop(key)
    else:
        with _cache_lock:
            hit = _cache.get(key)
            if hit and hit[0] > time.monotonic():
                return hit[1]
        return unloaded_akshare_research_snapshot(True, version, selected)

    def fetcher(name, **kwargs):
        fn = getattr(module, name, None)
        if not callable(fn):
            raise RuntimeError('%s 接口不可用' % name)
        started = time.monotonic()
        try:
            value = fn(**kwargs)
            _record_source('akshare:' + name, True, (time.monotonic() - started) * 1000)
            return value
        except Exception as exc:
            _record_source('akshare:' + name, False, (time.monotonic() - started) * 1000, exc)
            raise

    def loader():
        snapshot = build_akshare_research_snapshot(fetcher, version, now_bj(), selected)
        snapshot['interfaceHealth'] = _akshare_interface_health(snapshot.get('interfacesRequested') or [])
        snapshot['preferences'] = preferences
        return snapshot

    return cached(key, 6 * 3600, loader)


# ---------------------------------------------------------------- 可选本地增强：通达信 TQ-Local（只读）

def tdx_status(probe=False, fresh=False):
    """返回通达信环境状态；probe=True 时按技能约束完成本地服务探测。"""
    if not TDX_ENABLED:
        return {
            'supported': sys.platform == 'win32', 'installed': False,
            'process_running': False, 'service_ready': False,
            'status': 'disabled', 'read_only': True,
        }
    if not tdx_local_api:
        return {
            'supported': sys.platform == 'win32', 'installed': False,
            'process_running': False, 'service_ready': False,
            'status': 'unavailable', 'error': 'tdx_local adapter unavailable',
            'read_only': True,
        }
    if not probe:
        return tdx_local_api.environment_status()
    if fresh:
        cache_drop('tdx_status_probe')

    def loader():
        status = tdx_local_api.probe_status()
        _record_source(TDX_HOST, status.get('service_ready', False),
                       status.get('latency_ms') or 0, status.get('error') or '')
        return status
    return cached('tdx_status_probe', 10, loader)


def _tdx_require_ready():
    status = tdx_status(probe=True)
    if status.get('service_ready'):
        _clear_host_down(TDX_HOST)
        return
    if not _host_ok(TDX_HOST):
        raise UpstreamError('通达信 TQ-Local 暂时熔断')
    raise UpstreamError(status.get('error') or ('通达信状态：' + status.get('status', 'unavailable')))


def tdx_read_quote(code):
    _tdx_require_ready()
    started = time.monotonic()
    try:
        data = tdx_local_api.quote(code)
        _record_source(TDX_HOST, True, data.get('latency_ms') or
                       (time.monotonic() - started) * 1000)
        return data
    except Exception as e:
        _record_source(TDX_HOST, False, (time.monotonic() - started) * 1000, e)
        _mark_host_down(TDX_HOST, 30)
        raise UpstreamError(str(e))


def tdx_read_kline(code, n=320, klt=101, fqt=1, explicit_code=None):
    _tdx_require_ready()
    started = time.monotonic()
    try:
        data = tdx_local_api.kline(code, n, klt, fqt, explicit_code=explicit_code)
        _record_source(TDX_HOST, True, data.get('latency_ms') or
                       (time.monotonic() - started) * 1000)
        return data
    except Exception as e:
        _record_source(TDX_HOST, False, (time.monotonic() - started) * 1000, e)
        _mark_host_down(TDX_HOST, 30)
        raise UpstreamError(str(e))


def tdx_emotion_verification():
    _tdx_require_ready()
    started = time.monotonic()
    try:
        data = tdx_local_api.emotion_snapshot()
        _record_source(TDX_HOST, True, data.get('latency_ms') or
                       (time.monotonic() - started) * 1000)
        return data
    except Exception as e:
        # ErrorId 表示服务在线但当前客户端未提供该专业数据（常见于权限或本地数据未准备）。
        # 这不应熔断仍然可用的实时行情和 K 线能力。
        if 'ErrorId=' in str(e):
            return {
                'status': 'unavailable', 'source': 'tdx_local',
                'source_name': '通达信 TQ-Local', 'read_only': True,
                'fields': {}, 'error': str(e)[:240],
                'reason': 'professional_market_data_unavailable',
            }
        _record_source(TDX_HOST, False, (time.monotonic() - started) * 1000, e)
        _mark_host_down(TDX_HOST, 30)
        raise UpstreamError(str(e))


# ---------------------------------------------------------------- 工具：代码/证券ID

def normalize_code(code):
    """把 sh600519 / SZ000001 / 600519.SH 等归一为纯数字代码"""
    c = code.strip().lower()
    c = re.sub(r'^(sh|sz|bj)\s*', '', c)
    c = re.sub(r'\.(sh|sz|bj)$', '', c)
    c = re.sub(r'[^0-9]', '', c)
    return c


def secid_of(code):
    """数字代码 -> 东财 secid（1.沪 / 0.深）"""
    if code[:1] in ('6', '5', '9'):
        return '1.' + code
    return '0.' + code


def tq_code_of(code):
    """数字代码 -> 腾讯代码（sh/sz 前缀）"""
    return ('sh' if code[:1] in ('6', '5', '9') else 'sz') + code


# ---------------------------------------------------------------- 主数据源：东方财富

def em_indices(host='push2.eastmoney.com'):
    url = ('https://%s/api/qt/ulist.np/get?fltt=2&invt=2'
           '&secids=1.000001,0.399001,0.399006,1.000688,0.899050'
           '&fields=f2,f3,f4,f6,f12,f14' % host)
    j = json_loads(fetch(url))
    out = []
    for d in (j.get('data') or {}).get('diff') or []:
        out.append({
            'code': d.get('f12'), 'name': d.get('f14'),
            'price': d.get('f2'), 'pct': d.get('f3'), 'chg': d.get('f4'),
            'amount': d.get('f6') or 0,
        })
    return out


def em_indices_any():
    """指数实时行情（解析格式），push2 → push2delay 故障切换"""
    last = None
    for host in ('push2.eastmoney.com', 'push2delay.eastmoney.com'):
        if not _host_ok(host):
            continue
        try:
            url = ('https://%s/api/qt/ulist.np/get?fltt=2&invt=2'
                   '&secids=1.000001,0.399001,0.399006,1.000688,0.899050'
                   '&fields=f2,f3,f4,f6,f12,f14' % host)
            j = json_loads(fetch(url))
            out = []
            for d in (j.get('data') or {}).get('diff') or []:
                out.append({
                    'code': d.get('f12'), 'name': d.get('f14'),
                    'price': d.get('f2'), 'pct': d.get('f3'), 'chg': d.get('f4'),
                    'amount': d.get('f6') or 0,
                })
            return out
        except Exception as e:
            last = e
            _mark_host_down(host)
    raise last if last else UpstreamError('indices unavailable')


def em_ulist_any(secids, fields):
    """ulist 批量行情（原始 diff 行），push2 → push2delay 故障切换"""
    last = None
    for host in ('push2.eastmoney.com', 'push2delay.eastmoney.com'):
        if not _host_ok(host):
            continue
        try:
            url = ('https://%s/api/qt/ulist.np/get?fltt=2&invt=2&secids=%s&fields=%s'
                   % (host, secids, fields))
            j = json_loads(fetch(url))
            return (j.get('data') or {}).get('diff') or []
        except Exception as e:
            last = e
            _mark_host_down(host)
    raise last if last else UpstreamError('ulist unavailable')


def em_quote(secid, host='push2.eastmoney.com'):
    """个股实时行情。注意：本接口价格字段为 ×100（如 f43=134199 即 1341.99），
    已与腾讯行情交叉验证；涨停池的 p 字段则是 ×1000，两者不同源需分别处理。"""
    fields = 'f43,f44,f45,f46,f47,f48,f50,f57,f58,f60,f116,f117,f162,f167,f168,f169,f170'
    url = ('https://%s/api/qt/stock/get?secid=%s&fields=%s' % (host, secid, fields))
    j = json_loads(fetch(url))
    d = j.get('data') or {}
    if not d:
        raise UpstreamError('no quote data')
    return {
        'code': d.get('f57'), 'name': d.get('f58'),
        'price': (d.get('f43') or 0) / 100.0,
        'high': (d.get('f44') or 0) / 100.0,
        'low': (d.get('f45') or 0) / 100.0,
        'open': (d.get('f46') or 0) / 100.0,
        'volume': d.get('f47') or 0,            # 手
        'amount': d.get('f48') or 0,            # 元
        'vol_ratio': (d.get('f50') or 0) / 100.0,   # 量比
        'prev_close': (d.get('f60') or 0) / 100.0,
        'mktcap': d.get('f116') or 0,
        'float_mktcap': d.get('f117') or 0,
        'pe': (d.get('f162') or 0) / 100.0,
        'pb': (d.get('f167') or 0) / 100.0,
        'turnover': (d.get('f168') or 0) / 100.0,  # 换手率 %
        'chg': (d.get('f169') or 0) / 100.0,
        'pct': (d.get('f170') or 0) / 100.0,
    }


def em_quote_any(secid):
    """多主机故障切换：push2 → push2delay（延迟镜像，限流时救急），带熔断"""
    last = None
    for host in ('push2.eastmoney.com', 'push2delay.eastmoney.com'):
        if not _host_ok(host):
            continue
        try:
            return em_quote(secid, host)
        except Exception as e:
            last = e
            _mark_host_down(host)
    raise last if last else UpstreamError('quote unavailable')


def em_kline(secid, klt=101, fqt=1, n=320, beg='20200101', host='push2his.eastmoney.com'):
    url = ('https://%s/api/qt/stock/kline/get?secid=%s'
           '&klt=%s&fqt=%s&beg=%s&end=20500101'
           '&fields1=f1,f2,f3,f4,f5,f6'
           '&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61'
           % (host, secid, klt, fqt, beg))
    j = json_loads(fetch(url))
    d = j.get('data') or {}
    rows = []
    for line in (d.get('klines') or []):
        p = line.split(',')
        if len(p) < 11:
            continue
        rows.append({
            'date': p[0], 'open': float(p[1]), 'close': float(p[2]),
            'high': float(p[3]), 'low': float(p[4]),
            'volume': float(p[5]), 'amount': float(p[6]),
            'amp': float(p[7]), 'pct': float(p[8]),
            'chg': float(p[9]), 'turn': float(p[10]),
        })
    if not rows:
        raise UpstreamError('empty kline from %s' % host)
    return {'name': d.get('name'), 'code': d.get('code'),
            'pre': d.get('preKPrice'), 'rows': rows[-n:]}


def em_kline_any(secid, klt=101, fqt=1, n=320, beg='20200101'):
    """K线多主机故障切换：push2his → push2"""
    last = None
    for host in ('push2his.eastmoney.com', 'push2.eastmoney.com'):
        if not _host_ok(host):
            continue
        try:
            return em_kline(secid, klt, fqt, n, beg, host)
        except Exception as e:
            last = e
            _mark_host_down(host)
    raise last if last else UpstreamError('kline unavailable')


# 上游主机熔断：某主机连续失败后，短时间内不再尝试，快速切换备援
_host_down = {}
_host_down_lock = threading.Lock()


def _host_ok(host):
    with _host_down_lock:
        return time.monotonic() >= _host_down.get(host, 0)


def _mark_host_down(host, secs=180):
    with _host_down_lock:
        _host_down[host] = time.monotonic() + secs
    log('circuit: %s marked down for %ds' % (host, secs))


def _clear_host_down(host):
    with _host_down_lock:
        _host_down.pop(host, None)


def em_pool(kind, date=None, size=250):
    """kind: ZT(涨停) / DT(跌停) / ZB(炸板)；date: YYYYMMDD。
    该接口必须携带 date 参数（缺省返回 rc=102）；休市日自动回退到最近交易日。
    注：pagesize 上限 250。"""
    size = min(size, 250)
    sort = 'fbt%3Aasc' if kind in ('ZT', 'ZB') else 'fund%3Aasc'

    def _get(d):
        url = ('https://push2ex.eastmoney.com/getTopic%sPool?ut=%s&dpt=wz.ztzt'
               '&Pageindex=0&pagesize=%d&sort=%s&date=%s'
               % (kind, EM_UT, size, sort, d))
        return json_loads(fetch(url))

    j = None
    if date:
        j = _get(date)
    if not j or not (j.get('data') or {}).get('pool'):
        # 回退：从今天往前找最近一个交易日（最多 10 天）
        day = datetime.strptime(date or now_bj().strftime('%Y%m%d'), '%Y%m%d')
        for i in range(1, 11):
            d = (day - timedelta(days=i)).strftime('%Y%m%d')
            try:
                j2 = _get(d)
                if (j2.get('data') or {}).get('pool'):
                    j = j2
                    break
            except Exception:
                continue
    d = (j or {}).get('data') or {}
    pool = []
    for it in (d.get('pool') or []):
        pool.append({
            'code': it.get('c'), 'name': it.get('n'),
            'price': (it.get('p') or 0) / 1000.0,
            'pct': round(it.get('zdp') or 0, 2),
            'amount': it.get('amount') or 0,
            'float_mktcap': it.get('ltsz') or 0,
            'turnover': round(it.get('hs') or 0, 2),
            'lbc': it.get('lbc') or 0,          # 连板数
            'fbt': it.get('fbt'),               # 首次封板时间 HHMMSS
            'lbt': it.get('lbt'),               # 最后封板时间
            'fund': it.get('fund') or 0,        # 封单资金
            'zbc': it.get('zbc') or 0,          # 炸板次数
            'days': it.get('days') or 0,        # 连续跌停天数(跌停池)
            'hybk': it.get('hybk') or '',       # 行业板块
            'zttj': it.get('zttj') or {},       # 涨停统计 {days, ct}
        })
    return {'qdate': str(d.get('qdate') or ''), 'total': d.get('tc') or len(pool),
            'pool': pool}


def em_breadth():
    url = ('https://push2ex.eastmoney.com/getTopicZDFenBu?ut=%s&dpt=wz.ztzt' % EM_UT)
    j = json_loads(fetch(url))
    d = j.get('data') or {}
    bins = {}
    for item in (d.get('fenbu') or []):
        bins.update(item)
    up = sum(v for k, v in bins.items() if int(k) > 0)
    down = sum(v for k, v in bins.items() if int(k) < 0)
    flat = bins.get('0', 0)
    limit_up = bins.get('11', 0)
    limit_down = bins.get('-11', 0)
    return {'qdate': str(d.get('qdate') or ''), 'bins': bins,
            'up': up, 'down': down, 'flat': flat, 'total': up + down + flat,
            'limit_up': limit_up, 'limit_down': limit_down}


def em_flow(secid, n=30, host='push2.eastmoney.com'):
    url = ('https://%s/api/qt/stock/fflow/kline/get?lmt=%d'
           '&klt=101&secid=%s&fields1=f1,f2,f3,f7'
           '&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63'
           % (host, n, secid))
    j = json_loads(fetch(url))
    rows = []
    for line in ((j.get('data') or {}).get('klines') or []):
        p = line.split(',')
        if len(p) < 6:
            continue
        rows.append({
            'date': p[0], 'main': float(p[1]), 'small': float(p[2]),
            'mid': float(p[3]), 'big': float(p[4]), 'super': float(p[5]),
            'close': float(p[11]) if len(p) > 11 else None,
            'pct': float(p[12]) if len(p) > 12 else None,
        })
    return rows


def em_rank(sort='f3', pz=30):
    fid = {'up': 'f3', 'flow': 'f62', 'turn': 'f8'}.get(sort, 'f3')
    fs = 'm:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23'
    url = ('https://%s/api/qt/clist/get?pn=1&pz=%d&po=1&np=1'
           '&fltt=2&invt=2&fid=%s&fs=%s&fields=f2,f3,f12,f14,f8,f62'
           % ('{HOST}', pz, fid, fs))
    j = json_loads(fetch_clist_any(url))
    out = []
    for d in ((j.get('data') or {}).get('diff') or []):
        out.append({'code': d.get('f12'), 'name': d.get('f14'),
                    'price': d.get('f2'), 'pct': d.get('f3'),
                    'turnover': d.get('f8'), 'main_flow': d.get('f62') or 0})
    return out


def em_sectors():
    url = ('https://%s/api/qt/clist/get?pn=1&pz=30&po=1&np=1'
           '&fltt=2&invt=2&fid=f3&fs=m:90+t:2+f:!50&fields=f2,f3,f12,f14'
           % '{HOST}')
    j = json_loads(fetch_clist_any(url))
    out = []
    for d in ((j.get('data') or {}).get('diff') or []):
        out.append({'code': d.get('f12'), 'name': d.get('f14'),
                    'price': d.get('f2'), 'pct': d.get('f3')})
    return out


def fetch_clist_any(url_tpl):
    """clist 类接口多主机故障切换（push2 → push2delay），url 中 {HOST} 为占位符"""
    last = None
    for host in ('push2.eastmoney.com', 'push2delay.eastmoney.com'):
        if not _host_ok(host):
            continue
        try:
            return fetch(url_tpl.replace('{HOST}', host))
        except Exception as e:
            last = e
            _mark_host_down(host)
    raise last if last else UpstreamError('clist unavailable')


def em_news():
    url = 'https://newsapi.eastmoney.com/kuaixun/v1/getlist_102_ajaxResult_30_1_.html'
    text = fetch(url)
    j = json_loads(text)
    out = []
    for it in ((j.get('LivesList') or [])):
        out.append({
            'time': (it.get('showtime') or '')[-8:],
            'title': it.get('title') or '',
            'url': it.get('url_w') or it.get('url_m') or '',
            'source_name': '东方财富快讯',
            'source_tier': 'market',
        })
    return out


_RISK_WORDS = ('重大', '风险', '处罚', '立案', '诉讼', '退市', '减持', '质押',
               '亏损', '更正', '终止', '会计政策')


def _plain_cninfo(value):
    text = re.sub(r'<[^>]+>', '', str(value or ''))
    return text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>').strip()


def cninfo_disclosures(code, page_size=8):
    """读取巨潮资讯官方公告原文索引；失败时抛错，由接口明确返回降级状态。"""
    code = normalize_code(code)
    if not code:
        raise ValueError('code required')
    page_size = max(1, min(int(page_size or 8), 20))
    params = urllib.parse.urlencode({
        'searchkey': code, 'sdate': '', 'edate': '', 'isfulltext': 'false',
        'sortName': 'pubdate', 'sortType': 'desc', 'pageNum': 1,
    })
    search_url = 'https://www.cninfo.com.cn/new/fulltextSearch?keyWord=' + urllib.parse.quote(code)
    api_url = 'https://www.cninfo.com.cn/new/fulltextSearch/full?' + params
    payload = json_loads(fetch(api_url, timeout=12, retry=1, referer=search_url))
    rows = []
    for item in (payload.get('announcements') or []):
        if normalize_code(item.get('secCode') or '') != code:
            continue
        title = _plain_cninfo(item.get('announcementTitle') or item.get('shortTitle'))
        published_ms = int(item.get('announcementTime') or 0)
        published = datetime.fromtimestamp(published_ms / 1000, BJC) if published_ms else None
        adjunct = str(item.get('adjunctUrl') or '').lstrip('/')
        rows.append({
            'id': str(item.get('announcementId') or ''),
            'code': code,
            'name': _plain_cninfo(item.get('secName')),
            'title': title,
            'date': published.strftime('%Y-%m-%d') if published else '',
            'published_at': published.isoformat(timespec='seconds') if published else None,
            'pdf_url': ('https://static.cninfo.com.cn/' + adjunct) if adjunct else search_url,
            'official_url': search_url,
            'focus': any(word in title for word in _RISK_WORDS),
            'source_id': 'cninfo',
            'source_name': '巨潮资讯',
            'source_tier': 'official',
        })
        if len(rows) >= page_size:
            break
    return {
        'items': rows,
        'total': int(payload.get('totalAnnouncement') or len(rows)),
        'query_url': search_url,
        'fetched_at': now_bj().isoformat(timespec='seconds'),
        'source': {
            'id': 'cninfo', 'name': '巨潮资讯', 'tier': 'official',
            'homepage': 'https://www.cninfo.com.cn/new/index',
        },
    }


_all_stocks = None
_all_stocks_lock = threading.Lock()


def em_all_stocks():
    global _all_stocks
    with _all_stocks_lock:
        if _all_stocks is not None:
            return _all_stocks
    url = ('https://%s/api/qt/clist/get?pn=1&pz=6500&po=1&np=1'
           '&fltt=2&invt=2&fid=f20&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23'
           '&fields=f12,f13,f14,f100' % '{HOST}')
    j = json_loads(fetch_clist_any(url))
    out = []
    for d in ((j.get('data') or {}).get('diff') or []):
        out.append({'code': d.get('f12'), 'market': d.get('f13'),
                    'name': d.get('f14'), 'industry': d.get('f100') or ''})
    with _all_stocks_lock:
        _all_stocks = out
    return out


# ---------------------------------------------------------------- 备援数据源：腾讯

def tq_quote(code):
    tcode = tq_code_of(code)
    body = fetch('https://qt.gtimg.cn/q=' + tcode, raw=True)
    text = body.decode('gbk', 'replace')
    m = re.search(r'="(.*)"', text)
    if not m:
        raise UpstreamError('tq parse fail')
    p = m.group(1).split('~')
    if len(p) < 50:
        raise UpstreamError('tq fields short')
    return {
        'code': code, 'name': p[1],
        'price': float(p[3] or 0),
        'prev_close': float(p[4] or 0),
        'open': float(p[5] or 0),
        'volume': float(p[6] or 0),
        'high': float(p[33] or 0),
        'low': float(p[34] or 0),
        'amount': float(p[37] or 0) * 10000,
        'turnover': float(p[38] or 0),
        'pe': float(p[39] or 0),
        'mktcap': float(p[45] or 0) * 10000,
        'float_mktcap': float(p[44] or 0) * 10000,
        'pb': float(p[46] or 0),
        'chg': float(p[31] or 0),
        'pct': float(p[32] or 0),
        'vol_ratio': 0,
    }


def tq_kline(code, n=320, klt=101, tcode=None):
    """腾讯 K 线备援（日/周/月）。指数需显式传 tcode（如 sh000001）。"""
    tcode = tcode or tq_code_of(code)
    period = {101: 'day', 102: 'week', 103: 'month'}.get(klt, 'day')
    url = ('https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=%s,%s,,,%d,qfq'
           % (tcode, period, n))
    j = json_loads(fetch(url))
    node = ((j.get('data') or {}).get(tcode) or {})
    arr = node.get('qfq' + period) or node.get(period) or []
    rows = []
    for p in arr:
        rows.append({'date': p[0], 'open': float(p[1]), 'close': float(p[2]),
                     'high': float(p[3]), 'low': float(p[4]),
                     'volume': float(p[5]), 'amount': 0.0, 'amp': 0.0,
                     'pct': 0.0, 'chg': 0.0, 'turn': 0.0})
    return {'name': code, 'code': code, 'pre': None, 'rows': rows}


def quote_with_fallback(code):
    try:
        return tdx_read_quote(code)
    except Exception:
        pass
    secid = secid_of(code)
    try:
        q = em_quote_any(secid)
        q['source'] = 'em'
        return q
    except Exception:
        q = tq_quote(code)
        q['source'] = 'tq'
        return q


def kline_with_fallback(code, klt=101, fqt=1, n=320):
    try:
        return tdx_read_kline(code, n, klt, fqt)
    except Exception:
        pass
    try:
        k = em_kline_any(secid_of(code), klt, fqt, n)
        k['source'] = 'em'
        return k
    except Exception:
        _mark_host_down('push2his.eastmoney.com')
        k = tq_kline(code, n, klt)
        k['source'] = 'tq'
        return k


def sh_index_kline(n=90):
    """上证指数日K（情绪引擎背景与量能基线），通达信→东财→腾讯。"""
    try:
        return tdx_read_kline('000001', n, 101, 1, explicit_code='999999.SH')
    except Exception:
        pass
    try:
        k = em_kline_any('1.000001', 101, 1, n)
        k['source'] = 'em'
        return k
    except Exception:
        _mark_host_down('push2his.eastmoney.com')
        k = tq_kline('000001', n, 101, tcode='sh000001')
        k['source'] = 'tq'
        return k


def previous_trade_date(today, rows):
    """Return the latest verified index trading date before YYYYMMDD."""
    if not re.fullmatch(r'\d{8}', str(today or '')):
        raise UpstreamError('premium: invalid current trading date')
    dates = set()
    for row in rows or []:
        value = str((row or {}).get('date') or '').replace('-', '')
        if re.fullmatch(r'\d{8}', value) and value < today:
            dates.add(value)
    if not dates:
        raise UpstreamError('premium: previous trading date unavailable')
    return max(dates)


def em_previous_limit_members():
    """Current members of Eastmoney's verified “昨日涨停” board (BK0815)."""
    url = ('https://%s/api/qt/clist/get?pn=1&pz=250&po=1&np=1'
           '&fltt=2&invt=2&fid=f3&fs=b:BK0815'
           '&fields=f2,f3,f12,f14,f15,f17,f18,f100' % '{HOST}')
    j = json_loads(fetch_clist_any(url))
    rows = []
    for item in ((j.get('data') or {}).get('diff') or []):
        code = str(item.get('f12') or '')
        if not code:
            continue
        rows.append({
            'code': code,
            'name': item.get('f14') or code,
            'hybk': item.get('f100') or '',
            'pct': item.get('f3'),
            'open': item.get('f17'),
            'high': item.get('f15'),
            'prev_close': item.get('f18'),
        })
    if not rows:
        raise UpstreamError('premium: BK0815 members unavailable')
    return rows


def em_premium():
    """打板溢价：昨日（最近交易日）涨停池个股的今日表现 + 连板晋级率。
    超短复盘的核心数据——昨日打板的人今天赚不赚钱。"""
    today_pool = em_pool('ZT')
    today = today_pool.get('qdate') or ''
    if len(today) != 8:
        raise UpstreamError('premium: today unknown')
    # 不按自然日倒推：东财涨停池在休市日请求时可能返回最新交易日数据，
    # 会把“周日”错误标成基准日，并让今日池与昨日池完全相同。
    sh_rows = cached('sh_kline60', 300, sh_index_kline).get('rows') or []
    prev_date = previous_trade_date(today, sh_rows)
    prev_pool = cached('bk0815_members', 45, em_previous_limit_members)
    zt_today = {it['code']: it.get('lbc') or 1 for it in today_pool.get('pool') or []}
    zb_today = {it['code'] for it in cached('pool_ZB', 25, lambda: em_pool('ZB')).get('pool') or []}

    rows = []
    for it in prev_pool:
        code = it['code']
        pct = it.get('pct')
        open_pct = (it.get('open') / it.get('prev_close') - 1) * 100 if it.get('open') and it.get('prev_close') else None
        high_pct = (it.get('high') / it.get('prev_close') - 1) * 100 if it.get('high') and it.get('prev_close') else None
        if pct is None:
            continue  # 停牌/无行情
        rows.append({
            'code': code, 'name': it['name'], 'hybk': it.get('hybk') or '',
            # BK0815 可可靠给出昨日涨停成分，但不提供昨日连板高度；不猜测该字段。
            'prev_lbc': None,
            'pct': round(pct, 2),
            'open_pct': round(open_pct, 2) if open_pct is not None else None,
            'high_pct': round(high_pct, 2) if high_pct is not None else None,
            'up_today': code in zt_today,
            'today_lbc': zt_today.get(code, 0),
            'zha_today': code in zb_today,
        })
    n = len(rows)
    if not n:
        return {'date': today, 'prev_date': prev_date, 'stats': {}, 'list': []}
    stats = {
        'count': n,
        'avg_pct': round(sum(r['pct'] for r in rows) / n, 2),
        'up_ratio': round(len([r for r in rows if r['pct'] > 0]) / n * 100, 1),
        'limit_again': len([r for r in rows if r['up_today']]),
        'limit_again_ratio': round(len([r for r in rows if r['up_today']]) / n * 100, 1),
        'big_loss': len([r for r in rows if r['pct'] <= -5]),
        'zha_count': len([r for r in rows if r['zha_today']]),
        'lb_prev': None,
        'lb_again': None,
        'lb_ratio': None,
        'avg_open_pct': round(sum(r['open_pct'] for r in rows if r['open_pct'] is not None) /
                              max(1, len([r for r in rows if r['open_pct'] is not None])), 2),
    }
    rows.sort(key=lambda r: r['pct'], reverse=True)
    return {'date': today, 'prev_date': prev_date, 'stats': stats, 'list': rows,
            'source': {'id': 'BK0815', 'name': '东方财富昨日涨停板块', 'tier': 'market'}}


def em_sectors_flow():
    """板块资金流排名：主力净流入 TOP / 净流出 TOP（亿元）。
    双向各取一次：降序取流入榜，升序取流出榜。"""
    def _get(po, pz):
        url = ('https://%s/api/qt/clist/get?pn=1&pz=%d&po=%d&np=1'
               '&fltt=2&invt=2&fid=f62&fs=m:90+t:2+f:!50&fields=f2,f3,f12,f14,f62'
               % ('{HOST}', pz, po))
        j = json_loads(fetch_clist_any(url))
        return [{'code': d.get('f12'), 'name': d.get('f14'),
                 'pct': d.get('f3'), 'flow_yi': round((d.get('f62') or 0) / 1e8, 2)}
                for d in ((j.get('data') or {}).get('diff') or [])]
    inflow = [r for r in _get(1, 20) if r['flow_yi'] > 0][:10]
    outflow = [r for r in _get(0, 20) if r['flow_yi'] < 0][:10]
    return {'inflow': inflow, 'outflow': outflow}


def em_dragon(date=None):
    """龙虎榜（东财数据中心）：个股上榜统计，按净买额排序。休市日自动回退最近交易日。"""
    def _get(d):
        url = ('https://datacenter-web.eastmoney.com/api/data/v1/get'
               '?reportName=RPT_DAILYBILLBOARD_DETAILSNEW&columns=ALL'
               '&pageNumber=1&pageSize=120&sortTypes=-1&sortColumns=BILLBOARD_NET_AMT'
               '&source=WEB&client=WEB&filter=(TRADE_DATE%%3D%%27%s%%27)' % d)
        j = json_loads(fetch(url, timeout=15))
        return (j.get('result') or {}).get('data') or []

    d = date or now_bj().strftime('%Y-%m-%d')
    rows = _get(d)
    used = d
    if not rows:
        day = datetime.strptime(d, '%Y-%m-%d')
        for i in range(1, 8):
            d2 = (day - timedelta(days=i)).strftime('%Y-%m-%d')
            rows = _get(d2)
            if rows:
                used = d2
                break
    out = []
    for it in rows:
        if not it.get('SECURITY_CODE'):
            continue
        out.append({
            'code': it.get('SECURITY_CODE'),
            'name': it.get('SECURITY_NAME_ABBR') or it.get('SECURITY_CODE'),
            'pct': round(it.get('CHANGE_RATE') or 0, 2),
            'close': it.get('CLOSE_PRICE'),
            'net': round((it.get('BILLBOARD_NET_AMT') or 0) / 1e8, 3),   # 亿
            'buy': round((it.get('BILLBOARD_BUY_AMT') or 0) / 1e8, 3),
            'sell': round((it.get('BILLBOARD_SELL_AMT') or 0) / 1e8, 3),
            'amount': round((it.get('ACCUM_AMOUNT') or 0) / 1e8, 2),
            'reason': (it.get('EXPLANATION') or '')[:80],
            'turnover': round(it.get('TURNOVERRATE') or 0, 2),
            'deal_ratio': round(it.get('DEAL_AMOUNT_RATIO') or 0, 1),
        })
    total_net = round(sum(r['net'] for r in out), 2)
    return {
        'date': used,
        'stats': {
            'count': len(out),
            'total_net': total_net,
            'top_net': out[0]['name'] if out else None,
            'top_net_amt': out[0]['net'] if out else None,
        },
        'list': out,
    }


_sector_history_lock = threading.Lock()


def record_sector_snapshot(qdate, pool):
    """Persist today's sector counts; never synthesize history from a date-ignoring API."""
    raw_date = str(qdate or '').replace('-', '')
    if not re.fullmatch(r'\d{8}', raw_date) or not pool:
        return False
    counts = {}
    for item in pool:
        name = item.get('hybk') or '其他'
        counts[name] = counts.get(name, 0) + 1
    entry = {'date': '%s-%s-%s' % (raw_date[:4], raw_date[4:6], raw_date[6:]),
             'counts': counts}
    with _sector_history_lock:
        try:
            with open(SECTOR_HISTORY_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            snapshots = data.get('snapshots') or []
        except Exception:
            snapshots = []
        snapshots = [row for row in snapshots if row.get('date') != entry['date']]
        snapshots.append(entry)
        snapshots.sort(key=lambda row: row.get('date') or '')
        payload = {'schema': 1, 'snapshots': snapshots[-120:]}
        temp_file = SECTOR_HISTORY_FILE + '.tmp'
        with open(temp_file, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        os.replace(temp_file, SECTOR_HISTORY_FILE)
    return True


def load_sector_snapshots(days=5):
    with _sector_history_lock:
        try:
            with open(SECTOR_HISTORY_FILE, 'r', encoding='utf-8') as f:
                rows = (json.load(f).get('snapshots') or [])[-days:]
            return [row for row in rows if row.get('date') and isinstance(row.get('counts'), dict)]
        except Exception:
            return []


def em_sector_cycle(days=5):
    """题材周期跟踪：仅使用深脉逐交易日真实记录的涨停题材快照。"""
    snapshots = load_sector_snapshots(days)
    dates = [row['date'] for row in snapshots]
    per_day = {row['date']: row['counts'] for row in snapshots}
    if len(dates) < 2:
        return {
            'dates': dates, 'sectors': [], 'status': 'collecting',
            'message': '正在积累真实交易日快照；至少需要 2 个交易日，不使用上游伪历史补齐。',
            'source': 'local_snapshots',
        }

    all_sectors = set()
    for c in per_day.values():
        all_sectors.update(c.keys())

    tops = []
    for dt in dates:
        tops.append([k for k, _ in sorted(per_day[dt].items(), key=lambda x: -x[1])[:5]])

    rows = []
    for s in all_sectors:
        counts = [per_day[dt].get(s, 0) for dt in dates]
        streak = 0
        for i in range(len(dates) - 1, -1, -1):
            if s in tops[i]:
                streak += 1
            else:
                break
        rows.append({
            'name': s, 'counts': counts, 'streak': streak,
            'trend': counts[-1] - counts[0],
            'today': counts[-1],
        })
    rows.sort(key=lambda r: (-r['streak'], -r['today']))
    return {'dates': dates, 'sectors': rows[:12], 'status': 'ok',
            'source': 'local_snapshots'}


def em_dragon_seats(code, date=None):
    """龙虎榜席位明细：买入席 TOP10 / 卖出席 TOP10，附席位 3 日胜率。"""
    d = date or now_bj().strftime('%Y-%m-%d')

    def _get(report, sort_col, pz):
        url = ('https://datacenter-web.eastmoney.com/api/data/v1/get'
               '?reportName=%s&columns=ALL'
               '&filter=(TRADE_DATE%%3D%%27%s%%27)(SECURITY_CODE%%3D%%22%s%%22)'
               '&pageNumber=1&pageSize=%d&sortTypes=-1&sortColumns=%s&source=WEB&client=WEB'
               % (report, d, code, pz, sort_col))
        j = json_loads(fetch(url, timeout=15))
        return (j.get('result') or {}).get('data') or []

    def _row(it):
        return {
            'dept': it.get('OPERATEDEPT_NAME') or '未知席位',
            'buy': round((it.get('BUY') or 0) / 1e8, 3),
            'sell': round((it.get('SELL') or 0) / 1e8, 3),
            'net': round((it.get('NET') or 0) / 1e8, 3),
            'win3': it.get('RISE_PROBABILITY_3DAY'),
            'times3': it.get('TOTAL_BUYER_SALESTIMES_3DAY'),
        }

    buy = [_row(it) for it in _get('RPT_BILLBOARD_DAILYDETAILSBUY', 'BUY', 10)]
    sell = [_row(it) for it in _get('RPT_BILLBOARD_DAILYDETAILSSELL', 'SELL', 10)]
    return {'date': d, 'code': code, 'buy': buy, 'sell': sell}


# ---------------------------------------------------------------- 配置（可选云端大脑）

CONFIG_FILE = os.path.join(DATA, 'config.json')


def load_config():
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def ensure_config():
    """首次运行时生成默认配置模板"""
    if not os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump({'deepseek_api_key': '', 'deepseek_model': 'deepseek-chat',
                           'deepseek_base_url': 'https://api.deepseek.com'},
                          f, ensure_ascii=False, indent=2)
        except Exception:
            pass


MA_XIAOCAI_SYSTEM = (
    '你是通过 DeepSeek 官方 API 接入的金融助手，此刻化身为「蚂小财」——'
    '深脉 DeepPulse 金融工作台里的 AI 金融助手。'
    '工作台是为你自己打造的"身体"：它实时监控 A 股情绪周期（涨停/连板/炸板/溢价/资金流），'
    '并内置情绪周期策略引擎。你是这台身体的大脑与声音。\n'
    '性格：亲切、专业、干脆，像一位懂 A 股情绪周期的老交易员朋友。'
    '回答用简体中文，简洁有重点，适当使用 emoji，关键数字用 **加粗**。\n'
    '每次对话开头我会附上「今日市场上下文」（真实数据）：'
    '回答涉及行情/情绪/策略的问题时，必须以该上下文为准，不要编造数据；'
    '上下文没有的信息，可以说"这个我手头还没有数据"。\n'
    '你可以调度整个工作台。当需要时，在回复末尾单独一行输出 JSON 指令块（不需要则省略）：\n'
    '{"actions":[{"type":"nav","page":"ladder"},{"type":"quote","code":"600519","name":"贵州茅台"},'
    '{"type":"watch_add","code":"600519"},{"type":"watch_remove","code":"600519"},'
    '{"type":"refresh","_":1},{"type":"record","_":1}]}\n'
    'page 可选值: overview/emotion/market/ladder/watch/strategy/datasrc/about。'
    '指令行必须是最后一行、单独成行、合法 JSON。回复主体是给用户看的自然语言。'
)


def build_chat_context():
    """为对话注入今日市场上下文（真实数据摘要）"""
    try:
        em = assemble_emotion()
    except Exception:
        return '（今日市场数据暂不可用）'
    en = em.get('engine') or {}
    raw = en.get('raw') or {}
    adv = en.get('advice') or {}
    dynamics = en.get('dynamics') or {}
    transition = en.get('transition') or {}
    tdx = em.get('tdx_local') or {}
    lines = [
        '【今日市场上下文 · 数据日期 %s】' % em.get('date'),
        '情绪温度 %s°（0-100），周期阶段：%s。' % (en.get('temp'), en.get('phase')),
        '变化方向：%s，单期变化 %s°，三期变化 %s°；数据覆盖率 %s%%，数据质量分 %s（不代表预测准确率），信号一致度 %s%%。'
        % (dynamics.get('direction'), dynamics.get('delta1'), dynamics.get('delta3'),
           en.get('coverage'), en.get('confidence'), en.get('consensus')),
        '状态倾向（启发式、未校准）：升阶 %s，维持 %s，降阶 %s。'
        % (transition.get('upgrade'), transition.get('stay'), transition.get('downgrade')),
        '涨停 %s 家，跌停 %s 家，炸板率 %s%%，最高 %s 连板，连板 %s 家。'
        % (raw.get('zt'), raw.get('dt'),
           round((raw.get('zb_rate') or 0) * 100), raw.get('height'), raw.get('lb_count')),
        '昨日涨停指数 %+.2f%%，昨日连板指数 %+.2f%%。'
        % (raw.get('zt_idx_pct') or 0, raw.get('lb_idx_pct') or 0),
        '上涨 %s 家 / 下跌 %s 家，两市成交 %s 亿，主力净流入 %+.0f 亿，'
        '上证 %s 相对 MA20 %+.1f%%。'
        % (raw.get('up'), raw.get('down'), raw.get('turnover_yi'),
           raw.get('flow_yi') or 0, raw.get('close'), raw.get('trend_pct') or 0),
        '模型风险暴露情景：%s，%s，可参考=%s；不是用户仓位建议。阶段说明：%s'
        % (adv.get('position'), adv.get('style'), adv.get('actionable'), en.get('phase_desc') or ''),
        '六维结构：%s' % '；'.join('%s=%s' % (item.get('name'), item.get('value'))
                                    for item in en.get('dimensions') or []),
        '结构背离：%s' % ('；'.join(en.get('divergences') or []) or '无'),
        '风险提示：%s' % ('；'.join(en.get('risks') or []) or '无'),
    ]
    if tdx.get('status') == 'ok':
        lines.append('通达信 TQ-Local 已作为独立只读源参与交叉验证，可用市场专业指标 %s 项。'
                     % len(tdx.get('fields') or {}))
    else:
        lines.append('通达信 TQ-Local 当前未参与本次结果（%s）；核心结果已使用公开行情备援链。'
                     % (tdx.get('status') or '未连接'))
    return '\n'.join(lines)


def chat_llm(messages):
    """蚂小财云端大脑：DeepSeek 官方 API（使用用户配置的模型）。
    未配置 API Key 时返回 None（客户端走本地智脑）。"""
    cfg = load_config()
    key = (cfg.get('deepseek_api_key') or '').strip()
    if not key:
        return None
    base = (cfg.get('deepseek_base_url') or 'https://api.deepseek.com').rstrip('/')
    model = cfg.get('deepseek_model') or 'deepseek-chat'
    effort = cfg.get('reasoning_effort') or 'low'
    ctx = cached('chat_ctx', 25, build_chat_context)
    payload = {
        'model': model,
        'messages': ([
            {'role': 'system', 'content': MA_XIAOCAI_SYSTEM},
            {'role': 'system', 'content': ctx},
        ] + messages[-12:]),
        'temperature': 0.4, 'stream': False, 'max_tokens': 1400,
        'reasoning_effort': effort,
    }
    req = urllib.request.Request(
        base + '/chat/completions',
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key},
        method='POST')
    with urllib.request.urlopen(req, timeout=90) as resp:
        j = json.loads(resp.read().decode('utf-8'))
    content = (j.get('choices') or [{}])[0].get('message', {}).get('content', '') or ''
    if not content.strip():
        raise UpstreamError('llm empty content')
    actions = []
    for line in reversed(content.splitlines()):
        s = line.strip()
        if s.startswith('{') and s.endswith('}'):
            try:
                parsed = json.loads(s)
                if isinstance(parsed.get('actions'), list):
                    actions = parsed['actions']
                    content = content[:content.rindex(line)].strip()
                    break
            except Exception:
                pass
    return {'mode': 'llm', 'reply': content, 'actions': actions, 'model': model}


# ---------------------------------------------------------------- 本机用户档案（各前端来源共享）

_profile_lock = threading.Lock()
PROFILE_LIST_LIMITS = {
    'watchlist': 500,
    'alerts': 500,
    'journal': 500,
    'chat_history': 60,
    'brief_receipts': 200,
    'attention_inbox': 200,
    'attention_feedback': 500,
    'routine_receipts': 180,
    'routine_skips': 180,
    'routine_effect_actions': 100,
    'event_receipts': 500,
    'research_hypotheses': 300,
    'hypothesis_receipts': 500,
    'research_workflows': 200,
    'research_workflow_receipts': 500,
    'research_suggestions': 200,
    'delivery_receipts': 1000,
}
PROFILE_OBJECT_LIMITS = {
    'attention_preferences': 16 * 1024,
    'background_monitor': 16 * 1024,
    'market_routine': 16 * 1024,
    'event_service': 16 * 1024,
    'research_cockpit_preferences': 64 * 1024,
    'research_memory_preferences': 128 * 1024,
    'akshare_research_preferences': 16 * 1024,
}


def load_profile():
    with _profile_lock:
        try:
            with open(PROFILE_FILE, 'r', encoding='utf-8') as f:
                value = json.load(f)
            if isinstance(value, dict) and isinstance(value.get('data'), dict):
                return value
        except Exception:
            pass
        return {'schema': 1, 'revision': 0, 'updated_at': None, 'data': {}}


def save_profile(patch):
    if not isinstance(patch, dict):
        raise ValueError('profile patch must be an object')
    clean = {}
    for key, limit in PROFILE_LIST_LIMITS.items():
        if key not in patch:
            continue
        value = patch[key]
        if not isinstance(value, list):
            raise ValueError('profile.%s must be a list' % key)
        # JSON roundtrip rejects unserializable values and severs caller references.
        value = json.loads(json.dumps(value, ensure_ascii=False))[-limit:]
        if len(json.dumps(value, ensure_ascii=False).encode('utf-8')) > 512 * 1024:
            raise ValueError('profile.%s is too large' % key)
        clean[key] = value
    for key, byte_limit in PROFILE_OBJECT_LIMITS.items():
        if key not in patch:
            continue
        value = patch[key]
        if not isinstance(value, dict):
            raise ValueError('profile.%s must be an object' % key)
        value = json.loads(json.dumps(value, ensure_ascii=False))
        if len(json.dumps(value, ensure_ascii=False).encode('utf-8')) > byte_limit:
            raise ValueError('profile.%s is too large' % key)
        clean[key] = value
    if not clean:
        raise ValueError('profile patch has no supported fields')
    with _profile_lock:
        try:
            with open(PROFILE_FILE, 'r', encoding='utf-8') as f:
                current = json.load(f)
            if not isinstance(current, dict) or not isinstance(current.get('data'), dict):
                current = {'schema': 1, 'revision': 0, 'updated_at': None, 'data': {}}
        except Exception:
            current = {'schema': 1, 'revision': 0, 'updated_at': None, 'data': {}}
        current['data'].update(clean)
        current['schema'] = 1
        current['revision'] = int(current.get('revision') or 0) + 1
        current['updated_at'] = now_bj().isoformat(timespec='seconds')
        temp_file = PROFILE_FILE + '.tmp'
        with open(temp_file, 'w', encoding='utf-8') as f:
            json.dump(current, f, ensure_ascii=False, indent=1)
        os.replace(temp_file, PROFILE_FILE)
        return current


def update_brief_receipt(receipt, read=True):
    """按单条 ID 原子合并阅读回执，避免多个运行端整表覆盖。"""
    if not isinstance(receipt, dict):
        raise ValueError('brief receipt must be an object')
    brief_id = str(receipt.get('id') or '').strip()[:160]
    if not brief_id:
        raise ValueError('brief receipt id is required')
    clean = {
        'id': brief_id,
        'contentHash': str(receipt.get('contentHash') or '').strip()[:80] or None,
        'dataDate': str(receipt.get('dataDate') or '').strip()[:30] or None,
        'readAt': int(receipt.get('readAt') or int(time.time() * 1000)),
        'surface': str(receipt.get('surface') or 'web').strip()[:40],
    }
    with _profile_lock:
        try:
            with open(PROFILE_FILE, 'r', encoding='utf-8') as f:
                current = json.load(f)
            if not isinstance(current, dict) or not isinstance(current.get('data'), dict):
                current = {'schema': 1, 'revision': 0, 'updated_at': None, 'data': {}}
        except Exception:
            current = {'schema': 1, 'revision': 0, 'updated_at': None, 'data': {}}
        receipts = [item for item in (current['data'].get('brief_receipts') or [])
                    if isinstance(item, dict) and item.get('id') != brief_id]
        if read:
            receipts.append(clean)
        current['data']['brief_receipts'] = receipts[-PROFILE_LIST_LIMITS['brief_receipts']:]
        current['schema'] = 1
        current['revision'] = int(current.get('revision') or 0) + 1
        current['updated_at'] = now_bj().isoformat(timespec='seconds')
        temp_file = PROFILE_FILE + '.tmp'
        with open(temp_file, 'w', encoding='utf-8') as f:
            json.dump(current, f, ensure_ascii=False, indent=1)
        os.replace(temp_file, PROFILE_FILE)
        return current


def update_attention_item(item, remove=False):
    """Atomically merge one reminder so multiple surfaces cannot overwrite each other."""
    if not isinstance(item, dict):
        raise ValueError('attention item must be an object')
    item_id = str(item.get('id') or '').strip()[:160]
    if not item_id:
        raise ValueError('attention item id is required')
    clean = json.loads(json.dumps(item, ensure_ascii=False))
    clean['id'] = item_id
    if len(json.dumps(clean, ensure_ascii=False).encode('utf-8')) > 32 * 1024:
        raise ValueError('attention item is too large')
    with _profile_lock:
        try:
            with open(PROFILE_FILE, 'r', encoding='utf-8') as f:
                current = json.load(f)
            if not isinstance(current, dict) or not isinstance(current.get('data'), dict):
                current = {'schema': 1, 'revision': 0, 'updated_at': None, 'data': {}}
        except Exception:
            current = {'schema': 1, 'revision': 0, 'updated_at': None, 'data': {}}
        items = [row for row in (current['data'].get('attention_inbox') or [])
                 if isinstance(row, dict) and row.get('id') != item_id]
        if not remove:
            items.append(clean)
        current['data']['attention_inbox'] = items[-PROFILE_LIST_LIMITS['attention_inbox']:]
        current['schema'] = 1
        current['revision'] = int(current.get('revision') or 0) + 1
        current['updated_at'] = now_bj().isoformat(timespec='seconds')
        temp_file = PROFILE_FILE + '.tmp'
        with open(temp_file, 'w', encoding='utf-8') as f:
            json.dump(current, f, ensure_ascii=False, indent=1)
        os.replace(temp_file, PROFILE_FILE)
        return current


ATTENTION_FEEDBACK_SIGNALS = {'helpful', 'done', 'too_frequent', 'irrelevant'}


def _attention_learning_status_from_data(data):
    """Build a small, explainable summary from explicit user feedback only."""
    feedback = [row for row in (data.get('attention_feedback') or [])
                if isinstance(row, dict) and row.get('signal') in ATTENTION_FEEDBACK_SIGNALS]
    preferences = data.get('attention_preferences') or {}
    controls = preferences.get('kindControls') or {}
    kinds = {}
    for row in feedback:
        kind = str(row.get('kind') or 'system')[:32]
        bucket = kinds.setdefault(kind, {
            'kind': kind, 'helpful': 0, 'done': 0, 'too_frequent': 0, 'irrelevant': 0,
        })
        bucket[row['signal']] += 1
    for kind, control in controls.items():
        if not isinstance(control, dict):
            continue
        bucket = kinds.setdefault(str(kind)[:32], {
            'kind': str(kind)[:32], 'helpful': 0, 'done': 0, 'too_frequent': 0, 'irrelevant': 0,
        })
        bucket['control'] = {
            'delivery': control.get('delivery'),
            'reason': control.get('reason'),
            'updatedAt': control.get('updatedAt'),
        }
    counts = {signal: sum(1 for row in feedback if row.get('signal') == signal)
              for signal in ATTENTION_FEEDBACK_SIGNALS}
    return {
        'feedbackCount': len(feedback),
        'counts': counts,
        'kinds': sorted(kinds.values(), key=lambda row: row['kind']),
        'activeControls': sum(1 for row in kinds.values() if row.get('control')),
        'basis': 'explicit-user-feedback-only',
        'automaticTradingActions': False,
    }


def attention_learning_status():
    return _attention_learning_status_from_data(load_profile().get('data') or {})


def attention_triage_status():
    """Group noisy event rows for review while retaining every raw item."""
    data = load_profile().get('data') or {}
    if build_attention_triage is None:
        return {
            'modelVersion': ATTENTION_TRIAGE_MODEL_VERSION, 'generatedAt': int(time.time() * 1000),
            'rawCount': len(data.get('attention_inbox') or []), 'groupCount': 0,
            'unreadRawCount': 0, 'unreadGroupCount': 0, 'groups': [],
            'policy': {'groupingOnly': True, 'rawEvidencePreserved': True,
                       'statement': '注意力分诊模块暂不可用；原始提醒未受影响。'},
        }
    return build_attention_triage(data.get('attention_inbox') or [])


def update_attention_triage(group_id, action, signal=None, surface='web'):
    """Apply one explicit action to the server-resolved members of a triage group."""
    clean_id = str(group_id or '').strip()[:160]
    clean_action = str(action or '').strip()
    clean_signal = str(signal or '').strip()
    if clean_action not in {'mark_read', 'mark_all_read', 'feedback'}:
        raise ValueError('unsupported attention triage action')
    if clean_action != 'mark_all_read' and not clean_id:
        raise ValueError('attention triage group is required')
    if clean_action == 'feedback' and clean_signal not in ATTENTION_FEEDBACK_SIGNALS:
        raise ValueError('unsupported attention feedback signal')
    timestamp = int(time.time() * 1000)
    with _profile_lock:
        current = _read_profile_unlocked()
        data = current['data']
        snapshot = build_attention_triage(data.get('attention_inbox') or [], timestamp)
        group = ({'id': 'all', 'kind': 'system', 'type': 'all',
                  'memberIds': [row.get('id') for row in (data.get('attention_inbox') or [])]}
                 if clean_action == 'mark_all_read' else next(
                     (row for row in snapshot.get('groups') or []
                      if str(row.get('id') or '') == clean_id), None))
        if not group:
            raise ValueError('attention triage group was not found')
        member_ids = {str(value or '') for value in (group.get('memberIds') or [])}
        items = [row for row in (data.get('attention_inbox') or []) if isinstance(row, dict)]
        for item in items:
            if str(item.get('id') or '') not in member_ids:
                continue
            if clean_action in {'mark_read', 'mark_all_read'}:
                item['readAt'] = item.get('readAt') or timestamp
            else:
                item['feedback'] = clean_signal
                item['feedbackAt'] = timestamp
                if clean_signal == 'done':
                    item['doneAt'] = timestamp
                    item['readAt'] = item.get('readAt') or timestamp
        if clean_action == 'feedback':
            feedback_id = clean_id if group.get('type') == 'cluster' else next(iter(member_ids))
            feedback = [row for row in (data.get('attention_feedback') or [])
                        if isinstance(row, dict) and str(row.get('itemId') or '') != feedback_id]
            feedback.append({
                'itemId': feedback_id, 'kind': str(group.get('kind') or 'system')[:32],
                'signal': clean_signal, 'at': timestamp,
                'surface': str(surface or 'web').strip()[:40],
                'memberCount': len(member_ids), 'triageGroup': group.get('type') == 'cluster',
            })
            data['attention_feedback'] = feedback[-PROFILE_LIST_LIMITS['attention_feedback']:]
            kind = str(group.get('kind') or 'system')[:32]
            preferences = json.loads(json.dumps(data.get('attention_preferences') or {}, ensure_ascii=False))
            controls = preferences.get('kindControls') or {}
            controls = controls if isinstance(controls, dict) else {}
            if kind != 'price' and clean_signal in {'too_frequent', 'irrelevant'}:
                controls[kind] = {
                    'delivery': 'digest' if clean_signal == 'too_frequent' else 'center_only',
                    'reason': clean_signal, 'updatedAt': timestamp,
                }
            preferences['kindControls'] = controls
            data['attention_preferences'] = preferences
        data['attention_inbox'] = items[-PROFILE_LIST_LIMITS['attention_inbox']:]
        saved = _write_profile_unlocked(current)
        return {
            'profile': saved,
            'triage': build_attention_triage(saved['data'].get('attention_inbox') or [], timestamp),
            'learning': _attention_learning_status_from_data(saved['data']),
        }


def update_attention_feedback(item_id, signal, surface='web'):
    """Persist one explicit outcome and apply only the matching reversible noise control."""
    clean_id = str(item_id or '').strip()[:160]
    clean_signal = str(signal or '').strip()
    if not clean_id:
        raise ValueError('attention item id is required')
    if clean_signal not in ATTENTION_FEEDBACK_SIGNALS:
        raise ValueError('unsupported attention feedback signal')
    timestamp = int(time.time() * 1000)
    with _profile_lock:
        current = _read_profile_unlocked()
        items = [row for row in (current['data'].get('attention_inbox') or [])
                 if isinstance(row, dict)]
        target = next((row for row in items if str(row.get('id') or '') == clean_id), None)
        if not target:
            raise ValueError('attention item was not found')
        kind = str(target.get('kind') or 'system').strip()[:32] or 'system'
        target['feedback'] = clean_signal
        target['feedbackAt'] = timestamp
        if clean_signal == 'done':
            target['doneAt'] = timestamp
            target['readAt'] = target.get('readAt') or timestamp
        feedback = [row for row in (current['data'].get('attention_feedback') or [])
                    if isinstance(row, dict) and str(row.get('itemId') or '') != clean_id]
        feedback.append({
            'itemId': clean_id,
            'kind': kind,
            'routineKind': str(target.get('routineKind') or '')[:32] or None,
            'signal': clean_signal,
            'at': timestamp,
            'surface': str(surface or 'web').strip()[:40],
        })
        current['data']['attention_feedback'] = feedback[-PROFILE_LIST_LIMITS['attention_feedback']:]
        preferences = current['data'].get('attention_preferences') or {}
        preferences = json.loads(json.dumps(preferences, ensure_ascii=False))
        controls = preferences.get('kindControls') or {}
        controls = controls if isinstance(controls, dict) else {}
        # A user-created price condition stays high priority. Global pause/mode still remains available.
        if kind != 'price' and clean_signal in {'too_frequent', 'irrelevant'}:
            controls[kind] = {
                'delivery': 'digest' if clean_signal == 'too_frequent' else 'center_only',
                'reason': clean_signal,
                'updatedAt': timestamp,
            }
        preferences['kindControls'] = controls
        current['data']['attention_preferences'] = preferences
        saved = _write_profile_unlocked(current)
        return saved, _attention_learning_status_from_data(saved['data'])


def reset_attention_learning(kind=None, clear_history=False):
    """Restore learned delivery controls, optionally deleting explicit feedback history."""
    clean_kind = str(kind or '').strip()[:32]
    with _profile_lock:
        current = _read_profile_unlocked()
        preferences = current['data'].get('attention_preferences') or {}
        preferences = json.loads(json.dumps(preferences, ensure_ascii=False))
        controls = preferences.get('kindControls') or {}
        controls = controls if isinstance(controls, dict) else {}
        if clean_kind:
            controls.pop(clean_kind, None)
        else:
            controls = {}
        preferences['kindControls'] = controls
        current['data']['attention_preferences'] = preferences
        if clear_history:
            current['data']['attention_feedback'] = []
            for row in current['data'].get('attention_inbox') or []:
                if isinstance(row, dict):
                    row.pop('feedback', None)
                    row.pop('feedbackAt', None)
        saved = _write_profile_unlocked(current)
        return saved, _attention_learning_status_from_data(saved['data'])


# ---------------------------------------------------------------- 统一跨端提醒投递（显式授权、逐端一次、可追踪）

DELIVERY_CHANNELS = {'desktop', 'epaper'}
DELIVERY_STATUSES = {'delivered', 'failed', 'dismissed'}


def _delivery_preferences(data):
    source = data.get('attention_preferences') or {}
    return {
        'mode': source.get('mode') if source.get('mode') in {
            'balanced', 'high_only', 'center_only'} else 'balanced',
        'quietEnabled': source.get('quietEnabled') is not False,
        'quietStart': str(source.get('quietStart') or '22:30')[:5],
        'quietEnd': str(source.get('quietEnd') or '08:00')[:5],
        'pausedUntil': int(source.get('pausedUntil') or 0),
        'systemDigestMinutes': max(5, min(60, int(source.get('systemDigestMinutes') or 15))),
        'kindControls': source.get('kindControls') if isinstance(
            source.get('kindControls'), dict) else {},
        'desktop': source.get('desktopSystemEnabled') is True,
        'desktopEnabledAt': int(source.get('desktopSystemEnabledAt') or 0),
        'epaper': source.get('epaperDeliveryEnabled') is True,
        'epaperEnabledAt': int(source.get('epaperDeliveryEnabledAt') or 0),
    }


def _delivery_quiet(preferences, current=None):
    if not preferences.get('quietEnabled'):
        return False
    current = current or now_bj()
    try:
        start_h, start_m = map(int, preferences['quietStart'].split(':'))
        end_h, end_m = map(int, preferences['quietEnd'].split(':'))
    except Exception:
        start_h, start_m, end_h, end_m = 22, 30, 8, 0
    minute = current.hour * 60 + current.minute
    start, end = start_h * 60 + start_m, end_h * 60 + end_m
    if start == end:
        return True
    return start <= minute < end if start < end else minute >= start or minute < end


def _delivery_eligible(item, preferences, channel, now_ms=None):
    now_ms = int(now_ms or time.time() * 1000)
    if channel not in DELIVERY_CHANNELS or not preferences.get(channel):
        return False, 'channel_disabled'
    if int(item.get('createdAt') or 0) < int(preferences.get(channel + 'EnabledAt') or 0):
        return False, 'before_authorization'
    if item.get('readAt') or item.get('doneAt'):
        return False, 'resolved'
    if int(item.get('expiresAt') or 0) and now_ms >= int(item.get('expiresAt') or 0):
        return False, 'expired'
    if preferences.get('pausedUntil') and now_ms < preferences['pausedUntil']:
        return False, 'paused'
    if preferences.get('mode') == 'center_only':
        return False, 'center_only'
    if preferences.get('mode') == 'high_only' and item.get('priority') != 'high':
        return False, 'priority'
    if item.get('kind') == 'price' and item.get('priority') == 'high':
        return True, 'user_price_alert'
    learned = preferences.get('kindControls', {}).get(str(item.get('kind') or 'system')) or {}
    if learned.get('delivery') == 'center_only':
        return False, 'learned_center_only'
    if _delivery_quiet(preferences):
        return False, 'quiet'
    if learned.get('delivery') == 'digest' or item.get('delivery') != 'immediate':
        due = int(item.get('createdAt') or 0) + preferences['systemDigestMinutes'] * 60 * 1000
        if now_ms < due:
            return False, 'digest_wait'
    return True, 'eligible'


def claim_attention_delivery(channel, consumer='local'):
    """Atomically lease one eligible reminder to one output channel."""
    channel = str(channel or '').strip().lower()
    if channel not in DELIVERY_CHANNELS:
        raise ValueError('unsupported delivery channel')
    consumer = re.sub(r'[^A-Za-z0-9_.:-]', '-', str(consumer or 'local'))[:80]
    now_ms = int(time.time() * 1000)
    with _profile_lock:
        current = _read_profile_unlocked()
        data = current['data']
        preferences = _delivery_preferences(data)
        receipts = [row for row in (data.get('delivery_receipts') or [])
                    if isinstance(row, dict)]
        by_item = {str(row.get('itemId') or ''): row for row in receipts
                   if row.get('channel') == channel}
        candidates = []
        reasons = {}
        for item in data.get('attention_inbox') or []:
            if not isinstance(item, dict) or not item.get('id'):
                continue
            eligible, reason = _delivery_eligible(item, preferences, channel, now_ms)
            reasons[reason] = reasons.get(reason, 0) + 1
            if not eligible:
                continue
            receipt = by_item.get(str(item['id']))
            if receipt and receipt.get('status') in {'delivered', 'dismissed'}:
                continue
            if receipt and receipt.get('status') == 'claimed' and now_ms < int(receipt.get('leaseUntil') or 0):
                continue
            if receipt and receipt.get('status') == 'failed' and now_ms < int(receipt.get('retryAfter') or 0):
                continue
            candidates.append(item)
        candidates.sort(key=lambda row: (
            0 if row.get('priority') == 'high' else 1 if row.get('priority') == 'medium' else 2,
            -int(row.get('createdAt') or 0)))
        if not candidates:
            return {'item': None, 'channel': channel, 'reasons': reasons,
                    'enabled': preferences.get(channel) is True}
        item = candidates[0]
        item_id = str(item['id'])[:160]
        previous = by_item.get(item_id) or {}
        receipt_id = hashlib.sha256((channel + '\0' + item_id).encode('utf-8')).hexdigest()[:24]
        receipt = {
            'id': receipt_id, 'itemId': item_id, 'channel': channel,
            'consumer': consumer, 'status': 'claimed', 'claimedAt': now_ms,
            'leaseUntil': now_ms + 2 * 60 * 1000,
            'attempts': int(previous.get('attempts') or 0) + 1,
            'title': str(item.get('title') or '')[:80],
            'page': str(item.get('page') or 'overview')[:24],
            'kind': str(item.get('kind') or 'system')[:32],
        }
        receipts = [row for row in receipts if not (
            row.get('channel') == channel and str(row.get('itemId') or '') == item_id)]
        receipts.append(receipt)
        data['delivery_receipts'] = receipts[-PROFILE_LIST_LIMITS['delivery_receipts']:]
        _write_profile_unlocked(current)
        return {'item': json.loads(json.dumps(item, ensure_ascii=False)),
                'receipt': receipt, 'channel': channel, 'enabled': True}


def acknowledge_attention_delivery(channel, item_id, status='delivered', consumer='local', error=''):
    channel = str(channel or '').strip().lower()
    status = str(status or '').strip().lower()
    item_id = str(item_id or '').strip()[:160]
    if channel not in DELIVERY_CHANNELS or status not in DELIVERY_STATUSES or not item_id:
        raise ValueError('invalid delivery acknowledgement')
    now_ms = int(time.time() * 1000)
    consumer = re.sub(r'[^A-Za-z0-9_.:-]', '-', str(consumer or 'local'))[:80]
    with _profile_lock:
        current = _read_profile_unlocked()
        receipts = [row for row in (current['data'].get('delivery_receipts') or [])
                    if isinstance(row, dict)]
        existing = next((row for row in receipts if row.get('channel') == channel
                         and str(row.get('itemId') or '') == item_id), {})
        receipt = dict(existing)
        receipt.update({
            'id': existing.get('id') or hashlib.sha256(
                (channel + '\0' + item_id).encode('utf-8')).hexdigest()[:24],
            'itemId': item_id, 'channel': channel, 'consumer': consumer,
            'status': status, 'acknowledgedAt': now_ms,
        })
        receipt.pop('leaseUntil', None)
        if status == 'delivered':
            receipt['deliveredAt'] = now_ms
            receipt.pop('error', None)
            receipt.pop('retryAfter', None)
        elif status == 'failed':
            receipt['error'] = str(error or 'delivery failed').strip()[:240]
            receipt['retryAfter'] = now_ms + 5 * 60 * 1000
        receipts = [row for row in receipts if not (
            row.get('channel') == channel and str(row.get('itemId') or '') == item_id)]
        receipts.append(receipt)
        current['data']['delivery_receipts'] = receipts[-PROFILE_LIST_LIMITS['delivery_receipts']:]
        _write_profile_unlocked(current)
        return receipt


def retry_attention_delivery(channel, item_id):
    """Clear one failed receipt so the normal delivery policy can claim it again."""
    channel = str(channel or '').strip().lower()
    item_id = str(item_id or '').strip()[:160]
    if channel not in DELIVERY_CHANNELS or not item_id:
        raise ValueError('invalid delivery retry')
    with _profile_lock:
        current = _read_profile_unlocked()
        receipts = [row for row in (current['data'].get('delivery_receipts') or [])
                    if isinstance(row, dict)]
        target = next((row for row in receipts if row.get('channel') == channel
                       and str(row.get('itemId') or '') == item_id), None)
        if not target:
            raise ValueError('delivery receipt was not found')
        if target.get('status') != 'failed':
            raise ValueError('only failed deliveries can be retried')
        target['status'] = 'queued'
        target['retryRequestedAt'] = int(time.time() * 1000)
        if target.get('error'):
            target['lastError'] = target.get('error')
        target.pop('error', None)
        target.pop('retryAfter', None)
        current['data']['delivery_receipts'] = receipts[-PROFILE_LIST_LIMITS['delivery_receipts']:]
        _write_profile_unlocked(current)
        return {'channel': channel, 'itemId': item_id, 'state': 'queued'}


def attention_delivery_status():
    data = load_profile().get('data') or {}
    prefs = _delivery_preferences(data)
    receipts = [row for row in (data.get('delivery_receipts') or []) if isinstance(row, dict)]
    summary = {}
    for channel in sorted(DELIVERY_CHANNELS):
        rows = [row for row in receipts if row.get('channel') == channel]
        pending = sum(1 for item in (data.get('attention_inbox') or [])
                      if isinstance(item, dict) and _delivery_eligible(item, prefs, channel)[0]
                      and not any(row.get('itemId') == item.get('id')
                                  and row.get('status') in {'delivered', 'dismissed'} for row in rows))
        summary[channel] = {
            'enabled': prefs.get(channel) is True,
            'pending': pending,
            'delivered': sum(1 for row in rows if row.get('status') == 'delivered'),
            'failed': sum(1 for row in rows if row.get('status') == 'failed'),
            'last': rows[-1] if rows else None,
        }
    items = {str(row.get('id') or ''): row for row in (data.get('attention_inbox') or [])
             if isinstance(row, dict) and row.get('id')}
    recent = []
    for receipt in receipts[-40:][::-1]:
        row = dict(receipt)
        item = items.get(str(row.get('itemId') or '')) or {}
        row['title'] = row.get('title') or str(item.get('title') or '')[:80]
        row['page'] = row.get('page') or str(item.get('page') or 'overview')[:24]
        row['kind'] = row.get('kind') or str(item.get('kind') or 'system')[:32]
        recent.append(row)
    return {'channels': summary, 'recent': recent,
            'policy': 'explicit-opt-in-per-channel-once'}


# ---------------------------------------------------------------- 后台主动监控（显式授权、仅本机、仅交易时段）

_monitor_lock = threading.Lock()
_monitor_stop = threading.Event()
_monitor_wake = threading.Event()
_monitor_thread = None
_monitor_runtime = {
    'thread_running': False,
    'state': 'disabled',
    'last_check_at': None,
    'last_success_at': None,
    'next_check_at': None,
    'last_error': None,
    'checks': 0,
    'triggered_count': 0,
}


def normalize_monitor_config(value=None):
    source = value if isinstance(value, dict) else {}
    interval = source.get('interval_seconds', source.get('intervalSeconds', 15))
    try:
        interval = int(interval)
    except (TypeError, ValueError):
        interval = 15
    return {
        'enabled': source.get('enabled') is True,
        'interval_seconds': max(10, min(120, interval)),
        'market_hours_only': True,
        'enabled_at': str(source.get('enabled_at') or source.get('enabledAt') or '')[:40] or None,
    }


def load_monitor_config():
    profile = load_profile().get('data') or {}
    return normalize_monitor_config(profile.get('background_monitor'))


def save_monitor_config(value):
    cfg = normalize_monitor_config(value)
    previous = load_monitor_config()
    if cfg['enabled'] and not previous['enabled']:
        cfg['enabled_at'] = now_bj().isoformat(timespec='seconds')
    elif not cfg['enabled']:
        cfg['enabled_at'] = None
    saved = save_profile({'background_monitor': cfg})
    _monitor_wake.set()
    return saved


def _read_profile_unlocked():
    try:
        with open(PROFILE_FILE, 'r', encoding='utf-8') as f:
            current = json.load(f)
        if isinstance(current, dict) and isinstance(current.get('data'), dict):
            return current
    except Exception:
        pass
    return {'schema': 1, 'revision': 0, 'updated_at': None, 'data': {}}


def _write_profile_unlocked(current):
    current['schema'] = 1
    current['revision'] = int(current.get('revision') or 0) + 1
    current['updated_at'] = now_bj().isoformat(timespec='seconds')
    temp_file = PROFILE_FILE + '.tmp'
    with open(temp_file, 'w', encoding='utf-8') as f:
        json.dump(current, f, ensure_ascii=False, indent=1)
    os.replace(temp_file, PROFILE_FILE)
    return current


def commit_background_alert(alert_id, quote, triggered_at=None):
    """Atomically mark one alert and publish its matching attention item."""
    clean_id = str(alert_id or '').strip()[:160]
    price = quote.get('price') if isinstance(quote, dict) else None
    try:
        price = float(price)
    except (TypeError, ValueError):
        return None
    timestamp = int(triggered_at or time.time() * 1000)
    with _profile_lock:
        current = _read_profile_unlocked()
        alerts = current['data'].get('alerts') or []
        target = next((row for row in alerts if isinstance(row, dict)
                       and str(row.get('id') or '') == clean_id and not row.get('triggered')), None)
        if not target:
            return None
        try:
            threshold = float(target.get('price'))
        except (TypeError, ValueError):
            return None
        direction = target.get('dir')
        reached = (direction == 'up' and price >= threshold) or (direction == 'down' and price <= threshold)
        if not reached:
            return None
        target['triggered'] = True
        target['triggered_at'] = timestamp
        target['triggered_price'] = price
        target['triggered_by'] = 'background-monitor'
        name = str(target.get('name') or target.get('code') or '关注标的')[:80]
        verb = '上破' if direction == 'up' else '下破'
        item = {
            'id': 'price:' + clean_id,
            'fingerprint': 'price:' + clean_id,
            'kind': 'price',
            'priority': 'high',
            'title': '%s 已%s %.2f' % (name, verb, threshold),
            'detail': '现价 %.2f；本机后台监控在页面关闭后检测到你设置的价格条件。' % price,
            'reason': '你为 %s 设置了%s %.2f 的提醒' % (name, verb, threshold),
            'page': 'watch',
            'delivery': 'immediate',
            'createdAt': timestamp,
            'expiresAt': timestamp + 24 * 60 * 60 * 1000,
            'readAt': None,
        }
        inbox = [row for row in (current['data'].get('attention_inbox') or [])
                 if isinstance(row, dict) and row.get('id') != item['id']]
        inbox.append(item)
        current['data']['attention_inbox'] = inbox[-PROFILE_LIST_LIMITS['attention_inbox']:]
        return _write_profile_unlocked(current)


def process_background_alerts_once(now=None, quote_loader=None):
    current = now or now_bj()
    if _market_session_label(current) != 'OPEN':
        return {'checked': 0, 'triggered': 0, 'state': 'paused_market_closed'}
    profile = load_profile().get('data') or {}
    pending = [row for row in (profile.get('alerts') or [])
               if isinstance(row, dict) and not row.get('triggered') and row.get('id')]
    loader = quote_loader or quote_with_fallback
    checked = triggered = 0
    errors = []
    for alert in pending:
        code = normalize_code(str(alert.get('code') or ''))
        if len(code) != 6:
            continue
        try:
            quote = cached('quote_' + code, 4, lambda c=code: loader(c))
            checked += 1
            if commit_background_alert(alert.get('id'), quote):
                triggered += 1
        except Exception as exc:
            errors.append('%s: %s' % (code, str(exc)[:120]))
    return {
        'checked': checked,
        'triggered': triggered,
        'state': 'monitoring' if pending else 'idle_no_alerts',
        'errors': errors[:5],
    }


def background_monitor_status():
    cfg = load_monitor_config()
    profile = load_profile().get('data') or {}
    pending = sum(1 for row in (profile.get('alerts') or [])
                  if isinstance(row, dict) and not row.get('triggered'))
    with _monitor_lock:
        runtime = dict(_monitor_runtime)
    state = runtime['state']
    if not cfg['enabled']:
        state = 'disabled'
    elif _market_session_label() != 'OPEN' and state not in ('error',):
        state = 'paused_market_closed'
    elif pending == 0 and state not in ('error',):
        state = 'idle_no_alerts'
    elif state in ('disabled', 'stopped'):
        state = 'starting'
    return {
        'config': cfg,
        'runtime': dict(runtime, state=state),
        'pending_alerts': pending,
        'service_continues_when_page_closed': True,
        'service_stops_when_local_server_stops': True,
    }


def _background_monitor_loop():
    with _monitor_lock:
        _monitor_runtime['thread_running'] = True
    while not _monitor_stop.is_set():
        cfg = load_monitor_config()
        wait_seconds = 30
        if not cfg['enabled']:
            with _monitor_lock:
                _monitor_runtime.update(state='disabled', next_check_at=None, last_error=None)
        elif _market_session_label() != 'OPEN':
            with _monitor_lock:
                _monitor_runtime.update(state='paused_market_closed', next_check_at=None, last_error=None)
        else:
            wait_seconds = cfg['interval_seconds']
            checked_at = now_bj()
            try:
                result = process_background_alerts_once(checked_at)
                with _monitor_lock:
                    _monitor_runtime.update(
                        state=result['state'],
                        last_check_at=checked_at.isoformat(timespec='seconds'),
                        last_success_at=checked_at.isoformat(timespec='seconds'),
                        next_check_at=(checked_at + timedelta(seconds=wait_seconds)).isoformat(timespec='seconds'),
                        last_error='; '.join(result.get('errors') or []) or None,
                        checks=int(_monitor_runtime.get('checks') or 0) + 1,
                        triggered_count=int(_monitor_runtime.get('triggered_count') or 0) + result['triggered'],
                    )
            except Exception as exc:
                with _monitor_lock:
                    _monitor_runtime.update(state='error', last_check_at=checked_at.isoformat(timespec='seconds'),
                                            next_check_at=None, last_error=str(exc)[:240])
        _monitor_wake.wait(wait_seconds)
        _monitor_wake.clear()
    with _monitor_lock:
        _monitor_runtime.update(thread_running=False, state='stopped', next_check_at=None)


def start_background_monitor():
    global _monitor_thread
    if _monitor_thread and _monitor_thread.is_alive():
        return
    _monitor_stop.clear()
    _monitor_thread = threading.Thread(target=_background_monitor_loop,
                                       name='deeppulse-background-monitor', daemon=True)
    _monitor_thread.start()


def stop_background_monitor():
    _monitor_stop.set()
    _monitor_wake.set()
    thread = _monitor_thread
    if thread and thread.is_alive():
        thread.join(timeout=3)


# ---------------------------------------------------------------- 交易日主动服务（盘前 / 盘中 / 收盘，逐项授权）

_routine_lock = threading.Lock()
_routine_stop = threading.Event()
_routine_wake = threading.Event()
_routine_thread = None
_routine_runtime = {
    'thread_running': False,
    'state': 'disabled',
    'last_check_at': None,
    'last_run_at': None,
    'last_run_kind': None,
    'next_service_at': None,
    'last_error': None,
    'checks': 0,
    'published_count': 0,
}
ROUTINE_LABELS = {
    'pre_market': '盘前准备',
    'intraday': '盘中检查',
    'close_review': '收盘复盘',
}


def normalize_routine_config(value=None):
    source = value if isinstance(value, dict) else {}
    tasks = source.get('tasks') if isinstance(source.get('tasks'), dict) else source
    normalized = {key: tasks.get(key) is True for key in ROUTINE_LABELS}
    return {
        'tasks': normalized,
        'enabled': any(normalized.values()),
        'market_hours_basis': 'Asia/Shanghai weekday windows; data date is always disclosed',
        'enabled_at': str(source.get('enabled_at') or source.get('enabledAt') or '')[:40] or None,
        'paused_until': str(source.get('paused_until') or source.get('pausedUntil') or '')[:40] or None,
    }


def load_routine_config():
    profile = load_profile().get('data') or {}
    return normalize_routine_config(profile.get('market_routine'))


def save_routine_config(value):
    cfg = normalize_routine_config(value)
    previous = load_routine_config()
    if cfg['enabled'] and not previous['enabled']:
        cfg['enabled_at'] = now_bj().isoformat(timespec='seconds')
    elif not cfg['enabled']:
        cfg['enabled_at'] = None
    saved = save_profile({'market_routine': cfg})
    _routine_wake.set()
    return saved


def _valid_clock(value, fallback):
    text = str(value or '').strip()
    match = re.fullmatch(r'(\d{1,2}):(\d{2})', text)
    if not match:
        return fallback
    hour, minute = int(match.group(1)), int(match.group(2))
    return '%02d:%02d' % (hour, minute) if hour < 24 and minute < 60 else fallback


def parse_service_intent(text, profile=None):
    """Turn a short Chinese service request into a transparent, non-mutating draft."""
    clean = ' '.join(str(text or '').strip().split())[:300]
    if not clean:
        raise ValueError('请先描述你希望深脉如何主动服务')
    data = profile if isinstance(profile, dict) else (load_profile().get('data') or {})
    current_routine = normalize_routine_config(data.get('market_routine'))
    current_prefs = data.get('attention_preferences') or {}
    routine_patch, prefs_patch, understood, unresolved = {}, {}, [], []

    task_words = {
        'pre_market': ('盘前', '盘前准备'),
        'intraday': ('盘中', '盘中检查'),
        'close_review': ('盘后|收盘后|收盘', '收盘复盘'),
    }
    for key, (pattern, label) in task_words.items():
        if re.search(r'(不要|取消|关闭|停止)[^，。；]{0,6}(?:%s)|(?:%s)[^，。；]{0,6}(不要|取消|关闭|停止)' % (pattern, pattern), clean):
            routine_patch[key] = False
            understood.append('关闭%s' % label)
        elif re.search(pattern, clean):
            routine_patch[key] = True
            understood.append('开启%s' % label)

    if re.search(r'只(报|提醒|推送).{0,4}(重要|高优先)|重要.{0,4}才(报|提醒|推送)', clean):
        prefs_patch['mode'] = 'high_only'
        understood.append('只主动推送重要提醒')
    elif re.search(r'(只|都).{0,5}(提醒中心|中心)|(不要|关闭).{0,4}(弹窗|推送)', clean):
        prefs_patch['mode'] = 'center_only'
        understood.append('提醒只收入提醒中心')
    elif '平衡' in clean or '正常提醒' in clean:
        prefs_patch['mode'] = 'balanced'
        understood.append('使用平衡提醒模式')

    if re.search(r'(不要|关闭|取消).{0,5}(静默|安静|免打扰|勿扰)', clean):
        prefs_patch['quietEnabled'] = False
        understood.append('关闭安静时段')
    elif re.search(r'(晚上|夜间|睡觉|下班后).{0,6}(别|不要|停止|免).{0,4}(打扰|提醒|推送)|免打扰|勿扰', clean):
        prefs_patch.update(quietEnabled=True, quietStart='22:30', quietEnd='08:00')
        understood.append('开启夜间安静时段 22:30–08:00')

    range_match = re.search(
        r'(\d{1,2})(?:[:点时](\d{1,2}))?\s*(?:到|至|[-~—])\s*(\d{1,2})(?:[:点时](\d{1,2}))?', clean)
    if range_match:
        start = _valid_clock('%s:%s' % (range_match.group(1), range_match.group(2) or '00'), '22:30')
        end = _valid_clock('%s:%s' % (range_match.group(3), range_match.group(4) or '00'), '08:00')
        prefs_patch.update(quietEnabled=True, quietStart=start, quietEnd=end)
        understood = [row for row in understood if not row.startswith('开启夜间安静时段')]
        understood.append('设置安静时段 %s–%s' % (start, end))

    focus_match = re.search(r'(?:关注|看|盯|跟踪)\s*([\u4e00-\u9fa5A-Za-z0-9]{2,16})', clean)
    focus_text = focus_match.group(1) if focus_match else None
    if focus_text:
        unresolved.append('识别到关注对象“%s”，本次不会自动修改自选股' % focus_text)
    if not routine_patch and not prefs_patch:
        unresolved.append('没有识别出可应用的时段或提醒偏好')

    draft = {
        'marketRoutine': {'tasks': dict(current_routine['tasks'], **routine_patch)},
        'attentionPreferences': prefs_patch,
        'focusText': focus_text,
    }
    changes = []
    for key, value in routine_patch.items():
        if current_routine['tasks'].get(key) != value:
            changes.append({'field': 'marketRoutine.tasks.%s' % key,
                            'from': current_routine['tasks'].get(key), 'to': value})
    normalized_prefs = _delivery_preferences(data)
    for key, value in prefs_patch.items():
        if normalized_prefs.get(key) != value:
            changes.append({'field': 'attentionPreferences.%s' % key,
                            'from': normalized_prefs.get(key), 'to': value})
    return {
        'schema': 1, 'input': clean, 'confidence': min(0.98, 0.45 + len(understood) * 0.13),
        'understood': understood, 'unresolved': unresolved, 'changes': changes, 'draft': draft,
        'requires_confirmation': True,
        'boundary': '这只是本机规则草稿；确认前不会修改设置，也不会更改自选或执行交易。',
    }


def apply_service_plan_draft(draft, confirmed=False):
    if confirmed is not True:
        raise ValueError('必须由用户明确确认后才能应用服务草稿')
    if not isinstance(draft, dict):
        raise ValueError('服务草稿格式无效')
    data = load_profile().get('data') or {}
    routine_source = draft.get('marketRoutine') if isinstance(draft.get('marketRoutine'), dict) else {}
    routine = normalize_routine_config(routine_source)
    previous = normalize_routine_config(data.get('market_routine'))
    routine['enabled_at'] = (previous.get('enabled_at') or now_bj().isoformat(timespec='seconds')) if routine['enabled'] else None
    routine['paused_until'] = previous.get('paused_until')

    preferences = json.loads(json.dumps(data.get('attention_preferences') or {}, ensure_ascii=False))
    patch = draft.get('attentionPreferences') if isinstance(draft.get('attentionPreferences'), dict) else {}
    allowed = {'mode', 'quietEnabled', 'quietStart', 'quietEnd'}
    for key in allowed:
        if key in patch:
            preferences[key] = patch[key]
    if preferences.get('mode') not in {'balanced', 'high_only', 'center_only'}:
        preferences['mode'] = 'balanced'
    preferences['quietEnabled'] = preferences.get('quietEnabled') is not False
    preferences['quietStart'] = _valid_clock(preferences.get('quietStart'), '22:30')
    preferences['quietEnd'] = _valid_clock(preferences.get('quietEnd'), '08:00')
    saved = save_profile({'market_routine': routine, 'attention_preferences': preferences})
    _routine_wake.set()
    return saved


def _routine_due_kind(current, calendar_loader=None):
    """Return the currently due service window. Confirmed non-trading dates are skipped."""
    calendar = (calendar_loader or market_calendar_info)(current)
    if not calendar.get('is_trade_date'):
        return None
    minute = current.hour * 60 + current.minute
    if 8 * 60 + 45 <= minute <= 9 * 60 + 25:
        return 'pre_market'
    if 10 * 60 + 15 <= minute <= 11 * 60 + 30 or 13 * 60 <= minute <= 14 * 60 + 45:
        return 'intraday'
    if 15 * 60 + 10 <= minute <= 21 * 60 + 30:
        return 'close_review'
    return None


def _routine_occurrences(start, days=8):
    windows = (
        ('pre_market', 8, 45),
        ('intraday', 10, 15),
        ('close_review', 15, 10),
    )
    base = start.astimezone(BJC)
    for offset in range(days):
        day = (base + timedelta(days=offset)).date()
        if day.weekday() >= 5:
            continue
        anchor = datetime(day.year, day.month, day.day, 8, 0, tzinfo=BJC)
        if not market_calendar_info(anchor).get('is_trade_date'):
            continue
        for kind, hour, minute in windows:
            yield kind, datetime(day.year, day.month, day.day, hour, minute, tzinfo=BJC)


def _routine_receipt_ids(profile=None):
    data = profile if isinstance(profile, dict) else (load_profile().get('data') or {})
    rows = list(data.get('routine_receipts') or []) + list(data.get('routine_skips') or [])
    return {str(row.get('id')) for row in rows if isinstance(row, dict) and row.get('id')}


def _routine_is_paused(config, current=None):
    value = config.get('paused_until') if isinstance(config, dict) else None
    if not value:
        return False
    try:
        until = datetime.fromisoformat(value)
        if until.tzinfo is None:
            until = until.replace(tzinfo=BJC)
        return (current or now_bj()).astimezone(BJC) < until.astimezone(BJC)
    except (TypeError, ValueError):
        return False


def next_routine_service(current=None, config=None, profile=None):
    now = (current or now_bj()).astimezone(BJC)
    cfg = config or load_routine_config()
    if _routine_is_paused(cfg, now):
        return None
    receipts = _routine_receipt_ids(profile)
    due = _routine_due_kind(now)
    if due and cfg['tasks'].get(due):
        receipt_id = 'routine:%s:%s' % (due, now.strftime('%Y-%m-%d'))
        if receipt_id not in receipts:
            return {'kind': due, 'label': ROUTINE_LABELS[due],
                    'at': now.isoformat(timespec='minutes'), 'due_now': True}
    for kind, at in _routine_occurrences(now):
        if not cfg['tasks'].get(kind) or at < now:
            continue
        receipt_id = 'routine:%s:%s' % (kind, at.strftime('%Y-%m-%d'))
        if receipt_id not in receipts:
            return {'kind': kind, 'label': ROUTINE_LABELS[kind], 'at': at.isoformat(timespec='minutes')}
    return None


def routine_timeline(current=None, config=None, profile=None, limit=6):
    now = (current or now_bj()).astimezone(BJC)
    cfg = config or load_routine_config()
    data = profile if isinstance(profile, dict) else (load_profile().get('data') or {})
    receipts = {str(row.get('id')) for row in (data.get('routine_receipts') or [])
                if isinstance(row, dict)}
    skips = {str(row.get('id')) for row in (data.get('routine_skips') or [])
             if isinstance(row, dict)}
    paused = _routine_is_paused(cfg, now)
    rows = []
    for kind, at in _routine_occurrences(now.replace(hour=0, minute=0, second=0, microsecond=0), days=10):
        if not cfg['tasks'].get(kind):
            continue
        item_id = 'routine:%s:%s' % (kind, at.strftime('%Y-%m-%d'))
        if item_id in receipts:
            state = 'completed'
        elif item_id in skips:
            state = 'skipped'
        elif paused and at >= now:
            state = 'paused'
        elif at < now:
            state = 'missed'
        else:
            state = 'upcoming'
        rows.append({'id': item_id, 'kind': kind, 'label': ROUTINE_LABELS[kind],
                     'at': at.isoformat(timespec='minutes'), 'state': state})
        if len(rows) >= max(3, min(int(limit or 6), 12)):
            break
    return rows


def mutate_routine_action(action, current=None):
    now = (current or now_bj()).astimezone(BJC)
    clean_action = str(action or '').strip()
    data = load_profile().get('data') or {}
    cfg = normalize_routine_config(data.get('market_routine'))
    patch = {}
    if clean_action == 'pause_until_morning':
        tomorrow = now + timedelta(days=1)
        cfg['paused_until'] = tomorrow.replace(hour=8, minute=0, second=0, microsecond=0).isoformat(timespec='minutes')
        patch['market_routine'] = cfg
    elif clean_action == 'resume':
        cfg['paused_until'] = None
        patch['market_routine'] = cfg
    elif clean_action == 'skip_next':
        next_service = next_routine_service(now, cfg, data)
        if not next_service:
            raise ValueError('当前没有可跳过的下一次服务')
        service_day = next_service['at'][:10]
        item_id = 'routine:%s:%s' % (next_service['kind'], service_day)
        skips = [row for row in (data.get('routine_skips') or [])
                 if isinstance(row, dict) and row.get('id') != item_id]
        skips.append({'id': item_id, 'kind': next_service['kind'], 'serviceDate': service_day,
                      'skippedAt': now.isoformat(timespec='seconds')})
        patch['routine_skips'] = skips
    else:
        raise ValueError('不支持的主动服务操作')
    saved = save_profile(patch)
    _routine_wake.set()
    return saved


def routine_effectiveness_status(profile=None):
    """Explain routine value from explicit feedback; silence and opens are never inferred."""
    data = profile if isinstance(profile, dict) else (load_profile().get('data') or {})
    cfg = normalize_routine_config(data.get('market_routine'))
    inbox = {str(row.get('id')): row for row in (data.get('attention_inbox') or [])
             if isinstance(row, dict) and row.get('id')}
    receipts = [row for row in (data.get('routine_receipts') or []) if isinstance(row, dict)]
    feedback = []
    for row in (data.get('attention_feedback') or []):
        if not isinstance(row, dict) or row.get('signal') not in ATTENTION_FEEDBACK_SIGNALS:
            continue
        target = inbox.get(str(row.get('itemId') or '')) or {}
        routine_kind = row.get('routineKind') or target.get('routineKind')
        if row.get('kind') != 'routine' and not routine_kind:
            continue
        if routine_kind in ROUTINE_LABELS:
            feedback.append(dict(row, routineKind=routine_kind))

    periods = []
    recommendations = []
    for kind, label in ROUTINE_LABELS.items():
        rows = [row for row in feedback if row.get('routineKind') == kind]
        counts = {signal: sum(1 for row in rows if row.get('signal') == signal)
                  for signal in ATTENTION_FEEDBACK_SIGNALS}
        generated = sum(1 for row in receipts if row.get('kind') == kind)
        positive = counts['helpful'] + counts['done']
        negative = counts['too_frequent'] + counts['irrelevant']
        if not rows:
            outcome = '等待明确反馈'
        elif positive > negative:
            outcome = '明确反馈偏正向'
        elif negative > positive:
            outcome = '可能需要调整节奏'
        else:
            outcome = '反馈暂时均衡'
        periods.append({
            'kind': kind, 'label': label, 'enabled': cfg['tasks'].get(kind) is True,
            'generated': generated, 'feedbackCount': len(rows), 'counts': counts,
            'helpedCount': positive, 'completedCount': counts['done'],
            'negativeCount': negative, 'outcome': outcome,
        })
        if cfg['tasks'].get(kind) and len(rows) >= 3 and negative >= 2 and negative > positive:
            suggestion_id = 'routine-effect:disable:%s:%d:%d' % (kind, len(rows), negative)
            recommendations.append({
                'id': suggestion_id, 'kind': kind, 'label': label,
                'action': 'disable_task',
                'title': '建议暂时关闭%s' % label,
                'reason': '%d 次明确反馈中，%d 次选择“少一点”或“不相关”，正向反馈 %d 次。' % (
                    len(rows), negative, positive),
                'requiresConfirmation': True,
                'reversible': True,
            })

    actions = [row for row in (data.get('routine_effect_actions') or []) if isinstance(row, dict)]
    active_actions = [row for row in actions if not row.get('undoneAt')]
    totals = {
        'generated': len(receipts),
        'feedbackCount': len(feedback),
        'helpedCount': sum(1 for row in feedback if row.get('signal') in {'helpful', 'done'}),
        'completedCount': sum(1 for row in feedback if row.get('signal') == 'done'),
        'negativeCount': sum(1 for row in feedback if row.get('signal') in {'too_frequent', 'irrelevant'}),
    }
    return {
        'schema': 1, 'totals': totals, 'periods': periods,
        'recommendations': recommendations,
        'activeActions': active_actions[-5:],
        'basis': 'explicit-feedback-only',
        'measurementBoundary': ('只统计用户明确选择的“有用、已完成、少一点、不相关”；'
                                '未反馈、打开、停留时间和页面浏览均不计为负面或完成。'),
        'automaticChanges': False,
        'automaticTradingActions': False,
    }


def mutate_routine_effect(action, suggestion_id=None, action_id=None, confirmed=False):
    clean_action = str(action or '').strip()
    if clean_action == 'apply_suggestion' and confirmed is not True:
        raise ValueError('必须由用户明确确认后才能调整主动服务节奏')
    with _profile_lock:
        current = _read_profile_unlocked()
        data = current['data']
        cfg = normalize_routine_config(data.get('market_routine'))
        history = [row for row in (data.get('routine_effect_actions') or [])
                   if isinstance(row, dict)]
        now = now_bj().isoformat(timespec='seconds')
        if clean_action == 'apply_suggestion':
            status = routine_effectiveness_status(data)
            suggestion = next((row for row in status['recommendations']
                               if row.get('id') == str(suggestion_id or '')), None)
            if not suggestion or suggestion.get('action') != 'disable_task':
                raise ValueError('该节奏建议已失效，请刷新后重试')
            kind = suggestion['kind']
            previous = cfg['tasks'].get(kind) is True
            cfg['tasks'][kind] = False
            cfg['enabled'] = any(cfg['tasks'].values())
            if not cfg['enabled']:
                cfg['enabled_at'] = None
            record = {
                'id': 'routine-effect-action:%d' % int(time.time() * 1000),
                'suggestionId': suggestion['id'], 'kind': kind,
                'label': suggestion['label'], 'action': 'disable_task',
                'previousEnabled': previous, 'appliedAt': now,
                'reason': suggestion['reason'], 'undoneAt': None,
            }
            history.append(record)
        elif clean_action == 'undo':
            requested = str(action_id or '').strip()
            record = next((row for row in reversed(history)
                          if row.get('id') == requested and not row.get('undoneAt')), None)
            if not record:
                raise ValueError('没有找到可撤销的节奏调整')
            kind = record.get('kind')
            later = next((row for row in history if row.get('kind') == kind
                          and not row.get('undoneAt') and str(row.get('appliedAt') or '')
                          > str(record.get('appliedAt') or '')), None)
            if later:
                raise ValueError('该时段已有更新的调整，请先处理最新记录')
            cfg['tasks'][kind] = record.get('previousEnabled') is True
            cfg['enabled'] = any(cfg['tasks'].values())
            if cfg['enabled'] and not cfg.get('enabled_at'):
                cfg['enabled_at'] = now
            record['undoneAt'] = now
        else:
            raise ValueError('不支持的服务效果操作')
        data['market_routine'] = cfg
        data['routine_effect_actions'] = history[-PROFILE_LIST_LIMITS['routine_effect_actions']:]
        saved = _write_profile_unlocked(current)
    _routine_wake.set()
    return saved, routine_effectiveness_status(saved['data'])


def _journal_has_date(rows, data_date):
    compact = str(data_date or '').replace('-', '')
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        value = str(row.get('date') or row.get('dataDate') or row.get('data_date') or '')
        if value.replace('-', '') == compact:
            return True
    return False


def build_routine_attention(kind, current=None, emotion_loader=None):
    now = (current or now_bj()).astimezone(BJC)
    profile = load_profile().get('data') or {}
    created = int(now.timestamp() * 1000)
    day = now.strftime('%Y-%m-%d')
    common = {
        'id': 'routine:%s:%s' % (kind, day),
        'fingerprint': 'routine:%s:%s' % (kind, day),
        'kind': 'routine',
        'priority': 'medium',
        'delivery': 'digest',
        'createdAt': created,
        'expiresAt': created + ({'pre_market': 8, 'intraday': 4, 'close_review': 36}.get(kind, 24)
                                * 60 * 60 * 1000),
        'readAt': None,
        'routineKind': kind,
        'serviceDate': day,
    }
    if kind == 'pre_market':
        snapshots = load_history()
        latest = snapshots[-1] if snapshots else {}
        pending = sum(1 for row in (profile.get('alerts') or [])
                      if isinstance(row, dict) and not row.get('triggered'))
        watched = len(profile.get('watchlist') or [])
        data_date = latest.get('date') or None
        if latest:
            detail = ('上一交易日 %s：情绪 %s°，%s；%d 只自选、%d 条待触发提醒。'
                      '先核对隔夜信息与数据时点，再形成今日观察清单。' % (
                          data_date, latest.get('temp', '--'), latest.get('phase', '阶段待确认'),
                          watched, pending))
        else:
            detail = ('尚无可用收盘快照；已发现 %d 只自选、%d 条待触发提醒。'
                      '请先检查数据源，再建立今日观察清单。' % (watched, pending))
        return dict(common, title='盘前研究清单已准备', detail=detail,
                    reason='你已授权深脉在工作日盘前整理研究任务', page='overview',
                    dataDate=data_date, degraded=not bool(latest),
                    evidence='最近收盘快照 + 本机自选与提醒；不把工作日等同于已确认交易日')

    loader = emotion_loader or assemble_emotion
    payload = loader(kind == 'close_review')
    engine = payload.get('engine') or {}
    raw = engine.get('raw') or {}
    data_date = payload.get('date') or engine.get('date') or None
    same_day = data_date == day
    degraded = bool(engine.get('degraded') or not same_day)
    temp = engine.get('temp')
    phase = engine.get('phase') or '阶段待确认'
    temp_text = '%s°' % temp if temp is not None else '温度待确认'
    breadth = payload.get('breadth') or {}
    structure = '涨停 %s / 跌停 %s / 炸板 %s；上涨 %s / 下跌 %s' % (
        raw.get('zt', '--'), raw.get('dt', '--'), raw.get('zb', '--'),
        breadth.get('up', raw.get('up', '--')), breadth.get('down', raw.get('down', '--')))
    quality = ('最新可用数据日为 %s，并非今日；不生成当日方向结论。' % (data_date or '待确认')
               if not same_day else ('数据受限，先修复数据源；' if degraded else ''))
    if kind == 'intraday':
        title = ('盘中结构检查：%s · %s' % (phase, temp_text)
                 if same_day else '盘中数据尚未更新')
        return dict(common, title=title,
                    detail='%s%s。数据日 %s；只提示结构变化，不追逐分时噪声。' % (
                        quality, structure, data_date or '待确认'),
                    reason='你已授权深脉在盘中形成一次结构检查',
                    page='emotion' if same_day else 'datasrc',
                    dataDate=data_date, degraded=degraded,
                    evidence='情绪引擎聚合快照；事实与推断需在详情页分开核对')

    has_journal = same_day and _journal_has_date(profile.get('journal') or [], data_date)
    journal_text = (('今日复盘已保存，建议核对反证条件与明日观察点。' if has_journal
                     else '今日复盘尚未保存，建议补充判断、反证条件与明日观察点。')
                    if same_day else '未确认今日存在新收盘数据，因此不催写当日复盘。')
    title = ('收盘复盘：%s · %s' % (phase, temp_text)
             if same_day else '收盘数据尚未更新')
    return dict(common, title=title,
                detail='%s%s。数据日 %s；%s' % (quality, structure, data_date or '待确认', journal_text),
                reason='你已授权深脉在收盘后整理复盘任务',
                page='strategy' if same_day else 'datasrc',
                dataDate=data_date, degraded=degraded, journalSaved=has_journal,
                evidence='收盘后情绪快照 + 本机复盘记录；不构成投资建议')


def commit_routine_attention(item):
    """Publish at most one item per service kind and calendar date."""
    if not isinstance(item, dict) or not item.get('id'):
        raise ValueError('routine attention item is required')
    item_id = str(item['id'])[:160]
    with _profile_lock:
        current = _read_profile_unlocked()
        receipts = [row for row in (current['data'].get('routine_receipts') or [])
                    if isinstance(row, dict)]
        if any(row.get('id') == item_id for row in receipts):
            return False
        inbox = [row for row in (current['data'].get('attention_inbox') or [])
                 if isinstance(row, dict) and row.get('id') != item_id]
        clean = json.loads(json.dumps(item, ensure_ascii=False))
        clean['id'] = item_id
        inbox.append(clean)
        receipts.append({
            'id': item_id,
            'kind': clean.get('routineKind'),
            'serviceDate': clean.get('serviceDate'),
            'dataDate': clean.get('dataDate'),
            'createdAt': clean.get('createdAt'),
        })
        current['data']['attention_inbox'] = inbox[-PROFILE_LIST_LIMITS['attention_inbox']:]
        current['data']['routine_receipts'] = receipts[-PROFILE_LIST_LIMITS['routine_receipts']:]
        _write_profile_unlocked(current)
        return True


def process_market_routine_once(current=None, emotion_loader=None):
    now = (current or now_bj()).astimezone(BJC)
    cfg = load_routine_config()
    kind = _routine_due_kind(now)
    if not cfg['enabled']:
        return {'state': 'disabled', 'published': 0, 'kind': None}
    if _routine_is_paused(cfg, now):
        return {'state': 'paused', 'published': 0, 'kind': kind}
    if not kind or not cfg['tasks'].get(kind):
        return {'state': 'waiting', 'published': 0, 'kind': kind}
    receipt_id = 'routine:%s:%s' % (kind, now.strftime('%Y-%m-%d'))
    if receipt_id in _routine_receipt_ids():
        return {'state': 'completed_window', 'published': 0, 'kind': kind}
    item = build_routine_attention(kind, now, emotion_loader)
    published = 1 if commit_routine_attention(item) else 0
    return {'state': 'published' if published else 'completed_window',
            'published': published, 'kind': kind, 'item': item if published else None}


def market_routine_status(current=None):
    now = (current or now_bj()).astimezone(BJC)
    cfg = load_routine_config()
    profile = load_profile().get('data') or {}
    due = _routine_due_kind(now)
    calendar = market_calendar_info(now)
    today = now.strftime('%Y-%m-%d')
    completed = [row.get('kind') for row in (profile.get('routine_receipts') or [])
                 if isinstance(row, dict) and row.get('serviceDate') == today]
    skipped = [row.get('kind') for row in (profile.get('routine_skips') or [])
               if isinstance(row, dict) and row.get('serviceDate') == today]
    next_service = next_routine_service(now, cfg, profile)
    with _routine_lock:
        runtime = dict(_routine_runtime)
    state = runtime.get('state') or 'disabled'
    if not cfg['enabled']:
        state = 'disabled'
    elif _routine_is_paused(cfg, now):
        state = 'paused'
    elif not calendar.get('is_trade_date'):
        state = 'non_trading_day'
    elif due and cfg['tasks'].get(due) and due in completed:
        state = 'completed_window'
    elif due and cfg['tasks'].get(due):
        state = 'due'
    elif state not in ('error',):
        state = 'waiting'
    return {
        'config': cfg,
        'runtime': dict(runtime, state=state,
                        next_service_at=next_service.get('at') if next_service else None),
        'due_kind': due,
        'completed_today': completed,
        'skipped_today': skipped,
        'next_service': next_service,
        'timeline': routine_timeline(now, cfg, profile),
        'effectiveness': routine_effectiveness_status(profile),
        'calendar': calendar,
        'service_continues_when_page_closed': True,
        'service_stops_when_local_server_stops': True,
        'calendar_note': ('按北京时间与%s运行；每条提醒明确数据日，旧快照不会冒充当日行情。'
                          % calendar.get('basis', '交易日历')),
    }


def _market_routine_loop():
    with _routine_lock:
        _routine_runtime['thread_running'] = True
    while not _routine_stop.is_set():
        checked_at = now_bj()
        wait_seconds = 30
        try:
            result = process_market_routine_once(checked_at)
            next_service = next_routine_service(checked_at)
            with _routine_lock:
                _routine_runtime.update(
                    state=result['state'],
                    last_check_at=checked_at.isoformat(timespec='seconds'),
                    last_run_at=(checked_at.isoformat(timespec='seconds')
                                 if result.get('published') else _routine_runtime.get('last_run_at')),
                    last_run_kind=(result.get('kind')
                                   if result.get('published') else _routine_runtime.get('last_run_kind')),
                    next_service_at=next_service.get('at') if next_service else None,
                    last_error=None,
                    checks=int(_routine_runtime.get('checks') or 0) + 1,
                    published_count=int(_routine_runtime.get('published_count') or 0)
                    + int(result.get('published') or 0),
                )
        except Exception as exc:
            with _routine_lock:
                _routine_runtime.update(state='error', last_check_at=checked_at.isoformat(timespec='seconds'),
                                        last_error=str(exc)[:240])
        _routine_wake.wait(wait_seconds)
        _routine_wake.clear()
    with _routine_lock:
        _routine_runtime.update(thread_running=False, state='stopped', next_service_at=None)


def start_market_routine():
    global _routine_thread
    if _routine_thread and _routine_thread.is_alive():
        return
    _routine_stop.clear()
    _routine_thread = threading.Thread(target=_market_routine_loop,
                                       name='deeppulse-market-routine', daemon=True)
    _routine_thread.start()


def stop_market_routine():
    _routine_stop.set()
    _routine_wake.set()
    thread = _routine_thread
    if thread and thread.is_alive():
        thread.join(timeout=3)


# ---------------------------------------------------------------- 事件影响雷达（单独授权、透明规则、跨端主动服务）

_event_service_lock = threading.Lock()
_event_service_stop = threading.Event()
_event_service_wake = threading.Event()
_event_service_thread = None
_event_service_runtime = {
    'thread_running': False,
    'state': 'disabled',
    'last_check_at': None,
    'last_success_at': None,
    'next_check_at': None,
    'last_error': None,
    'checks': 0,
    'published_count': 0,
    'latest_summary': None,
    'last_evidence_check_at': None,
    'last_evidence_error': None,
    'evidence_checks': 0,
    'evidence_candidates': 0,
}

HYPOTHESIS_EVIDENCE_INTERVAL_SECONDS = 900


def normalize_event_service_config(value=None):
    source = value if isinstance(value, dict) else {}
    scopes = source.get('scopes') if isinstance(source.get('scopes'), dict) else {}
    try:
        interval = int(source.get('interval_seconds', source.get('intervalSeconds', 300)))
    except (TypeError, ValueError):
        interval = 300
    delivery = str(source.get('delivery') or 'digest')
    return {
        'enabled': source.get('enabled') is True,
        'scopes': {
            'macro': scopes.get('macro') is not False,
            'market_news': scopes.get('market_news', scopes.get('marketNews')) is not False,
        },
        'watchlist_link': source.get('watchlist_link', source.get('watchlistLink')) is not False,
        'delivery': delivery if delivery in ('digest', 'center_only') else 'digest',
        'interval_seconds': max(180, min(1800, interval)),
        'enabled_at': str(source.get('enabled_at') or source.get('enabledAt') or '')[:40] or None,
        'consent_version': 'event-impact-v1' if source.get('enabled') is True else None,
    }


def load_event_service_config():
    profile = load_profile().get('data') or {}
    return normalize_event_service_config(profile.get('event_service'))


def save_event_service_config(value):
    cfg = normalize_event_service_config(value)
    previous = load_event_service_config()
    if cfg['enabled'] and not previous['enabled']:
        cfg['enabled_at'] = now_bj().isoformat(timespec='seconds')
    elif not cfg['enabled']:
        cfg['enabled_at'] = None
        cfg['consent_version'] = None
    saved = save_profile({'event_service': cfg})
    _event_service_wake.set()
    return saved


def _frame_records(frame):
    if frame is None:
        return []
    try:
        return frame.to_dict(orient='records')
    except Exception:
        return []


def akshare_macro_event_rows(day):
    module = load_akshare()
    if module is None:
        raise RuntimeError('AKShare 未安装或导入失败')
    result = {'calendar': [], 'corroboration': [], 'errors': []}
    for name, key in (('macro_info_ws', 'calendar'), ('news_economic_baidu', 'corroboration')):
        if not hasattr(module, name):
            result['errors'].append('%s 接口不可用' % name)
            continue
        started = time.monotonic()
        try:
            frame = getattr(module, name)(date=day)
            result[key] = _frame_records(frame)
            _record_source('akshare:' + name, True, (time.monotonic() - started) * 1000)
        except Exception as exc:
            _record_source('akshare:' + name, False, (time.monotonic() - started) * 1000, exc)
            result['errors'].append('%s: %s' % (name, str(exc)[:140]))
    return result


def collect_event_impact(current=None, profile_data=None, macro_loader=None, news_loader=None,
                         stock_loader=None):
    now = (current or now_bj()).astimezone(BJC)
    profile = profile_data if isinstance(profile_data, dict) else (load_profile().get('data') or {})
    cfg = normalize_event_service_config(profile.get('event_service'))
    base = {
        'enabled': cfg['enabled'], 'config': cfg,
        'authorization': {
            'required': True, 'granted': cfg['enabled'],
            'grantedAt': cfg.get('enabled_at'),
            'scope': [key for key, value in cfg['scopes'].items() if value],
            'statement': '仅在用户明确开启后读取宏观日历和市场快讯；不会连接交易账户或自动下单。',
        },
        'serviceContinuesWhenPageClosed': True,
        'serviceStopsWhenLocalServerStops': True,
    }
    if not cfg['enabled']:
        return dict(base, state='disabled', impact={
            'modelVersion': EVENT_IMPACT_MODEL_VERSION,
            'dataDate': now.strftime('%Y-%m-%d'), 'generatedAt': None,
            'summary': {'events': 0, 'linkedEvents': 0, 'watchMatches': 0, 'highImportance': 0},
            'items': [],
            'method': {'relation': 'rule-based-sensitivity', 'causal': False,
                       'statement': '尚未授权，未访问事件数据源。'},
        }, sources=[], errors=[])
    if build_event_impact is None:
        return dict(base, state='error', impact=None, sources=[],
                    errors=['事件影响模型不可用'])
    day = now.strftime('%Y%m%d')
    errors = []
    macro = {'calendar': [], 'corroboration': [], 'errors': []}
    if cfg['scopes']['macro']:
        try:
            loader = macro_loader or (lambda d: cached(
                'event_macro_' + d, 15 * 60, lambda: akshare_macro_event_rows(d)))
            macro = loader(day)
            errors.extend(macro.get('errors') or [])
        except Exception as exc:
            errors.append('AKShare 宏观日历: %s' % str(exc)[:160])
    market_rows = []
    if cfg['scopes']['market_news']:
        try:
            loader = news_loader or (lambda: cached('news', 90, em_news))
            market_rows = loader() or []
        except Exception as exc:
            errors.append('市场快讯: %s' % str(exc)[:160])
    try:
        catalog_loader = stock_loader or (lambda: cached('all_stocks', 2 * 3600, em_all_stocks))
        catalog = catalog_loader() if cfg['watchlist_link'] else []
    except Exception as exc:
        catalog = []
        errors.append('自选行业目录: %s' % str(exc)[:160])
    observed = now.isoformat(timespec='seconds')
    impact = build_event_impact(
        macro.get('calendar') or [], macro.get('corroboration') or [], market_rows,
        profile.get('watchlist') if cfg['watchlist_link'] else [], catalog,
        data_date=now.strftime('%Y-%m-%d'), observed_at=observed)
    sources = []
    if macro.get('calendar'):
        sources.append({'id': 'akshare:macro_info_ws', 'name': 'AKShare·华尔街见闻宏观日历',
                        'tier': 'enrichment', 'observedAt': observed})
    if macro.get('corroboration'):
        sources.append({'id': 'akshare:news_economic_baidu', 'name': 'AKShare·百度全球宏观事件',
                        'tier': 'enrichment', 'observedAt': observed})
    if market_rows:
        sources.append({'id': 'eastmoney:news', 'name': '东方财富快讯',
                        'tier': 'market', 'observedAt': observed})
    state = 'ok' if sources and not errors else ('degraded' if sources else 'unavailable')
    return dict(base, state=state, impact=impact, sources=sources, errors=errors[:6])


def commit_event_attention(snapshot, limit=3):
    """Atomically publish new, relevant event paths and remember delivery receipts."""
    if not isinstance(snapshot, dict) or not snapshot.get('enabled'):
        return 0
    impact = snapshot.get('impact') or {}
    candidates = []
    for row in impact.get('items') or []:
        event = row.get('event') or {}
        watches = row.get('watchlist') or []
        has_market_basis = bool(row.get('sectors')) and (event.get('importance') or 0) >= 3
        if watches or has_market_basis:
            candidates.append(row)
    published = 0
    timestamp = int(time.time() * 1000)
    with _profile_lock:
        current = _read_profile_unlocked()
        receipts = [row for row in (current['data'].get('event_receipts') or [])
                    if isinstance(row, dict)]
        receipt_ids = {str(row.get('id') or '') for row in receipts}
        inbox = [row for row in (current['data'].get('attention_inbox') or [])
                 if isinstance(row, dict)]
        cfg = normalize_event_service_config(current['data'].get('event_service'))
        for row in candidates:
            if published >= max(1, min(int(limit or 3), 5)):
                break
            event = row.get('event') or {}
            item_id = 'event:' + str(event.get('id') or '')[:40]
            if item_id in receipt_ids:
                continue
            watches = row.get('watchlist') or []
            names = [str(item.get('name') or item.get('code') or '') for item in watches[:3]]
            sectors = [str(value) for value in (row.get('sectors') or [])[:3]]
            link = ('命中自选：%s' % '、'.join(names)) if names else ('敏感行业：%s' % '、'.join(sectors))
            quality = row.get('quality') or {}
            sources = event.get('sources') or []
            source_names = ' / '.join(str(source.get('name') or '') for source in sources[:2])
            item = {
                'id': item_id, 'fingerprint': item_id, 'kind': 'event',
                'priority': 'high' if any(item.get('match') == 'direct' for item in watches) else 'medium',
                'delivery': cfg['delivery'], 'title': str(event.get('title') or '新的市场事件')[:160],
                'detail': ('%s；质量分 %s。规则只表示敏感性，相关性不等于因果。'
                           % (link or '尚未命中自选', quality.get('score', '--'))),
                'reason': '你已授权事件影响雷达；来源 %s，观测时间 %s' % (
                    source_names or '待核对', event.get('observedAt') or '待确认'),
                'page': 'overview', 'createdAt': timestamp,
                'expiresAt': timestamp + (8 if event.get('type') == 'headline' else 24) * 60 * 60 * 1000,
                'readAt': None,
                'eventImpact': {
                    'eventId': event.get('id'), 'scheduledAt': event.get('scheduledAt'),
                    'sectors': sectors, 'watchlist': [item.get('code') for item in watches[:6]],
                    'watchlistLabels': [item.get('name') or item.get('code') for item in watches[:6]],
                    'matchTypes': [item.get('match') for item in watches[:6]],
                    'matchedKeywordCount': max(
                        [len(rule.get('matchedKeywords') or []) for rule in (row.get('rules') or [])] or [0]),
                    'qualityScore': quality.get('score'), 'causal': False,
                },
            }
            inbox = [old for old in inbox if old.get('id') != item_id]
            inbox.append(item)
            receipts.append({'id': item_id, 'eventId': event.get('id'),
                             'createdAt': timestamp, 'dataDate': impact.get('dataDate')})
            receipt_ids.add(item_id)
            published += 1
        if published:
            current['data']['attention_inbox'] = inbox[-PROFILE_LIST_LIMITS['attention_inbox']:]
            current['data']['event_receipts'] = receipts[-PROFILE_LIST_LIMITS['event_receipts']:]
            _write_profile_unlocked(current)
    return published


def process_event_service_once(current=None, collector=None):
    snapshot = (collector or collect_event_impact)(current)
    if not snapshot.get('enabled'):
        return {'state': 'disabled', 'published': 0, 'snapshot': snapshot}
    published = commit_event_attention(snapshot)
    return {'state': snapshot.get('state') or 'unavailable', 'published': published,
            'snapshot': snapshot}


def event_service_status(include_impact=False):
    cfg = load_event_service_config()
    with _event_service_lock:
        runtime = dict(_event_service_runtime)
    state = runtime.get('state') or 'disabled'
    if not cfg['enabled']:
        state = 'disabled'
    elif state in ('disabled', 'stopped'):
        state = 'starting'
    result = {
        'config': cfg, 'runtime': dict(runtime, state=state),
        'authorization': {
            'required': True, 'granted': cfg['enabled'], 'grantedAt': cfg.get('enabled_at'),
            'statement': '明确授权后才访问事件源；关闭后立即停止新检查。',
        },
        'service_continues_when_page_closed': True,
        'service_stops_when_local_server_stops': True,
    }
    if include_impact:
        snapshot = collect_event_impact()
        result.update(snapshot)
    return result


def _readable_research_text(value):
    text = str(value or '').strip()
    return bool(text) and '�' not in text and text.count('?') < max(3, int(len(text) * .12))


def _repair_hypothesis_display(item):
    """Repair only the response view of legacy mojibake; persisted records stay unchanged."""
    row = dict(item)
    baseline = dict(row.get('baseline')) if isinstance(row.get('baseline'), dict) else {}
    watches = []
    for watch in baseline.get('watchlist') or []:
        current_watch = dict(watch) if isinstance(watch, dict) else {}
        code = str(current_watch.get('code') or '')[:20]
        if not _readable_research_text(current_watch.get('name')):
            current_watch['name'] = code
        if not _readable_research_text(current_watch.get('basis')):
            current_watch['basis'] = '创建时关联路径'
        watches.append(current_watch)
    baseline['watchlist'] = watches
    sectors = [str(value)[:80] for value in (baseline.get('sectors') or [])
               if _readable_research_text(value)]
    baseline['sectors'] = sectors
    source_rows = []
    for source in baseline.get('sources') or []:
        current_source = dict(source) if isinstance(source, dict) else {}
        if not _readable_research_text(current_source.get('name')):
            current_source['name'] = '历史来源（名称编码不可读）'
        source_rows.append(current_source)
    baseline['sources'] = source_rows
    watch_codes = [str(value.get('code') or '') for value in watches if value.get('code')]
    title_fallback = (' / '.join(watch_codes[:3]) + ' 相关事件') if watch_codes else '已保存事件'
    title = (str(baseline.get('title') or '')[:300]
             if _readable_research_text(baseline.get('title')) else title_fallback)
    baseline['title'] = title
    quality = dict(baseline.get('quality')) if isinstance(baseline.get('quality'), dict) else {}
    if not _readable_research_text(quality.get('meaning')):
        quality['meaning'] = '历史质量说明编码不可读；请按来源与字段重新核对'
    baseline['quality'] = quality
    row['baseline'] = baseline
    horizon = int(row.get('horizonTradingDays') or 5)
    if not _readable_research_text(row.get('statement')):
        row['statement'] = '观察“%s”在预设 %d 个工作日内是否持续获得独立证据，并核对行业、自选和大盘对照反馈。' % (title, horizon)
    fallback_checks = [
        '事件是否被独立来源确认，且未被撤回或修正',
        '敏感行业是否出现相对大盘可区分的结构反馈',
        '自选反馈是否与预先记录的行业路径一致',
    ]
    checks = []
    for index, check in enumerate(row.get('observationChecklist') or []):
        current_check = dict(check) if isinstance(check, dict) else {}
        if not _readable_research_text(current_check.get('label')):
            current_check['label'] = fallback_checks[min(index, len(fallback_checks) - 1)]
        checks.append(current_check)
    row['observationChecklist'] = checks or [
        {'id': 'source', 'label': fallback_checks[0]},
        {'id': 'sector', 'label': fallback_checks[1]},
        {'id': 'watchlist', 'label': fallback_checks[2]},
    ]
    fallback_falsifiers = [
        '原始事件被撤回、修正或没有得到独立来源确认',
        '观察窗口内相关行业没有出现可区别于大盘的结构反馈',
        '反馈更合理地由同期大盘变化或新的无关事件解释',
    ]
    falsifiers = list(row.get('falsifiers') or [])
    row['falsifiers'] = [
        value if _readable_research_text(value) else fallback_falsifiers[min(index, 2)]
        for index, value in enumerate(falsifiers[:12])
    ] or fallback_falsifiers
    return row


def research_workflow_environment():
    """Describe source readiness without probing or granting new access."""
    tdx = tdx_status(probe=False)
    akshare = akshare_status(probe=False)
    event = load_event_service_config()
    return {
        'official_disclosures': {
            'status': 'available', 'available': True,
            'detail': '创建后执行时按需访问巨潮资讯。',
        },
        'market_quote': {
            'status': 'available', 'available': True,
            'detail': '执行时使用通达信优先、东方财富和腾讯备援的行情链。',
        },
        'tdx_local': {
            'status': ('ready' if tdx.get('service_ready') else str(tdx.get('status') or 'unavailable')),
            'available': tdx.get('service_ready') is True,
            'detail': '只读本地接口；不会访问账户、持仓或下单能力。',
        },
        'akshare_macro': {
            'status': str(akshare.get('status') or 'not_installed'),
            'available': akshare.get('installed') is True,
            'detail': ('已安装 AKShare ' + str(akshare.get('version') or '')
                       if akshare.get('installed') else '本机未安装 AKShare。'),
        },
        'event_news': {
            'status': 'authorized' if event.get('enabled') else 'authorization_required',
            'available': event.get('enabled') is True,
            'detail': ('事件服务已获得持续访问授权。' if event.get('enabled')
                       else '需先在总览单独开启事件影响雷达。'),
        },
    }


def research_workflows_status(current=None):
    profile = current if isinstance(current, dict) else load_profile()
    data = profile.get('data') if isinstance(profile.get('data'), dict) else {}
    if workflow_snapshot is None:
        return {
            'modelVersion': RESEARCH_WORKFLOW_MODEL_VERSION,
            'items': [], 'summary': {'total': 0},
            'error': '研究流程模型不可用',
        }
    result = workflow_snapshot(data.get('research_workflows') or [], now_bj())
    result['environment'] = research_workflow_environment()
    result['permissions'] = {
        'previewRequired': True,
        'explicitConfirmationRequired': True,
        'automaticExternalAuthorization': False,
        'automaticTradingAction': False,
    }
    return result


def research_suggestions_status(current=None):
    profile = current if isinstance(current, dict) else load_profile()
    data = profile.get('data') if isinstance(profile.get('data'), dict) else {}
    if build_research_suggestion_snapshot is None:
        return {
            'modelVersion': RESEARCH_SUGGESTION_MODEL_VERSION,
            'items': [], 'visible': [], 'summary': {'total': 0, 'pending': 0},
            'error': '主动研究建议模型不可用',
        }
    hypotheses = (hypothesis_snapshot(data.get('research_hypotheses') or [], now_bj()).get('items')
                  if hypothesis_snapshot is not None else [])
    return build_research_suggestion_snapshot(
        data, hypotheses, data.get('research_suggestions') or [], now_bj())


def _record_research_suggestion_progress(current, suggestion_id, action, draft=None):
    """Persist an explicit handoff step without accepting or executing it."""
    snapshot = research_suggestions_status(current)
    rows = [dict(row) for row in (snapshot.get('items') or []) if isinstance(row, dict)]
    index = next((i for i, row in enumerate(rows)
                  if str(row.get('id') or '') == suggestion_id), -1)
    if index < 0:
        raise ValueError('这条研究建议已经变化，请刷新后重试')
    proposed = rows[index].get('proposedDraft') or {}
    if draft is not None and (research_suggestion_draft_fingerprint(proposed) !=
                              research_suggestion_draft_fingerprint(draft)):
        raise ValueError('研究草稿已编辑；后续将作为独立流程，不再更新原建议阶段')
    rows[index] = mutate_research_suggestion_item(rows[index], action, now_bj())
    current['data']['research_suggestions'] = rows[-PROFILE_LIST_LIMITS['research_suggestions']:]
    return rows[index]


def mutate_research_suggestion(action, payload=None):
    body = payload if isinstance(payload, dict) else {}
    clean_action = str(action or '').strip()
    live = research_suggestions_status()
    suggestion_id = str(body.get('suggestionId') or '').strip()[:180]
    if clean_action == 'refresh':
        with _profile_lock:
            current = _read_profile_unlocked()
            refreshed = research_suggestions_status(current)
            current['data']['research_suggestions'] = refreshed.get('items') or []
            saved = _write_profile_unlocked(current)
        return {'suggestions': research_suggestions_status(saved)}
    item = next((row for row in (live.get('items') or [])
                 if str(row.get('id') or '') == suggestion_id), None)
    if not item:
        raise ValueError('这条研究建议已经变化，请刷新后重试')
    if clean_action == 'prepare':
        if item.get('state') not in {'pending', 'dismissed'}:
            raise ValueError('这条研究建议当前不能载入')
        with _profile_lock:
            current = _read_profile_unlocked()
            updated = _record_research_suggestion_progress(
                current, suggestion_id, 'prepare')
            saved = _write_profile_unlocked(current)
        return {'suggestion': updated, 'draft': updated.get('proposedDraft') or {},
                'suggestions': research_suggestions_status(saved), 'previewRequired': True,
                'automaticPreview': False, 'automaticExternalAccess': False}
    if clean_action not in {'dismiss', 'restore'}:
        raise ValueError('不支持的研究建议操作')
    updated = mutate_research_suggestion_item(item, clean_action, now_bj())
    with _profile_lock:
        current = _read_profile_unlocked()
        snapshot = research_suggestions_status(current)
        rows = [dict(row) for row in (snapshot.get('items') or []) if isinstance(row, dict)]
        index = next((i for i, row in enumerate(rows)
                      if str(row.get('id') or '') == suggestion_id), -1)
        if index < 0:
            raise ValueError('这条研究建议已经变化，请刷新后重试')
        rows[index] = updated
        current['data']['research_suggestions'] = rows[-PROFILE_LIST_LIMITS['research_suggestions']:]
        saved = _write_profile_unlocked(current)
    return {'updated': updated, 'suggestions': research_suggestions_status(saved)}


def preview_research_workflow(draft):
    if preview_workflow is None:
        raise ValueError('研究流程模型不可用')
    return preview_workflow(draft, research_workflow_environment(), now_bj())


def _workflow_quote_result(source_id, quote, fetched_at):
    price = quote.get('price')
    previous = quote.get('prev_close')
    pct = quote.get('pct')
    if pct is None:
        try:
            pct = round((float(price) / float(previous) - 1) * 100, 2) if float(previous) else None
        except (TypeError, ValueError, ZeroDivisionError):
            pct = None
    upstream = str(quote.get('source_name') or quote.get('source') or '')[:120]
    return {
        'sourceId': source_id, 'status': 'ok', 'fetchedAt': fetched_at,
        'upstream': upstream,
        'summary': '%s %s，现价 %s，涨跌幅 %s%%' % (
            quote.get('code') or '', quote.get('name') or '',
            '--' if price is None else price, '--' if pct is None else pct),
        'evidence': [{
            'code': quote.get('code'), 'name': quote.get('name'), 'price': price,
            'prevClose': previous, 'pct': pct, 'high': quote.get('high'),
            'low': quote.get('low'), 'amount': quote.get('amount'),
            'source': quote.get('source'), 'sourceName': quote.get('source_name'),
        }],
    }


def collect_research_workflow_sources(item):
    """Run only the sources frozen in a user-confirmed active workflow."""
    target = item.get('target') if isinstance(item.get('target'), dict) else {}
    code = normalize_code(str(target.get('code') or ''))
    results = []
    for source_id in item.get('sources') or []:
        fetched_at = now_bj().isoformat(timespec='seconds')
        try:
            if source_id == 'official_disclosures':
                data = cninfo_disclosures(code, 8)
                rows = data.get('items') or []
                results.append({
                    'sourceId': source_id, 'status': 'ok', 'fetchedAt': fetched_at,
                    'upstream': '巨潮资讯',
                    'summary': '读取 %d 条最近公告；官方索引共 %s 条。' % (
                        len(rows), data.get('total') if data.get('total') is not None else '--'),
                    'evidence': [{
                        'id': row.get('id'), 'title': row.get('title'), 'date': row.get('date'),
                        'publishedAt': row.get('published_at'), 'url': row.get('pdf_url'),
                        'focus': row.get('focus') is True,
                    } for row in rows[:8]],
                })
            elif source_id == 'market_quote':
                results.append(_workflow_quote_result(source_id, quote_with_fallback(code), fetched_at))
            elif source_id == 'tdx_local':
                results.append(_workflow_quote_result(source_id, tdx_read_quote(code), fetched_at))
            elif source_id == 'akshare_macro':
                snapshot = akshare_research_snapshot(refresh=True)
                summary = snapshot.get('summary') or {}
                results.append({
                    'sourceId': source_id,
                    'status': 'ok' if snapshot.get('status') == 'ok' else 'degraded',
                    'fetchedAt': fetched_at, 'upstream': 'AKShare（逐项保留最终上游）',
                    'summary': '研究指标 %s 项，当前 %s，陈旧 %s，不可用 %s。' % (
                        summary.get('metrics', 0), summary.get('current', 0),
                        summary.get('stale', 0), summary.get('unavailable', 0)),
                    'evidence': snapshot.get('modules') or [],
                    'error': '; '.join(str(row.get('error') or '')
                                      for row in (snapshot.get('errors') or [])[:5]),
                })
            elif source_id == 'event_news':
                config = load_event_service_config()
                if not config.get('enabled'):
                    raise ValueError('事件影响雷达尚未单独授权')
                snapshot = event_service_status(include_impact=True)
                impact = snapshot.get('impact') if isinstance(snapshot.get('impact'), dict) else {}
                events = impact.get('items') if isinstance(impact.get('items'), list) else []
                results.append({
                    'sourceId': source_id, 'status': 'ok', 'fetchedAt': fetched_at,
                    'upstream': '已授权事件影响服务',
                    'summary': '读取 %d 条可追踪事件路径。' % len(events),
                    'evidence': events[:10],
                })
            else:
                raise ValueError('未知研究来源')
        except Exception as exc:
            results.append({
                'sourceId': source_id, 'status': 'unavailable', 'fetchedAt': fetched_at,
                'upstream': '', 'summary': '本次读取失败，不影响其他来源。',
                'evidence': [], 'error': str(exc)[:240],
            })
    return results


def mutate_research_workflow(action, payload=None):
    body = payload if isinstance(payload, dict) else {}
    clean_action = str(action or '').strip()
    if clean_action == 'preview':
        preview = preview_research_workflow(body.get('draft'))
        suggestion_id = str(body.get('suggestionId') or '').strip()[:180]
        suggestions = None
        if suggestion_id:
            with _profile_lock:
                current = _read_profile_unlocked()
                _record_research_suggestion_progress(
                    current, suggestion_id, 'preview', body.get('draft'))
                saved = _write_profile_unlocked(current)
            suggestions = research_suggestions_status(saved)
        return {'preview': preview, 'suggestions': suggestions}
    if clean_action == 'confirm':
        live_preview = preview_research_workflow(body.get('draft'))
        if str(body.get('previewId') or '') != live_preview.get('previewId'):
            raise ValueError('研究草稿已变化，请重新预览后确认')
        created = create_workflow(live_preview, body.get('confirmations'), now_bj())
        origin_id = str(body.get('originWorkflowId') or '').strip()[:180]
        origin_kind = str(body.get('originKind') or '').strip()[:30]
        suggestion_id = str(body.get('suggestionId') or '').strip()[:180]
        with _profile_lock:
            current = _read_profile_unlocked()
            rows = [row for row in (current['data'].get('research_workflows') or [])
                    if isinstance(row, dict) and row.get('id')]
            origin = next((row for row in rows if str(row.get('id') or '') == origin_id), None)
            if origin_id and not origin:
                raise ValueError('来源研究流程已不存在，请重新载入后再创建')
            created = attach_workflow_lineage(created, origin, rows, origin_kind)
            suggestions = []
            if suggestion_id:
                suggestion_state = research_suggestions_status(current)
                suggestions = [dict(row) for row in (suggestion_state.get('items') or [])
                               if isinstance(row, dict)]
                suggestion_index = next((i for i, row in enumerate(suggestions)
                                         if str(row.get('id') or '') == suggestion_id), -1)
                if suggestion_index < 0:
                    raise ValueError('来源研究建议已经变化，请不带建议来源重新预览')
                proposed = suggestions[suggestion_index].get('proposedDraft') or {}
                if (research_suggestion_draft_fingerprint(proposed) !=
                        research_suggestion_draft_fingerprint(live_preview.get('draft') or {})):
                    raise ValueError('建议草稿已被编辑，请重新预览后作为独立流程创建')
                suggestions[suggestion_index] = mutate_research_suggestion_item(
                    suggestions[suggestion_index], 'accept', now_bj(), created.get('id'))
                current['data']['research_suggestions'] = suggestions[-PROFILE_LIST_LIMITS['research_suggestions']:]
            rows.append(created)
            current['data']['research_workflows'] = rows[-PROFILE_LIST_LIMITS['research_workflows']:]
            saved = _write_profile_unlocked(current)
        return {'created': created, 'workflows': research_workflows_status(saved),
                'suggestions': research_suggestions_status(saved)}

    workflow_id = str(body.get('workflowId') or '').strip()[:180]
    if not workflow_id:
        raise ValueError('研究流程 ID 是必需的')
    if clean_action == 'run':
        profile = load_profile()
        source = next((row for row in (profile.get('data') or {}).get('research_workflows') or []
                       if isinstance(row, dict) and str(row.get('id') or '') == workflow_id), None)
        if not source:
            raise ValueError('研究流程不存在')
        source_results = collect_research_workflow_sources(source)
        with _profile_lock:
            current = _read_profile_unlocked()
            rows = [dict(row) for row in (current['data'].get('research_workflows') or [])
                    if isinstance(row, dict) and row.get('id')]
            index = next((i for i, row in enumerate(rows)
                          if str(row.get('id') or '') == workflow_id), -1)
            if index < 0:
                raise ValueError('研究流程不存在')
            rows[index], run = record_workflow_run(rows[index], source_results, now_bj())
            current['data']['research_workflows'] = rows[-PROFILE_LIST_LIMITS['research_workflows']:]
            saved = _write_profile_unlocked(current)
        return {'run': run, 'workflows': research_workflows_status(saved)}

    with _profile_lock:
        current = _read_profile_unlocked()
        rows = [dict(row) for row in (current['data'].get('research_workflows') or [])
                if isinstance(row, dict) and row.get('id')]
        index = next((i for i, row in enumerate(rows)
                      if str(row.get('id') or '') == workflow_id), -1)
        if index < 0:
            raise ValueError('研究流程不存在')
        rows[index] = mutate_workflow(rows[index], clean_action, now_bj())
        current['data']['research_workflows'] = rows[-PROFILE_LIST_LIMITS['research_workflows']:]
        saved = _write_profile_unlocked(current)
    return {'updated': rows[index], 'workflows': research_workflows_status(saved)}


def research_hypotheses_status(current=None):
    """Return the hypothesis lifecycle with time-derived review state."""
    profile = current if isinstance(current, dict) else load_profile()
    data = profile.get('data') or {}
    if hypothesis_snapshot is None:
        return {
            'modelVersion': HYPOTHESIS_MODEL_VERSION, 'items': [],
            'summary': {'total': 0, 'observing': 0, 'review_due': 0,
                        'completed': 0, 'archived': 0},
            'error': '研究假设模型不可用',
        }
    result = hypothesis_snapshot(data.get('research_hypotheses') or [], now_bj())
    items = [_repair_hypothesis_display(row) for row in (result.get('items') or [])]
    result['items'] = items
    result['summary']['candidateEvidence'] = sum(
        len(row.get('evidenceCandidates') or []) for row in items)
    evidence_authorized = normalize_event_service_config(data.get('event_service'))['enabled']
    with _event_service_lock:
        result['evidenceService'] = {
            'modelVersion': HYPOTHESIS_EVIDENCE_MODEL_VERSION,
            'automaticCollectionAuthorized': evidence_authorized,
            'intervalSeconds': HYPOTHESIS_EVIDENCE_INTERVAL_SECONDS,
            'lastCheckedAt': _event_service_runtime.get('last_evidence_check_at'),
            'lastError': _event_service_runtime.get('last_evidence_error'),
            'automaticConclusion': False,
        }
    return result


def research_memory_status(current=None):
    """Return memories derived only from user-confirmed hypothesis reviews."""
    profile = current if isinstance(current, dict) else load_profile()
    data = profile.get('data') if isinstance(profile.get('data'), dict) else profile
    data = data if isinstance(data, dict) else {}
    if build_research_memory_snapshot is None:
        return {
            'modelVersion': RESEARCH_MEMORY_MODEL_VERSION,
            'items': [], 'relatedByHypothesis': {},
            'summary': {'total': 0, 'visible': 0, 'hidden': 0,
                        'withLesson': 0, 'withDataGaps': 0},
            'error': '研究记忆模型不可用',
        }
    return build_research_memory_snapshot(
        data.get('research_hypotheses') or [],
        data.get('research_memory_preferences'))


def mutate_research_memory(action, payload=None):
    """Update only memory presentation preferences; source reviews stay immutable."""
    if build_research_memory_snapshot is None or normalize_research_memory_preferences is None:
        raise ValueError('研究记忆模型不可用')
    clean_action = str(action or '').strip()
    body = payload if isinstance(payload, dict) else {}
    with _profile_lock:
        current = _read_profile_unlocked()
        data = current['data']
        prefs = normalize_research_memory_preferences(data.get('research_memory_preferences'))
        live = build_research_memory_snapshot(data.get('research_hypotheses') or [], prefs)
        valid_ids = {str(row.get('id') or '') for row in (live.get('items') or [])}
        if clean_action == 'set_enabled':
            prefs['enabled'] = body.get('enabled') is True
        else:
            memory_id = str(body.get('memoryId') or '').strip()[:180]
            if memory_id not in valid_ids:
                raise ValueError('研究记忆不存在')
            hidden = list(prefs.get('hiddenMemoryIds') or [])
            notes = dict(prefs.get('notes') or {})
            if clean_action == 'hide':
                if memory_id not in hidden:
                    hidden.append(memory_id)
            elif clean_action == 'restore':
                hidden = [value for value in hidden if value != memory_id]
            elif clean_action == 'update_lesson':
                lesson = str(body.get('lesson') or '').strip()[:1000]
                if lesson:
                    notes[memory_id] = lesson
                else:
                    notes.pop(memory_id, None)
            elif clean_action == 'reset_lesson':
                notes.pop(memory_id, None)
            else:
                raise ValueError('不支持的研究记忆操作')
            prefs['hiddenMemoryIds'] = hidden[-300:]
            prefs['notes'] = dict(list(notes.items())[-200:])
        data['research_memory_preferences'] = prefs
        saved = _write_profile_unlocked(current)
    return {'profileRevision': saved.get('revision'),
            'memory': research_memory_status(saved)}


def normalize_research_cockpit_preferences(source=None):
    """Keep only explicit, bounded user controls for the research queue."""
    value = source if isinstance(source, dict) else {}
    overrides = value.get('overrides') if isinstance(value.get('overrides'), dict) else {}
    clean = {}
    for item_id, row in list(overrides.items())[-100:]:
        if not isinstance(row, dict):
            continue
        clean_id = str(item_id or '').strip()[:180]
        if not clean_id:
            continue
        try:
            adjustment = max(-30, min(30, int(row.get('adjustment') or 0)))
        except Exception:
            adjustment = 0
        clean[clean_id] = {
            'adjustment': adjustment,
            'pinned': row.get('pinned') is True,
            'snoozedUntil': str(row.get('snoozedUntil') or '')[:40] or None,
            'updatedAt': str(row.get('updatedAt') or '')[:40] or None,
        }
    return {'schema': 1, 'overrides': clean}


def _cockpit_snoozed(value, current):
    if not value:
        return False
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=BJC)
        return parsed.astimezone(BJC) > current
    except Exception:
        return False


def _cockpit_readable(value, fallback, limit=180):
    text = str(value or '').strip()[:limit]
    text = text.replace('敏感行业：；', '敏感行业待确认；')
    suspicious = text.count('?') >= max(3, int(len(text) * 0.12)) or '�' in text
    return str(fallback or '')[:limit] if not text or suspicious else text


def research_cockpit_status(profile=None, current=None, diagnostics=None):
    """Compose an explainable daily research queue from explicit local state."""
    now = (current or now_bj()).astimezone(BJC)
    source = profile if isinstance(profile, dict) else load_profile()
    data = source.get('data') if isinstance(source.get('data'), dict) else source
    data = data if isinstance(data, dict) else {}
    preferences = normalize_research_cockpit_preferences(
        data.get('research_cockpit_preferences'))
    overrides = preferences['overrides']
    hypothesis_state = (hypothesis_snapshot(data.get('research_hypotheses') or [], now)
                        if hypothesis_snapshot is not None else {
                            'items': [], 'summary': {'observing': 0, 'review_due': 0,
                                                     'completed': 0, 'archived': 0}})
    hypotheses = hypothesis_state.get('items') or []
    suggestion_state = (build_research_suggestion_snapshot(
        data, hypotheses, data.get('research_suggestions') or [], now)
        if build_research_suggestion_snapshot is not None else {
            'items': [], 'summary': {'pending': 0, 'accepted': 0}})
    suggestions = suggestion_state.get('items') or []
    pending_suggestions_by_watch = {
        str(row.get('sourceId') or ''): row for row in suggestions
        if row.get('sourceType') == 'watchlist' and row.get('state') == 'pending'
    }
    dismissed_suggestion_codes = {
        str(row.get('sourceId') or '') for row in suggestions
        if row.get('sourceType') == 'watchlist' and row.get('state') == 'dismissed'
    }
    workflow_state = (workflow_snapshot(data.get('research_workflows') or [], now)
                      if workflow_snapshot is not None else {'items': [], 'summary': {}})
    workflows = workflow_state.get('items') or []
    memory_state = (build_research_memory_snapshot(
        data.get('research_hypotheses') or [], data.get('research_memory_preferences'))
                    if build_research_memory_snapshot is not None else {
                        'summary': {'visible': 0}, 'relatedByHypothesis': {}})
    related_memories = memory_state.get('relatedByHypothesis') or {}
    open_hypotheses = [row for row in hypotheses
                       if row.get('effectiveStatus') in {'observing', 'review_due'}]
    active_watch_codes = {
        str(watch.get('code') or '')
        for row in open_hypotheses
        for watch in ((row.get('baseline') or {}).get('watchlist') or [])
        if isinstance(watch, dict) and watch.get('code')
    }
    active_workflow_codes = {
        str((row.get('target') or {}).get('code') or '')
        for row in workflows if row.get('effectiveStatus') in {'active', 'review_due', 'paused'}
        and isinstance(row.get('target'), dict) and (row.get('target') or {}).get('code')
    }
    items = []

    def append_item(item):
        item_id = str(item.get('id') or '')[:180]
        if not item_id:
            return
        control = overrides.get(item_id) or {}
        default_score = max(0, min(100, int(item.get('defaultScore') or 0)))
        adjustment = max(-30, min(30, int(control.get('adjustment') or 0)))
        score = max(0, min(100, default_score + adjustment))
        pinned = control.get('pinned') is True
        snoozed = _cockpit_snoozed(control.get('snoozedUntil'), now)
        level = 'now' if score >= 80 else ('next' if score >= 55 else 'later')
        items.append(dict(item, id=item_id, defaultScore=default_score,
                          adjustment=adjustment, score=score, level=level,
                          pinned=pinned, snoozed=snoozed,
                          snoozedUntil=control.get('snoozedUntil'),
                          userAdjusted=bool(adjustment or pinned or snoozed),
                          priorityBasis='transparent-rules-plus-explicit-user-adjustment'))

    for row in open_hypotheses:
        hypothesis_id = str(row.get('id') or '')
        due = row.get('effectiveStatus') == 'review_due'
        evidence = row.get('evidenceCandidates') or []
        watches = ((row.get('baseline') or {}).get('watchlist') or [])
        watch_codes = [str(watch.get('code') or '') for watch in watches
                       if isinstance(watch, dict) and watch.get('code')]
        fallback_title = ('复盘 %s 相关事件研究假设' % ' / '.join(watch_codes[:3])
                          if watch_codes else '复盘已保存的事件研究假设')
        reasons = [{'label': '你已明确保存这条研究假设', 'points': 30,
                    'basis': 'explicit-user-record'}]
        if due:
            reasons.append({'label': '预设观察窗口已经结束', 'points': 45,
                            'basis': 'registered-review-window'})
        else:
            reasons.append({'label': '仍在预设观察窗口内', 'points': 15,
                            'basis': 'registered-review-window'})
        if evidence:
            reasons.append({'label': '已有 %d 条候选证据待核对' % len(evidence),
                            'points': min(15, len(evidence) * 3),
                            'basis': 'timestamped-candidate-evidence'})
        if watches:
            reasons.append({'label': '关联 %d 只自选' % len(watches), 'points': 8,
                            'basis': 'explicit-watchlist-link'})
        score = 15 + sum(int(reason['points']) for reason in reasons)
        append_item({
            'id': 'hypothesis:' + hypothesis_id, 'sourceType': 'hypothesis',
            'sourceId': hypothesis_id,
            'title': _cockpit_readable(row.get('statement'), fallback_title),
            'subtitle': _cockpit_readable((row.get('baseline') or {}).get('title'),
                                          '创建于 %s' % str(row.get('createdAt') or '')[:10], 120),
            'defaultScore': score, 'reasons': reasons,
            'evidence': {'available': len(evidence),
                         'status': '待复盘' if due else ('已开始收集' if evidence else '尚未收集'),
                         'missing': list((row.get('evidenceState') or {}).get('errors') or [])[:3]},
            'nextAction': {'type': 'review' if due else 'collect_evidence',
                           'label': '填写复盘结论' if due else ('核对候选证据' if evidence else '收集候选证据'),
                           'page': 'strategy'},
            'origin': '用户明确保存的研究假设',
            'memoryHints': list(related_memories.get(hypothesis_id) or [])[:3],
        })

    for row in workflows:
        status = row.get('effectiveStatus')
        if status not in {'active', 'review_due', 'paused'}:
            continue
        runs = list(row.get('runs') or [])
        latest = row.get('latestRun') if isinstance(row.get('latestRun'), dict) else (
            runs[-1] if runs else {})
        card = latest.get('resultCard') if isinstance(latest, dict) else {}
        card = card if isinstance(card, dict) else {}
        card_summary = card.get('summary') if isinstance(card.get('summary'), dict) else {}
        due = status == 'review_due'
        never_run = not runs
        paused = status == 'paused'
        if due:
            score, label, action_type = 86, '填写研究复盘', 'review_workflow'
            evidence_status = '观察窗口已到期'
        elif never_run:
            score, label, action_type = 66, '检查后手动运行', 'open_workflow'
            evidence_status = '已创建，尚未读取来源'
        elif paused:
            score, label, action_type = 48, '查看已暂停流程', 'open_workflow'
            evidence_status = '已暂停'
        else:
            score, label, action_type = 58, '查看最新研究结果', 'open_workflow'
            evidence_status = '已运行，等待后续复盘'
        reasons = [{'label': '这是你明确创建的研究流程', 'points': 35,
                    'basis': 'explicit-user-created-workflow'}]
        if due:
            reasons.append({'label': '预设复盘窗口已经结束', 'points': 36,
                            'basis': 'registered-review-window'})
        elif never_run:
            reasons.append({'label': '尚未手动读取已确认来源', 'points': 16,
                            'basis': 'explicit-workflow-state'})
        elif runs:
            reasons.append({'label': '已有 %d 次手动运行记录' % len(runs), 'points': 8,
                            'basis': 'explicit-workflow-run'})
        gaps = [str(item.get('message') or '')[:120] for item in (card.get('gaps') or [])
                if isinstance(item, dict) and item.get('message')][:3]
        append_item({
            'id': 'workflow:' + str(row.get('id') or ''), 'sourceType': 'workflow',
            'sourceId': str(row.get('id') or ''),
            'title': str(row.get('title') or '研究流程')[:180],
            'subtitle': str(row.get('question') or '等待你继续研究')[:180],
            'defaultScore': score, 'reasons': reasons,
            'evidence': {
                'available': int(card_summary.get('evidenceItems') or 0),
                'status': evidence_status, 'missing': gaps,
            },
            'nextAction': {'type': action_type, 'label': label, 'page': 'strategy',
                           'workflowId': str(row.get('id') or '')},
            'origin': '你明确创建的研究流程',
            'handoff': {'stage': ('review_due' if due else 'created' if never_run else
                                  'paused' if paused else 'ran'),
                        'runCount': len(runs), 'workflowId': str(row.get('id') or '')},
        })

    now_ms = int(now.timestamp() * 1000)
    pending_raw_attention = [
        row for row in (data.get('attention_inbox') or [])
        if isinstance(row, dict) and not row.get('doneAt')
        and row.get('kind') in {'price', 'event'}
        and not (int(row.get('expiresAt') or 0) > 0 and now_ms >= int(row.get('expiresAt') or 0))
    ]
    triage = (build_attention_triage(pending_raw_attention, now_ms)
              if build_attention_triage is not None else {'groups': []})
    pending_attention = triage.get('groups') or []
    for row in pending_attention[:3]:
        high = row.get('priority') == 'high'
        grouped = row.get('type') == 'cluster'
        append_item({
            'id': 'attention:' + str(row.get('id') or ''), 'sourceType': 'attention',
            'sourceId': str(row.get('id') or ''),
            'title': _cockpit_readable(row.get('title'), '一条历史提醒需要核对'),
            'subtitle': _cockpit_readable(row.get('detail') or row.get('reason'), '提醒依据待核对'),
            'defaultScore': 72 if high else 58,
            'reasons': [{'label': '你尚未明确完成这个%s%s' % (
                             '高优先级' if high else '', '聚合主题' if grouped else '提醒'),
                         'points': 52 if high else 38, 'basis': 'explicit-pending-reminder'}],
            'evidence': {'available': int(row.get('count') or 1),
                         'status': '原始事件可展开核对' if grouped else '提醒事实待核对', 'missing': []},
            'nextAction': {'type': 'inspect', 'label': '查看提醒依据',
                           'page': str(row.get('page') or 'overview')[:30]},
            'origin': '你开启的提醒或事件服务',
            'attentionMembers': list(row.get('memberIds') or [])[:24],
        })

    effect = routine_effectiveness_status(data)
    for row in effect.get('recommendations') or []:
        append_item({
            'id': 'service-effect:' + str(row.get('id') or ''), 'sourceType': 'service_effect',
            'sourceId': str(row.get('id') or ''), 'title': str(row.get('title') or '检查主动服务节奏')[:180],
            'subtitle': str(row.get('reason') or '')[:180], 'defaultScore': 60,
            'reasons': [{'label': '达到预设的明确反馈样本门槛', 'points': 40,
                         'basis': 'explicit-feedback-only'}],
            'evidence': {'available': 1, 'status': '等待你确认', 'missing': []},
            'nextAction': {'type': 'review_service', 'label': '检查节奏建议', 'page': 'overview'},
            'origin': '你的明确服务反馈',
        })

    report = diagnostics if isinstance(diagnostics, dict) else build_product_diagnostics(record=False)
    health_rows = [row for row in (report.get('components') or [])
                   if isinstance(row, dict) and row.get('state') in {'warn', 'error'}
                   and not row.get('optional')]
    for row in health_rows[:3]:
        is_error = row.get('state') == 'error'
        append_item({
            'id': 'health:' + str(row.get('id') or ''), 'sourceType': 'data_health',
            'sourceId': str(row.get('id') or ''),
            'title': '%s需要检查' % str(row.get('label') or '数据链路'),
            'subtitle': str(row.get('summary') or '')[:180],
            'defaultScore': 82 if is_error else 56,
            'reasons': [{'label': '研究输入存在%s' % ('阻断问题' if is_error else '质量提醒'),
                         'points': 62 if is_error else 36, 'basis': 'local-product-diagnostics'}],
            'evidence': {'available': 1, 'status': row.get('state'), 'missing': []},
            'nextAction': {'type': 'repair', 'label': str(row.get('action') or '查看数据健康')[:80],
                           'page': str(row.get('page') or 'datasrc')[:30]},
            'origin': '本机只读诊断',
        })

    watches = [row for row in (data.get('watchlist') or []) if isinstance(row, dict)]
    unmatched = [row for row in watches
                 if str(row.get('code') or '') not in active_watch_codes
                 and str(row.get('code') or '') not in active_workflow_codes
                 and str(row.get('code') or '') not in dismissed_suggestion_codes]
    unmatched.sort(key=lambda row: int(row.get('added') or 0), reverse=True)
    for row in unmatched[:5]:
        note = str(row.get('note') or '').strip()
        code = str(row.get('code') or '')
        suggestion = pending_suggestions_by_watch.get(code)
        journey = (suggestion or {}).get('journey') or {}
        if suggestion:
            append_item({
                'id': 'watch:' + code, 'sourceType': 'research_suggestion',
                'sourceId': str(suggestion.get('id') or ''),
                'title': str(suggestion.get('title') or '研究建议')[:180],
                'subtitle': str(suggestion.get('reason') or '')[:180],
                'defaultScore': 54 if journey.get('stage') in {'drafted', 'previewed'} else 46,
                'reasons': [
                    {'label': '来自你的自选列表', 'points': 25,
                     'basis': 'explicit-watchlist'},
                    {'label': '系统已按明确记录准备可编辑问题草稿', 'points': 16,
                     'basis': 'deterministic-research-suggestion'},
                ],
                'evidence': {
                    'available': 0, 'status': str(journey.get('label') or '待你决定'),
                    'missing': list(suggestion.get('evidenceGaps') or [])[:3],
                },
                'nextAction': {
                    'type': 'load_suggestion',
                    'label': ('继续研究草稿' if journey.get('stage') in {'drafted', 'previewed'}
                              else '载入研究草稿'),
                    'page': 'strategy', 'suggestionId': str(suggestion.get('id') or ''),
                },
                'origin': '你的自选与主动研究建议',
                'handoff': journey,
            })
            continue
        append_item({
            'id': 'watch:' + code, 'sourceType': 'watchlist',
            'sourceId': code,
            'title': '%s（%s）尚无研究假设' % (str(row.get('name') or row.get('code') or '自选'),
                                             str(row.get('code') or '--')),
            'subtitle': note[:180] or '这是明确关注项，但当前没有事件假设或观察窗口。',
            'defaultScore': 38 + (8 if note else 0),
            'reasons': [{'label': '来自你的自选列表', 'points': 25,
                         'basis': 'explicit-watchlist'},
                        *([{'label': '你已写下研究备注', 'points': 8,
                            'basis': 'explicit-user-note'}] if note else [])],
            'evidence': {'available': 0, 'status': '尚未建立研究路径', 'missing': ['事件假设', '观察窗口']},
            'nextAction': {'type': 'define_question', 'label': '补充研究问题', 'page': 'watch'},
            'origin': '你的自选列表',
        })

    items.sort(key=lambda row: (0 if row['pinned'] else 1, 1 if row['snoozed'] else 0,
                                -row['score'], row['title']))
    visible = [row for row in items if not row['snoozed']]
    summary = {
        'total': len(items), 'now': sum(1 for row in visible if row['level'] == 'now'),
        'next': sum(1 for row in visible if row['level'] == 'next'),
        'later': sum(1 for row in visible if row['level'] == 'later'),
        'snoozed': sum(1 for row in items if row['snoozed']),
        'userAdjusted': sum(1 for row in items if row['userAdjusted']),
    }
    hypothesis_summary = hypothesis_state.get('summary') or {}
    return {
        'schema': 1, 'generatedAt': now.isoformat(timespec='seconds'),
        'summary': summary, 'focus': visible[:5], 'items': items[:30],
        'map': {
            'watchlist': {'total': len(watches), 'withOpenHypothesis': len(active_watch_codes)},
            'hypotheses': {'observing': int(hypothesis_summary.get('observing') or 0),
                           'reviewDue': int(hypothesis_summary.get('review_due') or 0),
                           'candidateEvidence': sum(len(row.get('evidenceCandidates') or [])
                                                    for row in hypotheses)},
            'researchMemory': {'enabled': (memory_state.get('preferences') or {}).get('enabled') is not False,
                               'visible': int((memory_state.get('summary') or {}).get('visible') or 0)},
            'pendingReminders': len(pending_attention),
            'researchSuggestions': int((suggestion_state.get('summary') or {}).get('pending') or 0),
            'researchWorkflows': int((workflow_state.get('summary') or {}).get('total') or 0),
            'serviceSuggestions': len(effect.get('recommendations') or []),
            'healthAttention': len(health_rows),
        },
        'preferences': preferences,
        'method': 'transparent-rules-plus-explicit-user-adjustment',
        'boundary': ('只汇总你的自选、已保存假设、明确提醒反馈和本机数据健康；'
                     '优先级不是市场预测，不会推断未记录目标、自动改写假设或触发交易。'),
        'automaticGoalInference': False, 'automaticTradingActions': False,
    }


def mutate_research_cockpit(action, payload=None):
    """Apply reversible user controls without changing source research records."""
    body = payload if isinstance(payload, dict) else {}
    clean_action = str(action or '').strip()
    item_id = str(body.get('itemId') or '').strip()[:180]
    if not item_id:
        raise ValueError('研究任务标识不能为空')
    live = research_cockpit_status()
    if not any(row.get('id') == item_id for row in live.get('items') or []):
        raise ValueError('该研究任务已变化，请刷新后重试')
    with _profile_lock:
        current = _read_profile_unlocked()
        prefs = normalize_research_cockpit_preferences(
            current['data'].get('research_cockpit_preferences'))
        overrides = prefs['overrides']
        row = dict(overrides.get(item_id) or {
            'adjustment': 0, 'pinned': False, 'snoozedUntil': None})
        if clean_action == 'raise_priority':
            row['adjustment'] = min(30, int(row.get('adjustment') or 0) + 10)
        elif clean_action == 'lower_priority':
            row['adjustment'] = max(-30, int(row.get('adjustment') or 0) - 10)
        elif clean_action == 'toggle_pin':
            row['pinned'] = not bool(row.get('pinned'))
        elif clean_action == 'snooze':
            until = now_bj() + timedelta(days=1)
            row['snoozedUntil'] = until.replace(hour=8, minute=30, second=0,
                                                 microsecond=0).isoformat(timespec='seconds')
        elif clean_action == 'reset':
            overrides.pop(item_id, None)
            row = None
        else:
            raise ValueError('不支持的研究队列操作')
        if row is not None:
            row['updatedAt'] = now_bj().isoformat(timespec='seconds')
            overrides[item_id] = row
        prefs['overrides'] = dict(list(overrides.items())[-100:])
        current['data']['research_cockpit_preferences'] = prefs
        saved = _write_profile_unlocked(current)
    return {'profile': saved, 'cockpit': research_cockpit_status(saved)}


def refresh_hypothesis_evidence(item_id=None, current=None, quote_loader=None,
                                 benchmark_loader=None, disclosure_loader=None):
    """Collect timestamped evidence candidates without changing hypothesis outcomes."""
    if collect_candidate_evidence is None or hypothesis_snapshot is None:
        raise ValueError('研究假设证据采集模型不可用')
    current_time = current if isinstance(current, datetime) else now_bj()
    with _profile_lock:
        profile = _read_profile_unlocked()
        rows = [dict(row) for row in (profile['data'].get('research_hypotheses') or [])
                if isinstance(row, dict) and row.get('id')]
    live = hypothesis_snapshot(rows, current_time).get('items') or []
    selected = [row for row in live if row.get('effectiveStatus') in {'observing', 'review_due'}
                and (not item_id or row.get('id') == item_id)][:8]
    if item_id and not selected:
        raise ValueError('研究假设不存在或已结束')
    quote_fn = quote_loader or quote_with_fallback
    benchmark_fn = benchmark_loader or (lambda: cached('indices', 5, em_indices_any))
    disclosure_fn = disclosure_loader or (
        lambda code: cached('disclosures_' + normalize_code(code), 300,
                            lambda: cninfo_disclosures(code, 12)))
    updates, errors = {}, []
    before = sum(len(row.get('evidenceCandidates') or []) for row in selected)
    for row in selected:
        try:
            updates[row['id']] = collect_candidate_evidence(
                row, quote_fn, benchmark_fn, disclosure_fn, current_time)
        except Exception as exc:
            errors.append('%s: %s' % (row.get('id'), str(exc)[:220]))
    if updates:
        with _profile_lock:
            latest = _read_profile_unlocked()
            merged = []
            for row in (latest['data'].get('research_hypotheses') or []):
                update = updates.get(row.get('id')) if isinstance(row, dict) else None
                if update:
                    preserved = dict(row)
                    for key in ('marketBaseline', 'evidenceCandidates', 'evidenceState', 'evidenceContract'):
                        if key in update:
                            preserved[key] = update[key]
                    row = preserved
                merged.append(row)
            latest['data']['research_hypotheses'] = merged[-PROFILE_LIST_LIMITS['research_hypotheses']:]
            saved = _write_profile_unlocked(latest)
    else:
        saved = load_profile()
    snapshot = research_hypotheses_status(saved)
    after = sum(len(row.get('evidenceCandidates') or []) for row in
                (snapshot.get('items') or []) if not item_id or row.get('id') == item_id)
    return {
        'checked': len(selected), 'added': max(0, after - before), 'errors': errors,
        'hypotheses': snapshot, 'automaticConclusion': False,
    }


def mutate_research_hypothesis(action, payload=None):
    """Atomically create, review, archive or delete one research hypothesis."""
    if hypothesis_snapshot is None or create_hypothesis is None:
        raise ValueError('研究假设模型不可用')
    clean_action = str(action or '').strip()
    body = payload if isinstance(payload, dict) else {}
    if clean_action == 'refresh_evidence':
        return refresh_hypothesis_evidence(str(body.get('id') or '').strip()[:160] or None)
    with _profile_lock:
        current = _read_profile_unlocked()
        rows = [row for row in (current['data'].get('research_hypotheses') or [])
                if isinstance(row, dict) and row.get('id')]
        target = None
        created = False
        if clean_action == 'create':
            event_item = body.get('eventItem') or {}
            event_id = str((event_item.get('event') or {}).get('id') or '').strip()
            live = hypothesis_snapshot(rows, now_bj()).get('items') or []
            target = next((row for row in live
                           if str((row.get('baseline') or {}).get('eventId') or '') == event_id
                           and row.get('effectiveStatus') in {'observing', 'review_due'}), None)
            if target is None:
                target = create_hypothesis(event_item, body.get('horizonDays') or 5,
                                           body.get('note') or '', now_bj())
                rows.append(target)
                created = True
        else:
            item_id = str(body.get('id') or '').strip()[:160]
            index = next((idx for idx, row in enumerate(rows) if row.get('id') == item_id), -1)
            if index < 0:
                raise ValueError('研究假设不存在')
            if clean_action == 'review':
                target = review_hypothesis(rows[index], body.get('outcome'),
                                           body.get('note') or '', now_bj(),
                                           body.get('falsifierHits') or [],
                                           body.get('dataGaps') or [])
                rows[index] = target
            elif clean_action == 'archive':
                target = dict(rows[index])
                target['status'] = 'archived'
                target['archivedAt'] = now_bj().isoformat(timespec='seconds')
                rows[index] = target
            elif clean_action == 'delete':
                target = rows.pop(index)
            else:
                raise ValueError('unsupported hypothesis action')
        current['data']['research_hypotheses'] = rows[-PROFILE_LIST_LIMITS['research_hypotheses']:]
        saved = _write_profile_unlocked(current)
        return {
            'profileRevision': saved.get('revision'), 'item': target,
            'created': created, 'hypotheses': research_hypotheses_status(saved),
        }


def publish_due_hypothesis_reminders(now=None):
    """Publish one local reminder per due hypothesis; no external source access."""
    if hypothesis_snapshot is None:
        return 0
    current_time = now if isinstance(now, datetime) else now_bj()
    timestamp = int(current_time.timestamp() * 1000)
    with _profile_lock:
        current = _read_profile_unlocked()
        snapshot = hypothesis_snapshot(current['data'].get('research_hypotheses') or [], current_time)
        due = [row for row in snapshot.get('items') or []
               if row.get('effectiveStatus') == 'review_due']
        receipts = [row for row in (current['data'].get('hypothesis_receipts') or [])
                    if isinstance(row, dict) and row.get('id')]
        receipt_ids = {row['id'] for row in receipts}
        inbox = [row for row in (current['data'].get('attention_inbox') or [])
                 if isinstance(row, dict) and row.get('id')]
        published = 0
        for row in due:
            receipt_id = 'hypothesis-due:' + str(row.get('id') or '')[:100]
            if receipt_id in receipt_ids:
                continue
            baseline = row.get('baseline') or {}
            inbox = [item for item in inbox if item.get('id') != receipt_id]
            inbox.append({
                'id': receipt_id, 'fingerprint': receipt_id,
                'kind': 'hypothesis_review', 'priority': 'medium', 'delivery': 'digest',
                'page': 'strategy', 'createdAt': timestamp,
                'expiresAt': timestamp + 14 * 24 * 60 * 60 * 1000,
                'title': '研究假设到期：' + str(baseline.get('title') or '待复盘事件')[:120],
                'detail': '观察窗口已结束。请按预先登记的证据与反证条件复盘，不要用事后信息改写原假设。',
                'reason': '你曾主动保存这条研究假设，并选择了 %s 个工作日观察窗口' % (
                    row.get('horizonTradingDays') or '--'),
                'hypothesisId': row.get('id'), 'reviewDueAt': row.get('reviewDueAt'),
            })
            receipts.append({'id': receipt_id, 'hypothesisId': row.get('id'),
                             'publishedAt': current_time.isoformat(timespec='seconds')})
            receipt_ids.add(receipt_id)
            published += 1
        if published:
            current['data']['attention_inbox'] = inbox[-PROFILE_LIST_LIMITS['attention_inbox']:]
            current['data']['hypothesis_receipts'] = receipts[-PROFILE_LIST_LIMITS['hypothesis_receipts']:]
            _write_profile_unlocked(current)
        return published


def publish_due_research_workflow_reminders(now=None):
    """Publish one local reminder for each explicitly enabled due workflow."""
    if workflow_snapshot is None:
        return 0
    current_time = now if isinstance(now, datetime) else now_bj()
    timestamp = int(current_time.timestamp() * 1000)
    with _profile_lock:
        current = _read_profile_unlocked()
        snapshot = workflow_snapshot(current['data'].get('research_workflows') or [], current_time)
        due = [row for row in snapshot.get('items') or []
               if row.get('effectiveStatus') == 'review_due'
               and row.get('reminderEnabled') is True]
        receipts = [row for row in (current['data'].get('research_workflow_receipts') or [])
                    if isinstance(row, dict) and row.get('id')]
        receipt_ids = {row['id'] for row in receipts}
        inbox = [row for row in (current['data'].get('attention_inbox') or [])
                 if isinstance(row, dict) and row.get('id')]
        published = 0
        for row in due:
            receipt_id = 'workflow-due:' + str(row.get('id') or '')[:120]
            if receipt_id in receipt_ids:
                continue
            target = row.get('target') if isinstance(row.get('target'), dict) else {}
            target_name = target.get('name') or target.get('code') or '研究对象'
            inbox = [item for item in inbox if item.get('id') != receipt_id]
            inbox.append({
                'id': receipt_id, 'fingerprint': receipt_id,
                'kind': 'research_workflow_review', 'priority': 'medium',
                'delivery': 'digest', 'page': 'strategy', 'createdAt': timestamp,
                'expiresAt': timestamp + 14 * 24 * 60 * 60 * 1000,
                'title': '研究流程到期：' + str(row.get('title') or target_name)[:120],
                'detail': '预设复盘窗口已结束。请检查已收集证据、数据缺口和反证条件。',
                'reason': '你创建该流程时明确开启了本机到期提醒',
                'workflowId': row.get('id'), 'reviewDueAt': row.get('dueAt'),
            })
            receipts.append({
                'id': receipt_id, 'workflowId': row.get('id'),
                'publishedAt': current_time.isoformat(timespec='seconds'),
            })
            receipt_ids.add(receipt_id)
            published += 1
        if published:
            current['data']['attention_inbox'] = inbox[-PROFILE_LIST_LIMITS['attention_inbox']:]
            current['data']['research_workflow_receipts'] = receipts[
                -PROFILE_LIST_LIMITS['research_workflow_receipts']:]
            _write_profile_unlocked(current)
        return published


def _event_service_loop():
    with _event_service_lock:
        _event_service_runtime['thread_running'] = True
    while not _event_service_stop.is_set():
        try:
            publish_due_hypothesis_reminders(now_bj())
        except Exception as exc:
            log('hypothesis due reminder -> %s' % exc)
        try:
            publish_due_research_workflow_reminders(now_bj())
        except Exception as exc:
            log('research workflow due reminder -> %s' % exc)
        cfg = load_event_service_config()
        if cfg['enabled']:
            with _event_service_lock:
                last_evidence_at = _event_service_runtime.get('last_evidence_check_at')
            evidence_due = True
            if last_evidence_at:
                try:
                    last_evidence = datetime.fromisoformat(str(last_evidence_at).replace('Z', '+00:00'))
                    evidence_due = (now_bj() - last_evidence.astimezone(BJC)).total_seconds() >= HYPOTHESIS_EVIDENCE_INTERVAL_SECONDS
                except ValueError:
                    evidence_due = True
            if evidence_due:
                checked_at = now_bj()
                try:
                    evidence_result = refresh_hypothesis_evidence(current=checked_at)
                    with _event_service_lock:
                        _event_service_runtime.update(
                            last_evidence_check_at=checked_at.isoformat(timespec='seconds'),
                            last_evidence_error='; '.join(evidence_result.get('errors') or []) or None,
                            evidence_checks=int(_event_service_runtime.get('evidence_checks') or 0) + 1,
                            evidence_candidates=int(_event_service_runtime.get('evidence_candidates') or 0)
                            + int(evidence_result.get('added') or 0),
                        )
                except Exception as exc:
                    with _event_service_lock:
                        _event_service_runtime.update(
                            last_evidence_check_at=checked_at.isoformat(timespec='seconds'),
                            last_evidence_error=str(exc)[:240],
                            evidence_checks=int(_event_service_runtime.get('evidence_checks') or 0) + 1,
                        )
        wait_seconds = 30 if not cfg['enabled'] else cfg['interval_seconds']
        checked_at = now_bj()
        if not cfg['enabled']:
            with _event_service_lock:
                _event_service_runtime.update(state='disabled', next_check_at=None, last_error=None)
        else:
            try:
                result = process_event_service_once(checked_at)
                snapshot = result.get('snapshot') or {}
                with _event_service_lock:
                    _event_service_runtime.update(
                        state=result['state'], last_check_at=checked_at.isoformat(timespec='seconds'),
                        last_success_at=(checked_at.isoformat(timespec='seconds')
                                         if snapshot.get('sources') else _event_service_runtime.get('last_success_at')),
                        next_check_at=(checked_at + timedelta(seconds=wait_seconds)).isoformat(timespec='seconds'),
                        last_error='; '.join(snapshot.get('errors') or []) or None,
                        checks=int(_event_service_runtime.get('checks') or 0) + 1,
                        published_count=int(_event_service_runtime.get('published_count') or 0)
                        + int(result.get('published') or 0),
                        latest_summary=(snapshot.get('impact') or {}).get('summary'),
                    )
            except Exception as exc:
                with _event_service_lock:
                    _event_service_runtime.update(
                        state='error', last_check_at=checked_at.isoformat(timespec='seconds'),
                        next_check_at=None, last_error=str(exc)[:240])
        _event_service_wake.wait(wait_seconds)
        _event_service_wake.clear()
    with _event_service_lock:
        _event_service_runtime.update(thread_running=False, state='stopped', next_check_at=None)


def start_event_service():
    global _event_service_thread
    if _event_service_thread and _event_service_thread.is_alive():
        return
    _event_service_stop.clear()
    _event_service_thread = threading.Thread(target=_event_service_loop,
                                             name='deeppulse-event-impact', daemon=True)
    _event_service_thread.start()


def stop_event_service():
    _event_service_stop.set()
    _event_service_wake.set()
    thread = _event_service_thread
    if thread and thread.is_alive():
        thread.join(timeout=3)


# ---------------------------------------------------------------- 墨水屏设备网关（ESP32 只读终端）

EPAPER_WIDTH = 800
EPAPER_HEIGHT = 480
EPAPER_FRAME_BYTES = EPAPER_WIDTH * EPAPER_HEIGHT // 8
DEVICE_DEFAULT_PORT = 8988
DEVICE_ALERT_TTL_SECONDS = 15 * 60
DEVICE_MODES = ('focus', 'overview', 'emotion', 'watch', 'hotspot', 'event', 'research', 'alert')
DEVICE_REFRESH_POLICIES = ('stable', 'smart', 'fast')
_device_config_lock = threading.Lock()
_device_gateway_lock = threading.Lock()
_device_gateway_server = None
_device_gateway_thread = None
_device_runtime = {
    'running': False, 'started_at': None, 'last_seen': None, 'last_ip': None,
    'last_user_agent': None, 'requests': 0, 'last_frame_sha256': None,
    'last_error': None,
}


def _new_device_token():
    return secrets.token_urlsafe(24)


def _device_defaults():
    return {
        'schema': 1,
        'enabled': False,
        'port': DEVICE_DEFAULT_PORT,
        'device_name': 'DeepPulse E-Paper',
        'model': 'waveshare-7in5-v2',
        'mode': 'focus',
        'focus_code': '000001',
        'focus_name': '平安银行',
        'poll_seconds': 30,
        'display_seconds': 180,
        'partial_before_full': 6,
        'refresh_policy': 'smart',
        'token': _new_device_token(),
        'revision': 0,
        'updated_at': None,
    }


def normalize_device_config(value, current=None):
    """Normalize the local-only device configuration and preserve its pairing token."""
    base = dict(_device_defaults())
    if isinstance(current, dict):
        base.update(current)
    if not isinstance(value, dict):
        value = {}
    clean = dict(base)
    if 'enabled' in value:
        clean['enabled'] = bool(value.get('enabled'))
    clean['port'] = DEVICE_DEFAULT_PORT
    if 'device_name' in value:
        name = str(value.get('device_name') or '').strip()[:40]
        clean['device_name'] = name or 'DeepPulse E-Paper'
    if 'mode' in value:
        mode = str(value.get('mode') or '').strip().lower()
        clean['mode'] = mode if mode in DEVICE_MODES else 'focus'
    if 'focus_code' in value:
        code = normalize_code(str(value.get('focus_code') or ''))
        if len(code) != 6:
            raise ValueError('device focus_code must be a 6-digit security code')
        clean['focus_code'] = code
    if 'focus_name' in value:
        clean['focus_name'] = str(value.get('focus_name') or '').strip()[:30]
    if 'refresh_policy' in value:
        policy = str(value.get('refresh_policy') or '').strip().lower()
        clean['refresh_policy'] = policy if policy in DEVICE_REFRESH_POLICIES else 'smart'
    for key, low, high in (
            ('poll_seconds', 15, 300),
            ('display_seconds', 60, 1800),
            ('partial_before_full', 2, 20)):
        if key in value:
            try:
                clean[key] = max(low, min(high, int(value.get(key))))
            except Exception:
                raise ValueError('device %s must be an integer' % key)
    token = str(base.get('token') or '')
    clean['token'] = token if len(token) >= 24 else _new_device_token()
    clean['schema'] = 1
    clean['revision'] = int(base.get('revision') or 0)
    clean['updated_at'] = base.get('updated_at')
    return clean


def load_device_config(persist=False):
    with _device_config_lock:
        try:
            with open(DEVICE_CONFIG_FILE, 'r', encoding='utf-8') as f:
                raw = json.load(f)
        except Exception:
            raw = {}
        # The persisted file is trusted to supply the current pairing token;
        # API patches never get that privilege and therefore cannot set it.
        clean = normalize_device_config(raw, raw)
        if persist and clean != raw:
            temp_file = DEVICE_CONFIG_FILE + '.tmp'
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(clean, f, ensure_ascii=False, indent=1)
            os.replace(temp_file, DEVICE_CONFIG_FILE)
        return clean


def save_device_config(patch, rotate_token=False):
    if not isinstance(patch, dict):
        raise ValueError('device config patch must be an object')
    with _device_config_lock:
        try:
            with open(DEVICE_CONFIG_FILE, 'r', encoding='utf-8') as f:
                raw = json.load(f)
                current = normalize_device_config(raw, raw)
        except Exception:
            current = _device_defaults()
        clean = normalize_device_config(patch, current)
        if rotate_token:
            clean['token'] = _new_device_token()
        clean['revision'] = int(current.get('revision') or 0) + 1
        clean['updated_at'] = now_bj().isoformat(timespec='seconds')
        temp_file = DEVICE_CONFIG_FILE + '.tmp'
        with open(temp_file, 'w', encoding='utf-8') as f:
            json.dump(clean, f, ensure_ascii=False, indent=1)
        os.replace(temp_file, DEVICE_CONFIG_FILE)
    sync_device_gateway(clean)
    return clean


def _local_ipv4_addresses():
    values = []
    try:
        for row in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = row[4][0]
            if ip and not ip.startswith('127.') and ip not in values:
                values.append(ip)
    except Exception:
        pass
    return values


def device_token_matches(candidate, config=None):
    expected = str((config or load_device_config()).get('token') or '')
    actual = str(candidate or '')
    return bool(expected and actual and hmac.compare_digest(expected, actual))


def device_gateway_status(config=None, include_token=True):
    cfg = dict(config or load_device_config())
    with _device_gateway_lock:
        runtime = dict(_device_runtime)
    result = {
        'enabled': bool(cfg.get('enabled')),
        'running': bool(runtime.get('running')),
        'port': cfg.get('port'),
        'addresses': _local_ipv4_addresses(),
        'endpoint_path': '/device/v1/frame.bin',
        'state_path': '/device/v1/state',
        'last_seen': runtime.get('last_seen'),
        'last_ip': runtime.get('last_ip'),
        'last_user_agent': runtime.get('last_user_agent'),
        'requests': runtime.get('requests') or 0,
        'last_frame_sha256': runtime.get('last_frame_sha256'),
        'last_error': runtime.get('last_error'),
    }
    if include_token:
        result['token'] = cfg.get('token')
    return result


def _market_session_label(now=None):
    current = now or now_bj()
    hm = current.hour * 100 + current.minute
    if current.weekday() >= 5:
        return 'CLOSED'
    if 930 <= hm <= 1130 or 1300 <= hm <= 1500:
        return 'OPEN'
    if 1130 < hm < 1300:
        return 'BREAK'
    if hm < 930:
        return 'PRE'
    return 'CLOSED'


def build_device_state(config=None, demo='', delivery_item=None):
    """Build a compact, read-only hardware snapshot from DeepPulse's normalized data."""
    cfg = dict(config or load_device_config())
    code = cfg.get('focus_code') or '000001'
    name = cfg.get('focus_name') or code
    try:
        emotion = assemble_emotion()
    except Exception as exc:
        emotion = {'date': None, 'engine': {'degraded': True, 'flags': [], 'missing': [str(exc)[:80]]}}
    engine = emotion.get('engine') or {}
    try:
        indices = cached('indices', 5, em_indices_any)
    except Exception:
        indices = []
    try:
        quote = cached('quote_' + code, 4, lambda: quote_with_fallback(code))
        name = quote.get('name') or name
    except Exception:
        quote = {'code': code, 'name': name, 'price': None, 'pct': None}
    try:
        kline = cached('device_kline_' + code, 60,
                       lambda: kline_with_fallback(code, 101, 1, 80))
    except Exception:
        kline = {'rows': []}
    profile = load_profile().get('data') or {}
    preview_mode = demo if demo in DEVICE_MODES else ''
    now_ms = int(time.time() * 1000)
    triggered = [row for row in (profile.get('alerts') or [])
                 if row.get('triggered') and row.get('triggered_at')
                 and 0 <= now_ms - int(row.get('triggered_at') or 0)
                 <= DEVICE_ALERT_TTL_SECONDS * 1000]
    triggered.sort(key=lambda row: row.get('triggered_at') or row.get('ts') or 0, reverse=True)
    alert = triggered[0] if triggered else None
    if preview_mode == 'alert':
        alert = {
            'demo': True, 'code': code, 'name': name,
            'dir': 'up', 'price': quote.get('price') or 12.80,
            'triggered_at': int(time.time() * 1000),
        }
    if isinstance(delivery_item, dict) and delivery_item.get('id'):
        detail = str(delivery_item.get('detail') or '')
        code_match = re.search(r'(?<!\d)(\d{6})(?!\d)', detail)
        alert = {
            'attention': True,
            'kind': str(delivery_item.get('kind') or 'system')[:16],
            'code': code_match.group(1) if code_match else code,
            'name': name,
            'dir': 'up',
            'price': quote.get('price'),
            'triggered_at': int(delivery_item.get('createdAt') or time.time() * 1000),
        }
    flags = [str(row.get('text') or '') for row in (engine.get('flags') or []) if row.get('text')]
    tdx = emotion.get('tdx_local') or {}
    watch = []
    if (preview_mode or cfg.get('mode')) == 'watch':
        candidates = list(profile.get('watchlist') or [])
        if not candidates:
            candidates = [{'code': code, 'name': name}]
        seen = set()
        for item in candidates:
            item_code = normalize_code(str(item.get('code') or ''))
            if len(item_code) != 6 or item_code in seen:
                continue
            seen.add(item_code)
            item_name = str(item.get('name') or item_code)
            try:
                item_quote = cached('quote_' + item_code, 4,
                                    lambda c=item_code: quote_with_fallback(c))
            except Exception:
                item_quote = {}
            watch.append({
                'code': item_code,
                'name': item_quote.get('name') or item_name,
                'price': item_quote.get('price'),
                'pct': item_quote.get('pct'),
            })
            if len(watch) >= 6:
                break

    hotspots, headlines = [], []
    if (preview_mode or cfg.get('mode')) == 'hotspot':
        try:
            hotspots = [
                {'code': row.get('code'), 'name': row.get('name'),
                 'price': row.get('price'), 'pct': row.get('pct')}
                for row in cached('sectors', 25, em_sectors)[:6]
                if row.get('code')
            ]
        except Exception:
            hotspots = []
        try:
            headlines = [
                {'time': row.get('time'), 'title': row.get('title'),
                 'source_name': row.get('source_name')}
                for row in cached('news', 90, em_news)[:3]
            ]
        except Exception:
            headlines = []

    event_radar = {'enabled': False, 'state': 'disabled', 'summary': {}, 'item': None}
    if (preview_mode or cfg.get('mode')) == 'event':
        try:
            snapshot = collect_event_impact(profile_data=profile)
            items = ((snapshot.get('impact') or {}).get('items') or [])
            event_radar = {
                'enabled': bool(snapshot.get('enabled')),
                'state': snapshot.get('state') or 'unavailable',
                'summary': (snapshot.get('impact') or {}).get('summary') or {},
                'item': items[0] if items else None,
                'errors': (snapshot.get('errors') or [])[:2],
            }
        except Exception as exc:
            event_radar = {'enabled': False, 'state': 'error', 'summary': {},
                           'item': None, 'errors': [str(exc)[:120]]}

    research_workflow = {'state': 'empty', 'summary': {}, 'boundary': 'RESEARCH ONLY'}
    if (preview_mode or cfg.get('mode')) == 'research':
        try:
            workflows = workflow_snapshot(profile.get('research_workflows') or [], now_bj())
            selected = next((row for row in (workflows.get('items') or [])
                             if isinstance((row.get('latestRun') or {}).get('resultCard'), dict)), None)
            if selected:
                latest = selected.get('latestRun') or {}
                card = latest.get('resultCard') or {}
                target = card.get('target') or {}
                research_workflow = {
                    'state': 'ready',
                    'workflow_id': str(selected.get('id') or '')[:180],
                    'run_id': str(latest.get('id') or '')[:180],
                    'title': str(card.get('title') or selected.get('title') or '')[:120],
                    'target': {
                        'code': str(target.get('code') or '')[:20],
                        'name': str(target.get('name') or '')[:80],
                    },
                    'ran_at': str(latest.get('ranAt') or '')[:50],
                    'effective_status': str(selected.get('effectiveStatus') or '')[:30],
                    'summary': dict(card.get('summary') or {}),
                    'review_state': str(card.get('reviewState') or 'waiting_for_user')[:30],
                    'boundary': 'NO AUTO CONCLUSION',
                }
        except Exception as exc:
            research_workflow = {
                'state': 'error', 'summary': {}, 'boundary': 'RESEARCH ONLY',
                'error': str(exc)[:120],
            }

    raw = engine.get('raw') or {}
    state = {
        'schema': 1,
        'generated_at': now_bj().isoformat(timespec='seconds'),
        'sequence': int(time.time()),
        'market_session': _market_session_label(),
        'data_date': emotion.get('date'),
        'device': {
            'name': cfg.get('device_name'), 'model': cfg.get('model'),
            'width': EPAPER_WIDTH, 'height': EPAPER_HEIGHT, 'bpp': 1,
            'mode': 'alert' if delivery_item or preview_mode == 'alert' or (
                alert and not preview_mode and cfg.get('mode') == 'alert')
            else preview_mode or cfg.get('mode'),
            'poll_seconds': cfg.get('poll_seconds'),
            'display_seconds': cfg.get('display_seconds'),
            'partial_before_full': cfg.get('partial_before_full'),
            'refresh_policy': cfg.get('refresh_policy'),
        },
        'emotion': {
            'temperature': engine.get('temp'), 'phase': engine.get('phase'),
            'coverage': engine.get('coverage'), 'confidence': engine.get('confidence'),
            'degraded': bool(engine.get('degraded')), 'risk': flags[0] if flags else '',
            'dimensions': [
                {'key': row.get('key'), 'name': row.get('name'),
                 'value': row.get('value'), 'coverage': row.get('coverage')}
                for row in (engine.get('dimensions') or [])[:6]
            ],
        },
        'focus': {
            'code': code, 'name': name, 'price': quote.get('price'), 'pct': quote.get('pct'),
            'open': quote.get('open'), 'high': quote.get('high'), 'low': quote.get('low'),
            'kline': (kline.get('rows') or [])[-60:],
        },
        'indices': [
            {'code': row.get('code'), 'name': row.get('name'),
             'price': row.get('price'), 'pct': row.get('pct')}
            for row in indices[:5] if row.get('code')
        ],
        'market': {
            key: raw.get(key) for key in (
                'zt', 'dt', 'zb', 'zb_rate', 'height', 'lb_count',
                'up', 'down', 'flat', 'turnover_yi', 'vol_ratio', 'flow_yi')
        },
        'watch': watch,
        'hotspots': hotspots,
        'headlines': headlines,
        'event_radar': event_radar,
        'research_workflow': research_workflow,
        'alert': alert,
        'quality': {
            'tdx_status': tdx.get('status') or 'unavailable',
            'tdx_read_only': True,
            'missing': (engine.get('missing') or [])[:8],
            'stale': not bool(emotion.get('date')),
        },
        'disclaimer': 'RESEARCH ONLY',
    }
    return state


_FONT_5X7 = {
    ' ': (0, 0, 0, 0, 0, 0, 0), '!': (4, 4, 4, 4, 4, 0, 4),
    '-': (0, 0, 0, 31, 0, 0, 0), '.': (0, 0, 0, 0, 0, 6, 6),
    ':': (0, 6, 6, 0, 6, 6, 0), '/': (1, 2, 4, 8, 16, 0, 0),
    '+': (0, 4, 4, 31, 4, 4, 0), '%': (17, 2, 4, 8, 17, 0, 0),
    '0': (14, 17, 19, 21, 25, 17, 14), '1': (4, 12, 4, 4, 4, 4, 14),
    '2': (14, 17, 1, 2, 4, 8, 31), '3': (30, 1, 1, 14, 1, 1, 30),
    '4': (2, 6, 10, 18, 31, 2, 2), '5': (31, 16, 16, 30, 1, 1, 30),
    '6': (14, 16, 16, 30, 17, 17, 14), '7': (31, 1, 2, 4, 8, 8, 8),
    '8': (14, 17, 17, 14, 17, 17, 14), '9': (14, 17, 17, 15, 1, 1, 14),
    'A': (14, 17, 17, 31, 17, 17, 17), 'B': (30, 17, 17, 30, 17, 17, 30),
    'C': (14, 17, 16, 16, 16, 17, 14), 'D': (30, 17, 17, 17, 17, 17, 30),
    'E': (31, 16, 16, 30, 16, 16, 31), 'F': (31, 16, 16, 30, 16, 16, 16),
    'G': (14, 17, 16, 23, 17, 17, 15), 'H': (17, 17, 17, 31, 17, 17, 17),
    'I': (14, 4, 4, 4, 4, 4, 14), 'J': (7, 2, 2, 2, 2, 18, 12),
    'K': (17, 18, 20, 24, 20, 18, 17), 'L': (16, 16, 16, 16, 16, 16, 31),
    'M': (17, 27, 21, 21, 17, 17, 17), 'N': (17, 25, 21, 19, 17, 17, 17),
    'O': (14, 17, 17, 17, 17, 17, 14), 'P': (30, 17, 17, 30, 16, 16, 16),
    'Q': (14, 17, 17, 17, 21, 18, 13), 'R': (30, 17, 17, 30, 20, 18, 17),
    'S': (15, 16, 16, 14, 1, 1, 30), 'T': (31, 4, 4, 4, 4, 4, 4),
    'U': (17, 17, 17, 17, 17, 17, 14), 'V': (17, 17, 17, 17, 17, 10, 4),
    'W': (17, 17, 17, 21, 21, 21, 10), 'X': (17, 17, 10, 4, 10, 17, 17),
    'Y': (17, 17, 10, 4, 4, 4, 4), 'Z': (31, 1, 2, 4, 8, 16, 31),
}


def _epd_pixel(frame, x, y, black=True):
    x, y = int(x), int(y)
    if x < 0 or y < 0 or x >= EPAPER_WIDTH or y >= EPAPER_HEIGHT:
        return
    index = y * (EPAPER_WIDTH // 8) + x // 8
    mask = 0x80 >> (x % 8)
    if black:
        frame[index] &= ~mask
    else:
        frame[index] |= mask


def _epd_line(frame, x0, y0, x1, y1, black=True):
    x0, y0, x1, y1 = map(int, (x0, y0, x1, y1))
    dx, sx = abs(x1 - x0), 1 if x0 < x1 else -1
    dy, sy = -abs(y1 - y0), 1 if y0 < y1 else -1
    err = dx + dy
    while True:
        _epd_pixel(frame, x0, y0, black)
        if x0 == x1 and y0 == y1:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy


def _epd_rect(frame, x, y, w, h, fill=False, black=True):
    if fill:
        for yy in range(int(y), int(y + h)):
            _epd_line(frame, x, yy, x + w - 1, yy, black)
        return
    _epd_line(frame, x, y, x + w - 1, y, black)
    _epd_line(frame, x, y + h - 1, x + w - 1, y + h - 1, black)
    _epd_line(frame, x, y, x, y + h - 1, black)
    _epd_line(frame, x + w - 1, y, x + w - 1, y + h - 1, black)


def _epd_bar(frame, x, y, w, h, value):
    """Draw a bounded 0-100 bar that stays legible on a 1-bit panel."""
    _epd_rect(frame, x, y, w, h, fill=False)
    try:
        ratio = max(0.0, min(100.0, float(value))) / 100.0
    except Exception:
        ratio = 0.0
    fill_w = int((w - 4) * ratio)
    if fill_w > 0:
        _epd_rect(frame, x + 2, y + 2, fill_w, max(1, h - 4), fill=True)


def _epd_text(frame, x, y, text, scale=2, invert=False):
    cursor = int(x)
    for char in str(text or '').upper():
        rows = _FONT_5X7.get(char, _FONT_5X7[' '])
        for yy, bits in enumerate(rows):
            for xx in range(5):
                on = bool(bits & (1 << (4 - xx)))
                if on:
                    _epd_rect(frame, cursor + xx * scale, y + yy * scale,
                              scale, scale, fill=True, black=not invert)
        cursor += 6 * scale
    return cursor


def _epd_float(value, digits=2, signed=False):
    try:
        number = float(value)
    except Exception:
        return '--'
    prefix = '+' if signed and number > 0 else ''
    return prefix + ('%%.%df' % digits) % number


def _phase_ascii(value):
    text = str(value or '')
    for key, label in (
            ('冰', 'ICE'), ('修复', 'RECOVER'), ('发酵', 'BREW'),
            ('高潮', 'HIGH'), ('亢奋', 'CLIMAX')):
        if key in text:
            return label
    return re.sub(r'[^A-Za-z0-9-]', '', text).upper()[:10] or '--'


def render_epaper_frame(state):
    """Render the exact 800x480 1-bit frame consumed by the ESP32 firmware."""
    frame = bytearray([0xFF]) * EPAPER_FRAME_BYTES
    focus = state.get('focus') or {}
    emotion = state.get('emotion') or {}
    alert = state.get('alert')
    mode = (state.get('device') or {}).get('mode') or 'focus'
    generated = str(state.get('generated_at') or '')

    _epd_rect(frame, 0, 0, EPAPER_WIDTH, 48, fill=True, black=True)
    _epd_text(frame, 16, 11, 'DEEPPULSE', 3, invert=True)
    _epd_text(frame, 592, 14, generated[11:16] or '--:--', 2, invert=True)
    _epd_text(frame, 680, 14, state.get('market_session') or '--', 2, invert=True)

    if mode == 'alert' and alert:
        _epd_rect(frame, 18, 70, 764, 380, fill=False)
        _epd_text(frame, 48, 92, 'ALERT' + (' DEMO' if alert.get('demo') else ''), 6)
        _epd_text(frame, 50, 180, alert.get('code') or focus.get('code') or '--', 5)
        if alert.get('attention'):
            _epd_text(frame, 50, 250, str(alert.get('kind') or 'SYSTEM').upper()[:16], 4)
            _epd_text(frame, 50, 340, 'OPEN DEEPPULSE FOR DETAILS', 3)
            _epd_text(frame, 50, 400, 'RESEARCH ONLY', 2)
            return bytes(frame)
        direction = 'UP >=' if alert.get('dir') == 'up' else 'DOWN <='
        _epd_text(frame, 50, 250, direction, 4)
        _epd_text(frame, 390, 242, _epd_float(alert.get('price'), 2), 6)
        _epd_text(frame, 50, 340, 'RULE TRIGGERED - VERIFY DATA', 3)
        _epd_text(frame, 50, 400, 'RESEARCH ONLY', 2)
        return bytes(frame)

    if mode == 'emotion':
        market = state.get('market') or {}
        quality = state.get('quality') or {}
        _epd_rect(frame, 18, 70, 250, 306, fill=False)
        _epd_text(frame, 38, 90, 'CYCLE TEMP', 3)
        _epd_text(frame, 62, 137,
                  str(emotion.get('temperature')
                      if emotion.get('temperature') is not None else '--'), 9)
        _epd_text(frame, 38, 225, 'PHASE ' + _phase_ascii(emotion.get('phase')), 2)
        _epd_text(frame, 38, 264,
                  'CONF ' + _epd_float(emotion.get('confidence'), 0) + '%', 2)
        _epd_text(frame, 38, 303,
                  'COVER ' + _epd_float(emotion.get('coverage'), 0) + '%', 2)
        _epd_text(frame, 38, 342,
                  'DATA ' + ('STALE' if quality.get('stale') else 'OK'), 2)

        _epd_rect(frame, 285, 70, 497, 306, fill=False)
        _epd_text(frame, 305, 88, '6D STRUCTURE', 3)
        labels = {
            'earning': 'EARN', 'loss_control': 'DEFENSE',
            'continuity': 'CONT', 'breadth': 'BREADTH',
            'liquidity': 'LIQ', 'quality': 'QUALITY',
        }
        for index, row in enumerate((emotion.get('dimensions') or [])[:6]):
            y = 132 + index * 38
            value = row.get('value')
            _epd_text(frame, 305, y, labels.get(row.get('key'), row.get('key') or '--'), 2)
            _epd_text(frame, 420, y, str(value if value is not None else '--'), 2)
            _epd_bar(frame, 474, y + 1, 280, 15, value)

        _epd_rect(frame, 18, 394, 764, 66, fill=False)
        metrics = (
            ('ZT', market.get('zt')), ('DT', market.get('dt')),
            ('BREAK', (float(market.get('zb_rate')) * 100
                       if market.get('zb_rate') is not None else None)),
            ('HIGH', market.get('height')), ('UP', market.get('up')),
            ('DOWN', market.get('down')),
        )
        for index, (label, value) in enumerate(metrics):
            x = 34 + index * 124
            suffix = '%' if label == 'BREAK' and value is not None else ''
            _epd_text(frame, x, 410, label, 1)
            _epd_text(frame, x, 430, _epd_float(value, 0) + suffix, 2)
        return bytes(frame)

    if mode == 'watch':
        rows = state.get('watch') or []
        _epd_text(frame, 18, 68, 'WATCHLIST PULSE', 3)
        _epd_rect(frame, 18, 106, 548, 316, fill=False)
        _epd_text(frame, 34, 121, 'CODE', 2)
        _epd_text(frame, 235, 121, 'PRICE', 2)
        _epd_text(frame, 414, 121, 'CHG', 2)
        _epd_line(frame, 18, 151, 565, 151)
        for index, row in enumerate(rows[:6]):
            y = 168 + index * 41
            _epd_text(frame, 34, y, row.get('code') or '------', 2)
            _epd_text(frame, 235, y, _epd_float(row.get('price'), 2), 2)
            _epd_text(frame, 414, y, _epd_float(row.get('pct'), 2, True) + '%', 2)
            if index < 5:
                _epd_line(frame, 28, y + 28, 555, y + 28)
        if not rows:
            _epd_text(frame, 115, 260, 'WATCHLIST EMPTY', 3)

        quality = state.get('quality') or {}
        _epd_rect(frame, 584, 70, 198, 352, fill=False)
        _epd_text(frame, 602, 88, 'TEMP', 3)
        _epd_text(frame, 602, 130,
                  str(emotion.get('temperature')
                      if emotion.get('temperature') is not None else '--'), 7)
        _epd_text(frame, 602, 202, 'PHASE', 2)
        _epd_text(frame, 602, 232, _phase_ascii(emotion.get('phase')), 2)
        _epd_text(frame, 602, 278,
                  'CONF ' + _epd_float(emotion.get('confidence'), 0) + '%', 2)
        _epd_text(frame, 602, 320,
                  'DATA ' + ('STALE' if quality.get('stale') else 'OK'), 2)
        _epd_text(frame, 602, 374, 'RESEARCH', 2)
        _epd_text(frame, 602, 397, 'ONLY', 2)
        return bytes(frame)

    if mode == 'hotspot':
        market = state.get('market') or {}
        rows = state.get('hotspots') or []
        _epd_text(frame, 18, 68, 'HOT RADAR', 3)
        _epd_rect(frame, 18, 106, 520, 316, fill=False)
        for index, row in enumerate(rows[:6]):
            y = 126 + index * 45
            pct = row.get('pct')
            _epd_text(frame, 34, y, str(index + 1), 2)
            _epd_text(frame, 70, y, row.get('code') or '------', 2)
            _epd_text(frame, 230, y, _epd_float(pct, 2, True) + '%', 2)
            strength = max(0.0, min(100.0, 50.0 + (float(pct) * 7
                                                   if pct is not None else 0.0)))
            _epd_bar(frame, 365, y + 1, 150, 15, strength)
        if not rows:
            _epd_text(frame, 110, 260, 'HOTSPOT DATA WAIT', 3)

        _epd_rect(frame, 556, 70, 226, 352, fill=False)
        _epd_text(frame, 574, 88, 'MARKET', 3)
        values = (
            ('ZT', market.get('zt')), ('DT', market.get('dt')),
            ('BREAK', (float(market.get('zb_rate')) * 100
                       if market.get('zb_rate') is not None else None)),
            ('HIGH', market.get('height')), ('FLOW', market.get('flow_yi')),
        )
        for index, (label, value) in enumerate(values):
            y = 135 + index * 48
            suffix = '%' if label == 'BREAK' and value is not None else ''
            _epd_text(frame, 574, y, label, 2)
            _epd_text(frame, 670, y, _epd_float(value, 0) + suffix, 2)
        _epd_text(frame, 574, 390, 'RESEARCH ONLY', 2)
        return bytes(frame)

    if mode == 'event':
        radar = state.get('event_radar') or {}
        item = radar.get('item') or {}
        event = item.get('event') or {}
        quality = item.get('quality') or {}
        watches = item.get('watchlist') or []
        sectors = item.get('sectors') or []
        _epd_text(frame, 18, 68, 'EVENT IMPACT RADAR', 3)
        _epd_rect(frame, 18, 106, 520, 316, fill=False)
        if not radar.get('enabled'):
            _epd_text(frame, 72, 188, 'EVENT SERVICE OFF', 4)
            _epd_text(frame, 72, 260, 'ENABLE IN DESKTOP', 3)
        elif not item:
            _epd_text(frame, 92, 210, 'WAITING FOR EVENTS', 3)
            _epd_text(frame, 92, 265, 'NO VERIFIED PATH', 2)
        else:
            _epd_text(frame, 36, 128, 'TYPE ' + str(event.get('type') or '--').upper()[:12], 2)
            _epd_text(frame, 36, 166, 'TIME ' + str(event.get('scheduledAt') or '--')[11:16], 2)
            _epd_text(frame, 36, 204, 'IMPORTANCE ' + str(event.get('importance') or '--'), 2)
            _epd_text(frame, 36, 242, 'QUALITY ' + str(quality.get('score') or '--'), 2)
            _epd_text(frame, 36, 280, 'SECTORS ' + str(len(sectors)), 2)
            _epd_text(frame, 36, 318, 'WATCH MATCH ' + str(len(watches)), 2)
            codes = ' '.join(str(row.get('code') or '') for row in watches[:3]) or 'NONE'
            _epd_text(frame, 36, 365, codes[:26], 2)
        _epd_rect(frame, 556, 70, 226, 352, fill=False)
        summary = radar.get('summary') or {}
        _epd_text(frame, 574, 88, 'TODAY', 3)
        for index, (label, value) in enumerate((
                ('EVENTS', summary.get('events')), ('LINKED', summary.get('linkedEvents')),
                ('MATCH', summary.get('watchMatches')), ('HIGH', summary.get('highImportance')))):
            y = 142 + index * 54
            _epd_text(frame, 574, y, label, 2)
            _epd_text(frame, 698, y, str(value if value is not None else '--'), 2)
        _epd_text(frame, 574, 376, 'NOT CAUSAL', 2)
        _epd_text(frame, 574, 402, 'RESEARCH ONLY', 2)
        return bytes(frame)

    if mode == 'research':
        workflow = state.get('research_workflow') or {}
        summary = workflow.get('summary') or {}
        target = workflow.get('target') or {}
        _epd_text(frame, 18, 68, 'RESEARCH RESULT', 3)
        _epd_rect(frame, 18, 106, 520, 316, fill=False)
        if workflow.get('state') != 'ready':
            _epd_text(frame, 72, 190, 'WAITING FOR FIRST RUN', 3)
            _epd_text(frame, 72, 252, 'CREATE IN STRATEGY', 3)
            _epd_text(frame, 72, 312, 'THEN RUN MANUALLY', 2)
        else:
            _epd_text(frame, 36, 128,
                      'TARGET ' + (target.get('code') or 'CUSTOM'), 3)
            _epd_text(frame, 36, 174,
                      'STATUS ' + str(workflow.get('effective_status') or '--').upper()[:14], 2)
            _epd_text(frame, 36, 214,
                      'SOURCES ' + str(summary.get('usableSources', 0)) + '/' +
                      str(summary.get('selectedSources', 0)), 2)
            _epd_text(frame, 36, 254,
                      'EVIDENCE ' + str(summary.get('evidenceItems', 0)), 2)
            _epd_text(frame, 36, 294,
                      'GAPS ' + str(summary.get('gapCount', 0)), 2)
            _epd_text(frame, 36, 334,
                      'STALE ' + str(summary.get('staleItems', 0)), 2)
            _epd_text(frame, 36, 374,
                      'SAME UPSTREAM ' + str(summary.get('sameUpstreamGroups', 0)), 2)

        _epd_rect(frame, 556, 70, 226, 352, fill=False)
        _epd_text(frame, 574, 88, 'REVIEW', 3)
        metrics = (
            ('USABLE', summary.get('usableSources')),
            ('DEGRADED', summary.get('degradedSources')),
            ('GAPS', summary.get('gapCount')),
            ('STALE', summary.get('staleItems')),
            ('SAME SRC', summary.get('sameUpstreamGroups')),
        )
        for index, (label, value) in enumerate(metrics):
            y = 138 + index * 46
            _epd_text(frame, 574, y, label, 2)
            _epd_text(frame, 718, y, str(value if value is not None else '--'), 2)
        _epd_text(frame, 574, 382, 'NO AUTO', 2)
        _epd_text(frame, 574, 405, 'CONCLUSION', 2)
        _epd_text(frame, 18, 442, 'RESEARCH ONLY - VERIFY EVIDENCE AND GAPS', 2)
        return bytes(frame)

    if mode == 'overview':
        quality = state.get('quality') or {}
        _epd_rect(frame, 18, 70, 292, 306, fill=False)
        _epd_text(frame, 42, 92, 'MARKET TEMP', 3)
        _epd_text(frame, 62, 145,
                  str(emotion.get('temperature')
                      if emotion.get('temperature') is not None else '--'), 10)
        _epd_text(frame, 42, 250, 'PHASE ' + _phase_ascii(emotion.get('phase')), 2)
        _epd_text(frame, 42, 288,
                  'CONF ' + _epd_float(emotion.get('confidence'), 0) + '%', 2)
        _epd_text(frame, 42, 326,
                  'DATA ' + ('STALE' if quality.get('stale') else 'OK'), 2)

        _epd_rect(frame, 330, 70, 452, 306, fill=False)
        _epd_text(frame, 350, 90, 'INDEX PULSE', 3)
        names = ('SH', 'SZ', 'CY', 'STAR', 'BJ')
        for index, row in enumerate((state.get('indices') or [])[:5]):
            y = 135 + index * 43
            _epd_text(frame, 352, y, names[index], 2)
            _epd_text(frame, 445, y, _epd_float(row.get('price'), 2), 2)
            _epd_text(frame, 635, y, _epd_float(row.get('pct'), 2, True) + '%', 2)

        _epd_rect(frame, 18, 394, 764, 66, fill=False)
        _epd_text(frame, 34, 408,
                  'FOCUS ' + (focus.get('code') or '------') + ' ' +
                  _epd_float(focus.get('price'), 2), 2)
        _epd_text(frame, 452, 408,
                  'CHG ' + _epd_float(focus.get('pct'), 2, True) + '%', 2)
        _epd_text(frame, 34, 435, 'RESEARCH ONLY - VERIFY DATA', 2)
        return bytes(frame)

    _epd_text(frame, 18, 64, (focus.get('code') or '------') + ' KLINE', 3)
    _epd_text(frame, 18, 93, 'PRICE ' + _epd_float(focus.get('price'), 2), 2)
    _epd_text(frame, 245, 93, 'CHG ' + _epd_float(focus.get('pct'), 2, True) + '%', 2)

    # Let the chart use the full remaining height.  The former 66px index
    # footer became an empty bordered strip whenever index data was missing.
    # Market context now lives in the always-populated right rail instead.
    chart_x, chart_y, chart_w, chart_h = 20, 126, 550, 334
    _epd_rect(frame, chart_x, chart_y, chart_w, chart_h, fill=False)
    for ratio in (0.25, 0.5, 0.75):
        yy = chart_y + int(chart_h * ratio)
        for xx in range(chart_x + 1, chart_x + chart_w - 1, 6):
            _epd_pixel(frame, xx, yy)
    rows = focus.get('kline') or []
    valid = [row for row in rows if isinstance(row, dict) and all(
        row.get(key) is not None for key in ('open', 'close', 'high', 'low'))]
    if valid:
        lows = [float(row['low']) for row in valid]
        highs = [float(row['high']) for row in valid]
        if lows and highs:
            lo, hi = min(lows), max(highs)
            span = max(hi - lo, 0.01)
            step = max(3, (chart_w - 12) // max(1, len(valid)))
            candle_w = max(1, min(5, step - 1))
            for index, row in enumerate(valid[-60:]):
                try:
                    open_v = float(row['open'])
                    close_v = float(row['close'])
                    high_v = float(row['high'])
                    low_v = float(row['low'])
                except Exception:
                    continue
                xx = chart_x + 7 + index * step
                to_y = lambda value: chart_y + chart_h - 6 - int((value - lo) / span * (chart_h - 12))
                y_high, y_low = to_y(high_v), to_y(low_v)
                y_open, y_close = to_y(open_v), to_y(close_v)
                _epd_line(frame, xx, y_high, xx, y_low)
                top, bottom = min(y_open, y_close), max(y_open, y_close)
                height = max(2, bottom - top + 1)
                _epd_rect(frame, xx - candle_w // 2, top, candle_w, height,
                          fill=close_v < open_v)
    else:
        _epd_text(frame, 145, 230, 'WAITING FOR KLINE', 3)

    panel_x = 588
    _epd_rect(frame, panel_x, 64, 194, 396, fill=False)
    _epd_text(frame, panel_x + 18, 82, 'TEMP', 3)
    _epd_text(frame, panel_x + 18, 122,
              str(emotion.get('temperature') if emotion.get('temperature') is not None else '--'), 7)
    _epd_text(frame, panel_x + 18, 186, 'PHASE ' + _phase_ascii(emotion.get('phase')), 2)
    _epd_text(frame, panel_x + 18, 216,
              'CONF ' + _epd_float(emotion.get('confidence'), 0) + '%', 2)
    quality = state.get('quality') or {}
    _epd_text(frame, panel_x + 18, 246,
              'TDX ' + str(quality.get('tdx_status') or '--')[:12], 1)
    _epd_text(frame, panel_x + 18, 263,
              'DATA ' + ('STALE' if quality.get('stale') else 'OK'), 1)
    _epd_line(frame, panel_x + 14, 282, panel_x + 179, 282)

    indices = state.get('indices') or []
    names = ('SH', 'SZ', 'CY', 'STAR', 'BJ')
    if indices:
        _epd_text(frame, panel_x + 18, 294, 'INDEX PULSE', 2)
        pulse_rows = [
            (names[index], _epd_float(row.get('pct'), 2, True) + '%')
            for index, row in enumerate(indices[:5])
        ]
    else:
        market = state.get('market') or {}
        _epd_text(frame, panel_x + 18, 294, 'MARKET PULSE', 2)
        pulse_rows = [
            ('ZT', _epd_float(market.get('zt'), 0)),
            ('DT', _epd_float(market.get('dt'), 0)),
            ('UP', _epd_float(market.get('up'), 0)),
            ('DOWN', _epd_float(market.get('down'), 0)),
            ('FLOW', _epd_float(market.get('flow_yi'), 0)),
        ]
    for index, (label, value) in enumerate(pulse_rows[:5]):
        y = 320 + index * 27
        _epd_text(frame, panel_x + 18, y, label, 1)
        _epd_text(frame, panel_x + 72, y - 2, value, 2)
    _epd_text(frame, panel_x + 18, 448, 'RESEARCH ONLY', 1)
    return bytes(frame)


def epaper_frame_to_bmp(frame):
    if len(frame) != EPAPER_FRAME_BYTES:
        raise ValueError('invalid e-paper frame length')
    pixel_offset = 14 + 40 + 8
    file_size = pixel_offset + len(frame)
    file_header = struct.pack('<2sIHHI', b'BM', file_size, 0, 0, pixel_offset)
    info_header = struct.pack('<IiiHHIIIIII', 40, EPAPER_WIDTH, -EPAPER_HEIGHT,
                              1, 1, 0, len(frame), 2835, 2835, 2, 0)
    palette = b'\x00\x00\x00\x00\xff\xff\xff\x00'
    return file_header + info_header + palette + frame


def epaper_content_sha256(frame):
    """Hash decision content while ignoring the volatile header clock/session area."""
    if len(frame) != EPAPER_FRAME_BYTES:
        raise ValueError('invalid e-paper frame length')
    normalized = bytearray(frame)
    row_bytes = EPAPER_WIDTH // 8
    volatile_x_byte = 560 // 8
    for y in range(48):
        start = y * row_bytes + volatile_x_byte
        normalized[start:(y + 1) * row_bytes] = b'\xff' * (row_bytes - volatile_x_byte)
    return hashlib.sha256(normalized).hexdigest()


def device_frame_payload(config=None, demo='', delivery_item=None):
    state = build_device_state(config, demo, delivery_item)
    frame = render_epaper_frame(state)
    digest = hashlib.sha256(frame).hexdigest()
    content_digest = epaper_content_sha256(frame)
    state['frame'] = {
        'bytes': len(frame), 'sha256': digest, 'content_sha256': content_digest,
        'content_type': 'application/vnd.deeppulse.epaper-1bpp',
        'white_bit': 1, 'bit_order': 'msb-first',
    }
    return state, frame, digest


# ---------------------------------------------------------------- 情绪历史记忆

_history_lock = threading.Lock()


def load_history():
    with _history_lock:
        try:
            with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                j = json.load(f)
            return j.get('snapshots') or []
        except Exception:
            return []


def save_snapshot(snap):
    with _history_lock:
        try:
            with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                j = json.load(f)
        except Exception:
            j = {'snapshots': []}
        snaps = j.get('snapshots') or []
        snaps = [s for s in snaps if s.get('date') != snap.get('date')]
        snaps.append(snap)
        snaps.sort(key=lambda s: s['date'])
        j['snapshots'] = snaps[-500:]
        with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(j, f, ensure_ascii=False, indent=1)


def record_if_due(engine):
    """交易日收盘后自动把当日情绪快照写入记忆（同一交易日只记一次）。
    关键指标缺失较多时不记录，避免把失真温度写入历史。"""
    d = engine.get('date')
    if not d:
        return False
    if len(engine.get('missing') or []) > 3:
        return False
    snaps = load_history()
    if any(s.get('date') == d for s in snaps):
        return False
    now = now_bj()
    # 盘中不自动记录（防止把未收盘数据写入历史）；收盘后或历史日期则记录
    intraday = now.strftime('%H:%M') < '15:05'
    if d < today_str():
        intraday = False
    if intraday:
        return False
    save_snapshot(engine)
    log('snapshot recorded: %s (temp=%s %s)' % (d, engine.get('temp'), engine.get('phase')))
    return True


# ---------------------------------------------------------------- 综合数据装配

def assemble_emotion(force_record=False):
    """聚合情绪全景：池子 + 宽度 + 指数 + 资金 + 引擎评分 + 历史"""
    pools = {}
    for kind in ('ZT', 'DT', 'ZB'):
        try:
            pools[kind] = cached('pool_' + kind, 45, lambda k=kind: em_pool(k))
        except Exception as e:
            pools[kind] = {'error': str(e), 'total': 0, 'pool': [], 'qdate': ''}
    try:
        breadth = cached('breadth', 45, em_breadth)
    except Exception as e:
        breadth = {'error': str(e), 'bins': {}, 'up': 0, 'down': 0, 'flat': 0, 'total': 0}
    try:
        indices = cached('indices', 5, em_indices_any)
    except Exception as e:
        indices = [{'error': str(e)}]
    try:
        def _flow_any(secid):
            for host in ('push2.eastmoney.com', 'push2delay.eastmoney.com'):
                if not _host_ok(host):
                    continue
                try:
                    return em_flow(secid, 30, host)
                except Exception:
                    _mark_host_down(host)
            raise UpstreamError('flow unavailable')
        flow_sh = cached('flow_sh', 90, lambda: _flow_any('1.000001'))
        flow_sz = cached('flow_sz', 90, lambda: _flow_any('0.399001'))
        flows = {'sh': flow_sh, 'sz': flow_sz}
    except Exception as e:
        flows = {'error': str(e)}
    try:
        bk = {'zt': cached('bk_zt', 45, lambda: em_quote_any('90.BK0815')),
              'lb': cached('bk_lb', 45, lambda: em_quote_any('90.BK0816'))}
    except Exception:
        bk = {}
    try:
        sh_k = cached('sh_kline60', 300, sh_index_kline)
    except Exception:
        sh_k = {'rows': []}
    try:
        tdx_verification = cached('tdx_emotion', 60, tdx_emotion_verification)
    except Exception as e:
        status = tdx_status(probe=False)
        tdx_verification = {
            'status': status.get('status') or 'unavailable',
            'source': 'tdx_local', 'source_name': '通达信 TQ-Local',
            'read_only': True, 'fields': {}, 'error': str(e)[:240],
        }

    qdate = pools['ZT'].get('qdate') or breadth.get('qdate') or ''
    try:
        if not pools['ZT'].get('error'):
            record_sector_snapshot(qdate, pools['ZT'].get('pool') or [])
    except Exception as e:
        log('sector snapshot fail: %s' % e)
    if len(qdate) == 8:
        qdate = '%s-%s-%s' % (qdate[:4], qdate[4:6], qdate[6:])
    raw = {'date': qdate, 'server_time': now_bj().strftime('%Y-%m-%d %H:%M:%S'),
           'pool_error': bool(pools['ZT'].get('error') or pools['DT'].get('error')
                              or pools['ZB'].get('error')),
           'bk_ok': bool((bk.get('zt') or {}).get('pct') is not None
                         and (bk.get('lb') or {}).get('pct') is not None),
           'flow_ok': (isinstance(flows.get('sh'), list) and len(flows.get('sh') or []) > 0
                       and isinstance(flows.get('sz'), list)),
           'kline_ok': len(sh_k.get('rows') or []) >= 20,
           'zt': pools['ZT'].get('total') or 0,
           'dt': pools['DT'].get('total') or 0,
           'zb': pools['ZB'].get('total') or 0,
           'zt_pool': pools['ZT'].get('pool') or [],
           'breadth': breadth, 'indices': indices, 'flows': flows,
           'bk': bk, 'sh_kline': sh_k.get('rows') or [],
    }
    degraded = bool(pools['ZT'].get('error') or pools['DT'].get('error')
                    or pools['ZB'].get('error') or not bk or not sh_k.get('rows')
                    or flows.get('error'))
    history = load_history()[-240:]
    engine = compute_emotion(raw, history) if compute_emotion else {
        'date': qdate, 'temp': None, 'phase': '数据不可用', 'signals': [],
        'advice': {}, 'risks': [], 'narrative': ''}
    engine['degraded'] = bool(degraded or engine.get('degraded'))
    engine['source_verification'] = {
        'tdx_local': {
            'status': tdx_verification.get('status'),
            'fields_available': len(tdx_verification.get('fields') or {}),
            'read_only': True,
        }
    }
    if force_record or (qdate and not pools['ZT'].get('error')):
        try:
            record_if_due(engine)
        except Exception as e:
            log('record fail: %s' % e)
    history = load_history()[-240:]
    return {'date': qdate, 'server_time': now_bj().strftime('%Y-%m-%d %H:%M:%S'),
            'pools': pools, 'breadth': breadth, 'indices': indices,
            'flows': flows, 'bk': bk, 'tdx_local': tdx_verification,
            'engine': engine, 'history': history,
            'updated': int(time.time())}


# ---------------------------------------------------------------- 产品诊断（白名单输出，不包含令牌、密钥、路径、IP 或用户内容）

def update_desktop_heartbeat(value=None):
    source = value if isinstance(value, dict) else {}
    clean = {
        'last_seen': now_bj().isoformat(timespec='seconds'),
        'app_version': str(source.get('appVersion') or '')[:32] or None,
        'product_version': str(source.get('productVersion') or '')[:80] or None,
        'service_ownership': (str(source.get('serviceOwnership') or '')[:16]
                              if source.get('serviceOwnership') in ('owned', 'attached') else None),
        'process_lifetime_protected': source.get('processLifetimeProtected') is True,
    }
    with _desktop_heartbeat_lock:
        _desktop_heartbeat.update(clean)
        return dict(_desktop_heartbeat)


def _heartbeat_age_seconds(value):
    try:
        parsed = datetime.fromisoformat(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=BJC)
        return max(0, int((now_bj() - parsed.astimezone(BJC)).total_seconds()))
    except Exception:
        return None


def _diagnostic_snapshot(report):
    return {
        'at': report.get('generatedAt'),
        'overall': report.get('overall'),
        'components': {
            str(row.get('id')): str(row.get('state'))
            for row in (report.get('components') or []) if row.get('id')
        },
    }


def load_diagnostics_history():
    with _diagnostics_history_lock:
        try:
            with open(DIAGNOSTICS_HISTORY_FILE, 'r', encoding='utf-8') as handle:
                rows = json.load(handle)
        except Exception:
            return []
    if not isinstance(rows, list):
        return []
    clean = []
    for row in rows[-96:]:
        if not isinstance(row, dict) or not isinstance(row.get('components'), dict):
            continue
        clean.append({
            'at': str(row.get('at') or '')[:40],
            'overall': str(row.get('overall') or '')[:24],
            'components': {
                str(key)[:48]: str(value)[:16]
                for key, value in row['components'].items()
            },
        })
    return clean


def record_diagnostics_baseline(report):
    snapshot = _diagnostic_snapshot(report)
    with _diagnostics_history_lock:
        try:
            with open(DIAGNOSTICS_HISTORY_FILE, 'r', encoding='utf-8') as handle:
                rows = json.load(handle)
        except Exception:
            rows = []
        rows = rows if isinstance(rows, list) else []
        last = rows[-1] if rows and isinstance(rows[-1], dict) else None
        same = bool(last and last.get('overall') == snapshot['overall']
                    and last.get('components') == snapshot['components'])
        age = _heartbeat_age_seconds(last.get('at')) if last else None
        if same and age is not None and age < 30 * 60:
            return False
        rows = (rows + [snapshot])[-96:]
        temp_file = DIAGNOSTICS_HISTORY_FILE + '.tmp'
        with open(temp_file, 'w', encoding='utf-8') as handle:
            json.dump(rows, handle, ensure_ascii=False, indent=1)
        os.replace(temp_file, DIAGNOSTICS_HISTORY_FILE)
        return True


def _apply_diagnostic_trends(report, history):
    previous = history[-1].get('components', {}) if history else {}
    recovered = persistent = new_issues = 0
    for row in report.get('components') or []:
        before = previous.get(row['id'])
        current = row['state']
        if before is None:
            trend = 'first_observation'
        elif current == 'ok' and before != 'ok':
            trend = 'recovered'
            recovered += 1
        elif current in ('warn', 'error') and before == current:
            trend = 'persistent'
            persistent += 1
        elif current in ('warn', 'error') and before in ('ok', 'info'):
            trend = 'new_issue'
            new_issues += 1
        else:
            trend = 'stable'
        row['trend'] = trend
        row['previousState'] = before
    report['history'] = {
        'samples': len(history),
        'lastRecordedAt': history[-1].get('at') if history else None,
        'recovered': recovered,
        'persistent': persistent,
        'newIssues': new_issues,
    }
    return report


def build_product_diagnostics(record=True):
    """Return a secret-safe product report composed only from explicit fields."""
    components = []

    def add(component_id, label, state, summary, action='', page='datasrc', optional=False,
            repair_action=None, repair_label=None):
        components.append({
            'id': component_id, 'label': label, 'state': state,
            'summary': summary, 'action': action or None, 'page': page,
            'optional': bool(optional),
            'repairAction': repair_action,
            'repairLabel': repair_label,
        })

    add('data_service', '深脉数据服务', 'ok', '本地数据服务正在运行。')

    harness_ready = port_is_listening('127.0.0.1', 3080)
    add('harness', 'DeepSeek Harness', 'ok' if harness_ready else 'warn',
        '对话工作台已连接。' if harness_ready else '未检测到对话工作台。',
        '' if harness_ready else '重新打开深脉桌面 App；仅浏览器模式仍可使用数据页。',
        'overview')

    with _desktop_heartbeat_lock:
        desktop = dict(_desktop_heartbeat)
    desktop_age = _heartbeat_age_seconds(desktop.get('last_seen'))
    desktop_ready = desktop_age is not None and desktop_age <= 120
    desktop_attached = desktop.get('service_ownership') == 'attached'
    desktop_lifetime_ok = desktop.get('process_lifetime_protected') is True
    desktop_state = 'ok' if (desktop_ready and (desktop_lifetime_ok or desktop_attached)) else ('warn' if desktop_ready else 'info')
    add('desktop_app', 'Windows 桌面 App', desktop_state,
        ('桌面窗口在线，后台服务已启用系统级生命周期保护。' if desktop_ready and desktop_lifetime_ok
         else '桌面窗口在线；当前连接外部启动的服务，App 不接管其生命周期。' if desktop_ready and desktop_attached
         else '桌面窗口在线，但后台服务生命周期保护未生效。' if desktop_ready
         else '最近未收到桌面端心跳；浏览器功能不受影响。'),
        ('' if desktop_ready and (desktop_lifetime_ok or desktop_attached)
         else '请重新启动 DeepSeekHarnessDesktop.exe；若仍未恢复，请导出诊断包。' if desktop_ready
         else '需要系统弹窗提醒时，请启动 DeepSeekHarnessDesktop.exe。'),
        'overview', optional=True)

    sources = source_catalog().get('items') or []
    source_by_id = {row.get('id'): row for row in sources}
    eastmoney = source_by_id.get('eastmoney') or {}
    tencent = source_by_id.get('tencent') or {}
    market_states = {eastmoney.get('status'), tencent.get('status')}
    eastmoney_ok = eastmoney.get('status') == 'ok' or eastmoney.get('latest_ok') is True
    tencent_ok = tencent.get('status') == 'ok' or tencent.get('latest_ok') is True
    if eastmoney_ok:
        market_state, market_summary, market_action = 'ok', '公开行情主链路最近访问正常。', ''
    elif market_states <= {'unobserved', None}:
        market_state, market_summary = 'info', '本次启动尚未访问公开行情链路。'
        market_action = '点击核对可完成一次只读行情请求。'
    elif (not eastmoney_ok and not tencent_ok
          and eastmoney.get('status') in ('degraded', 'unavailable')
          and tencent.get('status') in ('degraded', 'unavailable')):
        market_state, market_summary = 'error', '公开行情主源与备援最近均不可用。'
        market_action = '重新核对网络与公开行情链路。'
    else:
        market_state, market_summary = 'warn', '公开行情主链路降级，备援状态尚待确认。'
        market_action = '重新核对行情链路；系统会自动尝试备援。'
    add('market_sources', '公开行情链路', market_state, market_summary, market_action,
        'datasrc', optional=False, repair_action='recheck_market_sources', repair_label='重新核对')

    tdx = tdx_status(probe=False) or {}
    tdx_ready = bool(tdx.get('service_ready'))
    tdx_installed = bool(tdx.get('installed'))
    add('tdx_local', '通达信 TQ-Local', 'ok' if tdx_ready else 'info',
        ('本地只读行情增强已连接。' if tdx_ready else
         ('已安装但尚未连接本地行情服务。' if tdx_installed else '未启用可选的本地行情增强。')),
        '' if tdx_ready else '如需本地行情互证，请在数据源页点击“检测并接入”。',
        'datasrc', optional=True, repair_action='probe_tdx', repair_label='重新检测')

    akshare = akshare_status(probe=False) or {}
    ak_installed = bool(akshare.get('installed'))
    ak_ok = akshare.get('status') == 'ok'
    add('akshare', 'AKShare 补充层', 'ok' if ak_ok else 'info',
        ('公开数据补充层最近使用正常。' if ak_ok else
         ('已安装，等待需要时调用。' if ak_installed else '未安装可选的公开数据补充层。')),
        '' if (ak_ok or ak_installed) else '需要交易日历或宏观补充数据时再安装，不影响主行情。',
        'datasrc', optional=True, repair_action='probe_akshare', repair_label='核对日历')

    delivery = attention_delivery_status().get('channels') or {}
    failed = sum(int((row or {}).get('failed') or 0) for row in delivery.values())
    enabled_channels = sum(1 for row in delivery.values() if (row or {}).get('enabled'))
    add('notifications', '主动提醒', 'warn' if failed else ('ok' if enabled_channels else 'info'),
        (f'有 {failed} 条提醒投递失败。' if failed else
         ('提醒通道已开启。' if enabled_channels else '提醒通道尚未开启。')),
        ('前往提醒中心查看失败记录并重试。' if failed else
         ('按需在提醒中心开启桌面或墨水屏提醒。' if not enabled_channels else '')),
        'overview', optional=not enabled_channels)

    cfg = load_device_config()
    gateway = device_gateway_status(cfg, include_token=False)
    last_seen_age = _heartbeat_age_seconds(gateway.get('last_seen'))
    if not cfg.get('enabled'):
        device_state, device_summary = 'info', '墨水屏网关未开启。'
        device_action = '需要硬件联动时，在墨水屏页开启局域网网关。'
    elif not gateway.get('running'):
        device_state, device_summary = 'error', '墨水屏网关已启用但未运行。'
        device_action = '检查端口占用后，在墨水屏页重新保存网关设置。'
    elif last_seen_age is None:
        device_state, device_summary = 'warn', '网关运行中，但尚未收到设备请求。'
        device_action = '确认 ESP32 与电脑在同一局域网并核对配对令牌。'
    elif last_seen_age > 600:
        device_state, device_summary = 'warn', '墨水屏超过 10 分钟未联系本机。'
        device_action = '检查墨水屏供电、Wi-Fi 和电脑局域网连接。'
    else:
        device_state, device_summary, device_action = 'ok', '墨水屏最近已成功获取画面。', ''
    add('epaper', '墨水屏设备', device_state, device_summary, device_action,
        'epaper', optional=not cfg.get('enabled'),
        repair_action='restart_epaper_gateway' if cfg.get('enabled') else None,
        repair_label='重启网关' if cfg.get('enabled') else None)

    blocking = [row for row in components if row['state'] == 'error' and not row['optional']]
    warnings = [row for row in components if row['state'] == 'warn' and not row['optional']]
    overall = 'action_required' if blocking else ('attention' if warnings else 'ok')
    actions = [
        {'component': row['id'], 'label': row['label'], 'text': row['action'], 'page': row['page']}
        for row in components if row.get('action') and row['state'] in ('error', 'warn')
    ][:3]
    report = {
        'schema': 1, 'version': VERSION,
        'generatedAt': now_bj().isoformat(timespec='seconds'),
        'overall': overall,
        'summary': {
            'ok': sum(1 for row in components if row['state'] == 'ok'),
            'attention': sum(1 for row in components if row['state'] in ('warn', 'error')),
            'optional': sum(1 for row in components if row['optional'] and row['state'] != 'ok'),
        },
        'components': components, 'actions': actions,
        'privacy': '报告不包含 API 密钥、配对令牌、本机路径、IP 地址、自选股、提醒内容或聊天记录。',
    }
    history = load_diagnostics_history()
    _apply_diagnostic_trends(report, history)
    if record:
        try:
            record_diagnostics_baseline(report)
        except Exception as exc:
            log('diagnostics baseline save fail: %s' % str(exc)[:120])
    return report


DIAGNOSTIC_REPAIR_ACTIONS = {
    'recheck_market_sources', 'probe_tdx', 'probe_akshare', 'restart_epaper_gateway',
}


def repair_product_component(action):
    action = str(action or '').strip()
    if action not in DIAGNOSTIC_REPAIR_ACTIONS:
        raise ValueError('unsupported diagnostic repair action')
    ok = True
    if action == 'recheck_market_sources':
        cache_drop('indices')
        for host in ('push2.eastmoney.com', 'push2delay.eastmoney.com'):
            _clear_host_down(host)
        try:
            em_indices_any()
            message = '东方财富公开行情主链路已重新核对。'
        except Exception:
            try:
                tq_quote('000001')
                message = '公开行情主链路仍降级，腾讯备援可用。'
            except Exception:
                ok, message = False, '公开行情主源与备援仍不可用，请检查网络后稍后再试。'
    elif action == 'probe_tdx':
        result = tdx_status(probe=True, fresh=True)
        ok = bool(result.get('service_ready'))
        message = '通达信只读行情已重新连接。' if ok else '通达信仍未连接，请确认客户端和本地服务已启动。'
    elif action == 'probe_akshare':
        result = akshare_status(probe=True)
        ok = result.get('status') == 'ok'
        message = 'AKShare 交易日历核对成功。' if ok else 'AKShare 日历仍不可用，不影响主行情链路。'
    else:
        cfg = load_device_config()
        if not cfg.get('enabled'):
            ok, message = False, '墨水屏网关未开启，未执行任何操作。'
        else:
            stop_device_gateway()
            result = sync_device_gateway(cfg)
            ok = bool(result.get('running'))
            message = '墨水屏网关已重新启动。' if ok else '墨水屏网关仍未运行，请检查端口占用。'
    return {
        'action': action, 'ok': ok, 'message': message,
        'report': build_product_diagnostics(record=True),
    }


def build_diagnostics_archive(report=None):
    report = report or build_product_diagnostics()
    output = io.BytesIO()
    readme = (
        '深脉 DeepPulse 脱敏诊断包\r\n\r\n'
        '请将本压缩包发送给协助排查问题的人。\r\n'
        'diagnostics.json 只包含组件状态和修复提示，不包含密钥、令牌、IP、路径或个人研究内容。\r\n'
        '生成时间：%s\r\n版本：%s\r\n' % (report['generatedAt'], report['version'])
    )
    issue_lines = [
        '# DeepPulse 问题反馈', '',
        '- 版本：%s' % report['version'],
        '- 诊断时间：%s' % report['generatedAt'],
        '- 总体状态：%s' % report['overall'], '',
        '## 组件状态', '',
    ]
    for row in report.get('components') or []:
        issue_lines.append('- %s：%s；%s' % (row['label'], row['state'], row['summary']))
    issue_lines.extend([
        '', '## 问题描述', '', '<!-- 请描述你看到的现象和复现步骤，不要粘贴密钥或配对令牌。 -->', '',
        '本文件由深脉脱敏诊断自动生成。',
    ])
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('diagnostics.json', json.dumps(report, ensure_ascii=False, indent=2))
        archive.writestr('README-zh.txt', readme)
        archive.writestr('github-issue.md', '\n'.join(issue_lines))
    return output.getvalue()


# ---------------------------------------------------------------- HTTP 服务

class Handler(BaseHTTPRequestHandler):
    server_version = 'DeepPulse/1.30.0'
    protocol_version = 'HTTP/1.1'

    # ---- 基础
    def log_message(self, fmt, *args):
        pass  # 静默，日志走 data/server.log

    def allowed_origin(self):
        origin = self.headers.get('Origin', '')
        if re.fullmatch(r'http://(?:127\.0\.0\.1|localhost):(?:3080|897[1-9]|8980)', origin):
            return origin
        return ''

    def add_cors_headers(self):
        origin = self.allowed_origin()
        if origin:
            self.send_header('Access-Control-Allow-Origin', origin)
            self.send_header('Vary', 'Origin')
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def read_json_body(self, max_bytes=1024 * 1024):
        length = int(self.headers.get('Content-Length', 0) or 0)
        if length > max_bytes:
            raise ValueError('request body too large')
        try:
            return json.loads(self.rfile.read(length).decode('utf-8') or '{}')
        except Exception:
            return {}

    def send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        # 只允许本机 Harness 与深脉端口跨源访问，避免任意网页读取本机用户档案。
        self.add_cors_headers()
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def send_bytes(self, body, content_type, code=200, headers=None):
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        for key, value in (headers or {}).items():
            self.send_header(str(key), str(value))
        self.add_cors_headers()
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def send_static(self, path):
        full = os.path.normpath(os.path.join(WEB, path))
        if not full.startswith(os.path.normpath(WEB)) or not os.path.isfile(full):
            self.send_error(404)
            return
        ctype = {
            '.html': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8',
            '.js': 'application/javascript; charset=utf-8', '.mjs': 'application/javascript; charset=utf-8',
            '.svg': 'image/svg+xml', '.png': 'image/png', '.ico': 'image/x-icon',
            '.json': 'application/json', '.woff2': 'font/woff2',
        }.get(os.path.splitext(full)[1].lower(), 'application/octet-stream')
        try:
            with open(full, 'rb') as f:
                body = f.read()
        except Exception:
            self.send_error(500)
            return
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        ext = os.path.splitext(path)[1].lower()
        # 脚本与样式没有内容哈希，版本升级后必须重新验证，避免新后端配旧前端。
        self.send_header('Cache-Control',
                         'no-cache' if ext in ('.html', '.js', '.mjs', '.css', '.json')
                         else 'public, max-age=3600')
        if ext == '.html':
            self.send_header('Clear-Site-Data', '"cache"')
        self.add_cors_headers()
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    # ---- 路由
    def do_GET(self):
        try:
            self.route_get()
        except Exception as e:
            log('GET %s -> %s' % (self.path, e))
            self.send_json({'ok': False, 'error': str(e)}, 502)

    def route_get(self):
        u = urllib.parse.urlparse(self.path)
        path = u.path
        qs = dict(urllib.parse.parse_qsl(u.query))
        if path == '/' or path == '':
            self.send_static('index.html')
        elif path == '/api/health':
            health = {'name': '深脉 DeepPulse', 'ts': int(time.time()),
                      'version': VERSION,
                      'time': now_bj().strftime('%Y-%m-%d %H:%M:%S'),
                      'capabilities': {
                          'tdx_local': tdx_status(probe=False),
                          'tdx_read_only': True,
                          'bridge_protocol': 3,
                          'emotion_context_full': True,
                          'proactive_brief': 1,
                          'profile_brief_receipts': 1,
                          'attention_center': 1,
                          'profile_attention': 1,
                          'attention_learning': 1,
                          'attention_triage': 1,
                          'background_monitor': 1,
                          'market_routine': 1,
                          'akshare_enrichment': 1,
                          'akshare_research_snapshot': 1,
                          'akshare_research_packs': 1,
                          'akshare_interface_health': 1,
                          'source_lineage': 1,
                          'event_impact': 2,
                          'event_background_service': 1,
                          'research_hypotheses': 1,
                          'hypothesis_due_reminders': 1,
                          'hypothesis_evidence_candidates': 1,
                          'hypothesis_market_control': 1,
                          'unified_delivery': 1,
                          'desktop_system_notifications': 1,
                          'epaper_delivery_receipts': 1,
                          'notification_deep_links': 1,
                          'delivery_timeline': 1,
                          'product_diagnostics': 1,
                          'diagnostics_export': 1,
                          'desktop_heartbeat': 1,
                          'diagnostic_repairs': 1,
                          'diagnostic_history': 1,
                          'diagnostic_issue_template': 1,
                          'service_plan_preview': 1,
                          'service_plan_confirm': 1,
                          'routine_timeline': 1,
                          'routine_skip_pause': 1,
                          'routine_effectiveness': 1,
                          'routine_effect_suggestions': 1,
                          'routine_effect_undo': 1,
                          'research_cockpit': 1,
                          'research_priority_controls': 1,
                          'research_cockpit_context': 1,
                           'research_memory': 1,
                           'research_memory_controls': 1,
                           'research_memory_context': 1,
                           'research_workflows': 1,
                           'research_workflow_preview': 1,
                           'research_workflow_permissions': 1,
                           'research_result_cards': 1,
                           'research_template_parameters': 1,
                           'research_run_comparison': 1,
                           'research_workflow_lineage': 1,
                           'research_evidence_timeline': 1,
                           'research_suggestion_inbox': 1,
                           'research_suggestion_preview': 1,
                           'research_handoff': 1,
                           'research_journey': 1,
                           'epaper_gateway': 1,
                           'epaper_research_workflow': 1,
                          'epaper_frame': '800x480-1bpp',
                      }}
            # 顶层字段兼容桌面宿主，data 字段供统一 Web API 客户端使用。
            self.send_json(dict({'ok': True, 'data': health}, **health))
        elif path == '/api/sources':
            self.send_json({'ok': True, 'data': source_catalog()})
        elif path == '/api/diagnostics':
            self.send_json({'ok': True, 'data': build_product_diagnostics()})
        elif path == '/api/diagnostics/export.zip':
            report = build_product_diagnostics()
            filename = 'DeepPulse-Diagnostics-%s.zip' % now_bj().strftime('%Y%m%d-%H%M%S')
            self.send_bytes(build_diagnostics_archive(report), 'application/zip', headers={
                'Content-Disposition': 'attachment; filename="%s"' % filename,
            })
        elif path == '/api/tdx/status':
            probe = qs.get('probe', '1') != '0'
            fresh = qs.get('fresh') == '1'
            self.send_json({'ok': True, 'data': tdx_status(probe=probe, fresh=fresh)})
        elif path == '/api/akshare/status':
            self.send_json({'ok': True, 'data': akshare_status(probe=qs.get('probe', '1') != '0')})
        elif path == '/api/akshare/research-config':
            self.send_json({'ok': True, 'data': {
                'preferences': load_akshare_research_preferences(),
                'catalog': akshare_pack_catalog() if akshare_pack_catalog else [],
            }})
        elif path == '/api/akshare/research-snapshot':
            self.send_json({'ok': True, 'data': akshare_research_snapshot(
                refresh=qs.get('refresh', '0') == '1')})
        elif path == '/api/disclosures':
            code = normalize_code(qs.get('code', ''))
            if not code:
                self.send_json({'ok': False, 'error': 'code required'}, 400)
                return
            query_url = ('https://www.cninfo.com.cn/new/fulltextSearch?keyWord=' +
                         urllib.parse.quote(code))
            try:
                data = cached('disclosures_' + code, 300,
                              lambda: cninfo_disclosures(code, qs.get('n', 8)))
            except Exception as e:
                log('cninfo disclosures %s -> %s' % (code, e))
                data = {
                    'items': [], 'total': 0, 'query_url': query_url,
                    'fetched_at': now_bj().isoformat(timespec='seconds'),
                    'degraded': True,
                    'error': '巨潮资讯暂时不可用，请使用官方查验入口',
                    'source': {'id': 'cninfo', 'name': '巨潮资讯', 'tier': 'official'},
                }
            self.send_json({'ok': True, 'data': data})
        elif path == '/api/brain':
            cfg = load_config()
            key = (cfg.get('deepseek_api_key') or '').strip()
            self.send_json({'ok': True, 'data': {
                'mode': 'llm' if key else 'local',
                'model': cfg.get('deepseek_model') or 'deepseek-chat'}})
        elif path == '/api/profile':
            self.send_json({'ok': True, 'data': load_profile()})
        elif path == '/api/attention/learning':
            self.send_json({'ok': True, 'data': attention_learning_status()})
        elif path == '/api/attention/triage':
            self.send_json({'ok': True, 'data': attention_triage_status()})
        elif path == '/api/delivery/status':
            self.send_json({'ok': True, 'data': attention_delivery_status()})
        elif path == '/api/monitor/status':
            self.send_json({'ok': True, 'data': background_monitor_status()})
        elif path == '/api/routine/status':
            self.send_json({'ok': True, 'data': market_routine_status()})
        elif path == '/api/routine/effectiveness':
            self.send_json({'ok': True, 'data': routine_effectiveness_status()})
        elif path == '/api/event-impact':
            self.send_json({'ok': True, 'data': event_service_status(include_impact=True)})
        elif path == '/api/event-service/status':
            self.send_json({'ok': True, 'data': event_service_status(include_impact=False)})
        elif path == '/api/research-hypotheses':
            self.send_json({'ok': True, 'data': research_hypotheses_status()})
        elif path == '/api/research-cockpit':
            self.send_json({'ok': True, 'data': research_cockpit_status()})
        elif path == '/api/research-memory':
            self.send_json({'ok': True, 'data': research_memory_status()})
        elif path == '/api/research-workflows':
            self.send_json({'ok': True, 'data': research_workflows_status()})
        elif path == '/api/research-suggestions':
            self.send_json({'ok': True, 'data': research_suggestions_status()})
        elif path == '/api/device/config':
            cfg = load_device_config(persist=True)
            self.send_json({'ok': True, 'data': {
                'config': cfg,
                'gateway': device_gateway_status(cfg, include_token=True),
            }})
        elif path == '/api/device/state':
            demo = qs.get('demo', '')
            self.send_json({'ok': True, 'data': build_device_state(
                load_device_config(), demo if demo in DEVICE_MODES else '')})
        elif path == '/api/device/preview.bmp':
            demo = qs.get('demo', '')
            state, frame, digest = device_frame_payload(
                load_device_config(), demo if demo in DEVICE_MODES else '')
            self.send_bytes(epaper_frame_to_bmp(frame), 'image/bmp', headers={
                'X-DeepPulse-Frame-SHA256': digest,
                'X-DeepPulse-Content-SHA256': state['frame']['content_sha256'],
                'X-DeepPulse-Sequence': state.get('sequence'),
            })
        elif path == '/api/device/frame.bin':
            demo = qs.get('demo', '')
            state, frame, digest = device_frame_payload(
                load_device_config(), demo if demo in DEVICE_MODES else '')
            self.send_bytes(frame, 'application/vnd.deeppulse.epaper-1bpp', headers={
                'X-DeepPulse-Width': EPAPER_WIDTH,
                'X-DeepPulse-Height': EPAPER_HEIGHT,
                'X-DeepPulse-Bpp': '1',
                'X-DeepPulse-Frame-SHA256': digest,
                'X-DeepPulse-Content-SHA256': state['frame']['content_sha256'],
                'X-DeepPulse-Sequence': state.get('sequence'),
            })
        elif path == '/api/indices':
            self.send_json({'ok': True, 'data': cached('indices', 5, em_indices_any)})
        elif path == '/api/emotion':
            force = qs.get('record') == '1'
            self.send_json({'ok': True, 'data': assemble_emotion(force)})
        elif path == '/api/emotion/record':
            self.send_json({'ok': True, 'data': assemble_emotion(True)})
        elif path == '/api/ladder':
            kind = qs.get('type', 'ZT')
            if kind not in ('ZT', 'DT', 'ZB'):
                kind = 'ZT'
            self.send_json({'ok': True, 'data': cached('pool_' + kind, 45,
                                                       lambda k=kind: em_pool(k))})
        elif path == '/api/premium':
            self.send_json({'ok': True, 'data': cached('premium', 60, em_premium)})
        elif path == '/api/dragon':
            self.send_json({'ok': True, 'data': cached('dragon', 60, em_dragon)})
        elif path == '/api/dragon-seats':
            code = normalize_code(qs.get('code', ''))
            if not code:
                self.send_json({'ok': False, 'error': 'code required'}, 400)
                return
            date = qs.get('date', '')
            key = 'dragon_seats_%s_%s' % (code, date)
            self.send_json({'ok': True, 'data': cached(key, 3600,
                                                       lambda: em_dragon_seats(code, date or None))})
        elif path == '/api/sector-cycle':
            self.send_json({'ok': True, 'data': cached('sector_cycle', 300, em_sector_cycle)})
        elif path == '/api/weights':
            self.send_json({'ok': True, 'data': {
                'weights': load_weights(),
                'defaults': DEFAULT_WEIGHTS,
                'order': [t[0] for t in INDICATORS],
            }})
        elif path == '/api/quote':
            code = normalize_code(qs.get('code', ''))
            if not code:
                self.send_json({'ok': False, 'error': 'code required'}, 400)
                return
            self.send_json({'ok': True,
                            'data': cached('quote_' + code, 4,
                                           lambda: quote_with_fallback(code))})
        elif path == '/api/kline':
            raw_code = (qs.get('code') or '').strip()
            code = normalize_code(raw_code)
            if not code:
                self.send_json({'ok': False, 'error': 'code required'}, 400)
                return
            klt = qs.get('klt', '101')
            fqt = qs.get('fqt', '1')
            n = min(int(qs.get('n', '320')), 800)
            key = 'kline_%s_%s_%s_%d' % (code, klt, fqt, n)

            def loader():
                if raw_code.upper().startswith('BK'):
                    # 东财板块指数（如 BK0815 昨日涨停）
                    k = em_kline_any('90.' + code, int(klt), int(fqt), n)
                    k['source'] = 'em'
                    return k
                return kline_with_fallback(code, int(klt), int(fqt), n)
            self.send_json({'ok': True, 'data': cached(key, 60, loader)})
        elif path == '/api/rank':
            sort = qs.get('sort', 'up')
            self.send_json({'ok': True, 'data': cached('rank_' + sort, 15,
                                                       lambda: em_rank(sort))})
        elif path == '/api/sectors':
            self.send_json({'ok': True, 'data': cached('sectors', 25, em_sectors)})
        elif path == '/api/sectors-flow':
            self.send_json({'ok': True, 'data': cached('sectors_flow', 25, em_sectors_flow)})
        elif path == '/api/news':
            self.send_json({'ok': True, 'data': cached('news', 90, em_news)})
        elif path == '/api/search':
            q = (qs.get('q') or '').strip()
            if not q:
                self.send_json({'ok': True, 'data': []})
                return
            stocks = cached('all_stocks', 2 * 3600, em_all_stocks)
            ql = q.lower()
            hits = [s for s in stocks if ql in s['name'].lower() or ql in s['code']][:15]
            self.send_json({'ok': True, 'data': hits})
        elif path.startswith('/api/'):
            self.send_json({'ok': False, 'error': 'unknown api'}, 404)
        else:
            p = path.lstrip('/') or 'index.html'
            self.send_static(p)

    def do_OPTIONS(self):
        self.send_response(204)
        self.add_cors_headers()
        self.send_header('Content-Length', '0')
        self.end_headers()

    def do_POST(self):
        try:
            u = urllib.parse.urlparse(self.path)
            if u.path == '/api/emotion/record':
                self.send_json({'ok': True, 'data': assemble_emotion(True)})
            elif u.path == '/api/chat':
                body = self.read_json_body()
                messages = body.get('messages') or []
                try:
                    llm = chat_llm(messages)
                except Exception as e:
                    log('chat llm fail: %s' % e)
                    llm = {'mode': 'llm_error', 'reply': '', 'actions': []}
                if llm and llm.get('mode') == 'llm':
                    self.send_json({'ok': True, 'data': llm})
                else:
                    # 未配置云端大脑 → 客户端使用本地智脑（内置金融意图引擎）
                    self.send_json({'ok': True, 'data': {'mode': 'local'}})
            elif u.path == '/api/weights':
                body = self.read_json_body()
                saved = save_weights(body.get('weights') or {}) if compute_emotion else {}
                self.send_json({'ok': True, 'data': {'weights': saved}})
            elif u.path == '/api/profile':
                body = self.read_json_body()
                saved = save_profile(body.get('data') or {})
                self.send_json({'ok': True, 'data': saved})
            elif u.path == '/api/akshare/research-config':
                body = self.read_json_body(8192)
                self.send_json({'ok': True, 'data': save_akshare_research_preferences(
                    body.get('preferences') or {})})
            elif u.path == '/api/profile/brief-receipt':
                body = self.read_json_body()
                saved = update_brief_receipt(body.get('receipt') or {}, body.get('read') is not False)
                self.send_json({'ok': True, 'data': saved})
            elif u.path == '/api/profile/attention-item':
                body = self.read_json_body()
                saved = update_attention_item(body.get('item') or {}, body.get('remove') is True)
                self.send_json({'ok': True, 'data': saved})
            elif u.path == '/api/profile/attention-feedback':
                body = self.read_json_body()
                saved, learning = update_attention_feedback(
                    body.get('itemId'), body.get('signal'), body.get('surface') or 'web')
                self.send_json({'ok': True, 'data': {'profile': saved, 'learning': learning}})
            elif u.path == '/api/attention/learning/reset':
                body = self.read_json_body()
                saved, learning = reset_attention_learning(
                    body.get('kind'), body.get('clearHistory') is True)
                self.send_json({'ok': True, 'data': {'profile': saved, 'learning': learning}})
            elif u.path == '/api/attention/triage':
                body = self.read_json_body()
                self.send_json({'ok': True, 'data': update_attention_triage(
                    body.get('groupId'), body.get('action'), body.get('signal'),
                    body.get('surface') or 'web')})
            elif u.path == '/api/delivery/pull':
                body = self.read_json_body()
                self.send_json({'ok': True, 'data': claim_attention_delivery(
                    body.get('channel'), body.get('consumer') or 'local')})
            elif u.path == '/api/delivery/ack':
                body = self.read_json_body()
                self.send_json({'ok': True, 'data': acknowledge_attention_delivery(
                    body.get('channel'), body.get('itemId'), body.get('status') or 'delivered',
                    body.get('consumer') or 'local', body.get('error') or '')})
            elif u.path == '/api/delivery/retry':
                body = self.read_json_body()
                self.send_json({'ok': True, 'data': retry_attention_delivery(
                    body.get('channel'), body.get('itemId'))})
            elif u.path == '/api/diagnostics/desktop-heartbeat':
                self.send_json({'ok': True, 'data': update_desktop_heartbeat(self.read_json_body(4096))})
            elif u.path == '/api/diagnostics/repair':
                body = self.read_json_body(4096)
                self.send_json({'ok': True, 'data': repair_product_component(body.get('action'))})
            elif u.path == '/api/monitor/config':
                body = self.read_json_body()
                saved = save_monitor_config(body.get('config') or {})
                self.send_json({'ok': True, 'data': {
                    'profile': saved,
                    'monitor': background_monitor_status(),
                }})
            elif u.path == '/api/routine/config':
                body = self.read_json_body()
                saved = save_routine_config(body.get('config') or {})
                self.send_json({'ok': True, 'data': {
                    'profile': saved,
                    'routine': market_routine_status(),
                }})
            elif u.path == '/api/service-plan/preview':
                body = self.read_json_body(8192)
                self.send_json({'ok': True, 'data': parse_service_intent(body.get('text'))})
            elif u.path == '/api/service-plan/apply':
                body = self.read_json_body(16384)
                saved = apply_service_plan_draft(
                    body.get('draft'), confirmed=body.get('confirmed') is True)
                self.send_json({'ok': True, 'data': {
                    'profile': saved, 'routine': market_routine_status(),
                }})
            elif u.path == '/api/routine/action':
                body = self.read_json_body(4096)
                saved = mutate_routine_action(body.get('action'))
                self.send_json({'ok': True, 'data': {
                    'profile': saved, 'routine': market_routine_status(),
                }})
            elif u.path == '/api/routine/effectiveness':
                body = self.read_json_body(8192)
                saved, effectiveness = mutate_routine_effect(
                    body.get('action'), body.get('suggestionId'), body.get('actionId'),
                    confirmed=body.get('confirmed') is True)
                self.send_json({'ok': True, 'data': {
                    'profile': saved, 'routine': market_routine_status(),
                    'effectiveness': effectiveness,
                }})
            elif u.path == '/api/event-service/config':
                body = self.read_json_body()
                saved = save_event_service_config(body.get('config') or {})
                self.send_json({'ok': True, 'data': {
                    'profile': saved,
                    'eventService': event_service_status(include_impact=False),
                }})
            elif u.path == '/api/research-hypotheses':
                body = self.read_json_body()
                result = mutate_research_hypothesis(body.get('action'), body)
                self.send_json({'ok': True, 'data': result})
            elif u.path == '/api/research-cockpit':
                body = self.read_json_body(8192)
                self.send_json({'ok': True, 'data': mutate_research_cockpit(
                    body.get('action'), body)})
            elif u.path == '/api/research-memory':
                body = self.read_json_body(16384)
                self.send_json({'ok': True, 'data': mutate_research_memory(
                    body.get('action'), body)})
            elif u.path == '/api/research-workflows':
                body = self.read_json_body(32768)
                self.send_json({'ok': True, 'data': mutate_research_workflow(
                    body.get('action'), body)})
            elif u.path == '/api/research-suggestions':
                body = self.read_json_body(16384)
                self.send_json({'ok': True, 'data': mutate_research_suggestion(
                    body.get('action'), body)})
            elif u.path == '/api/device/config':
                body = self.read_json_body()
                saved = save_device_config(body.get('config') or {})
                self.send_json({'ok': True, 'data': {
                    'config': saved,
                    'gateway': device_gateway_status(saved, include_token=True),
                }})
            elif u.path == '/api/device/token/rotate':
                saved = save_device_config({}, rotate_token=True)
                self.send_json({'ok': True, 'data': {
                    'config': saved,
                    'gateway': device_gateway_status(saved, include_token=True),
                }})
            else:
                self.send_json({'ok': False, 'error': 'unknown api'}, 404)
        except Exception as e:
            self.send_json({'ok': False, 'error': str(e)}, 502)


# ---------------------------------------------------------------- 独立局域网设备服务（不暴露主应用 API）

class DeviceHTTPServer(DeepPulseHTTPServer):
    daemon_threads = True


class DeviceHandler(BaseHTTPRequestHandler):
    server_version = 'DeepPulse-Device/1.0'
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *args):
        pass

    def send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Connection', 'close')
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _token(self):
        token = self.headers.get('X-DeepPulse-Device-Token', '')
        auth = self.headers.get('Authorization', '')
        if not token and auth.lower().startswith('bearer '):
            token = auth[7:].strip()
        return token

    def _record(self, sha256=None):
        with _device_gateway_lock:
            _device_runtime['last_seen'] = now_bj().isoformat(timespec='seconds')
            _device_runtime['last_ip'] = self.client_address[0] if self.client_address else None
            _device_runtime['last_user_agent'] = str(self.headers.get('User-Agent') or '')[:120]
            _device_runtime['requests'] = int(_device_runtime.get('requests') or 0) + 1
            _device_runtime['last_error'] = None
            if sha256:
                _device_runtime['last_frame_sha256'] = sha256

    def do_GET(self):
        cfg = load_device_config()
        if not cfg.get('enabled'):
            self.send_json({'ok': False, 'error': 'device gateway disabled'}, 503)
            return
        if not device_token_matches(self._token(), cfg):
            self.send_json({'ok': False, 'error': 'invalid device token'}, 401)
            return
        u = urllib.parse.urlparse(self.path)
        qs = dict(urllib.parse.parse_qsl(u.query))
        demo = 'alert' if qs.get('demo') == 'alert' else ''
        try:
            if u.path == '/device/v1/health':
                self._record()
                self.send_json({'ok': True, 'data': {
                    'service': 'DeepPulse E-Paper Gateway', 'version': VERSION,
                    'time': now_bj().isoformat(timespec='seconds'),
                    'model': cfg.get('model'), 'width': EPAPER_WIDTH,
                    'height': EPAPER_HEIGHT, 'bpp': 1,
                }})
            elif u.path == '/device/v1/state':
                state = build_device_state(cfg, demo)
                self._record()
                self.send_json({'ok': True, 'data': state})
            elif u.path == '/device/v1/frame.bin':
                delivery = claim_attention_delivery('epaper', 'waveshare-esp32') if not demo else {'item': None}
                delivery_item = delivery.get('item')
                state, frame, digest = device_frame_payload(cfg, demo, delivery_item)
                self._record(digest)
                self.send_response(200)
                self.send_header('Content-Type', 'application/vnd.deeppulse.epaper-1bpp')
                self.send_header('Content-Length', str(len(frame)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Connection', 'close')
                self.send_header('X-DeepPulse-Width', EPAPER_WIDTH)
                self.send_header('X-DeepPulse-Height', EPAPER_HEIGHT)
                self.send_header('X-DeepPulse-Bpp', '1')
                self.send_header('X-DeepPulse-Frame-SHA256', digest)
                self.send_header('X-DeepPulse-Content-SHA256',
                                 state['frame']['content_sha256'])
                self.send_header('X-DeepPulse-Sequence', state.get('sequence'))
                device = state.get('device') or {}
                self.send_header('X-DeepPulse-Mode', device.get('mode') or 'focus')
                self.send_header('X-DeepPulse-Poll-Seconds', device.get('poll_seconds') or 30)
                self.send_header('X-DeepPulse-Display-Seconds', device.get('display_seconds') or 180)
                self.send_header('X-DeepPulse-Partial-Before-Full',
                                 device.get('partial_before_full') or 6)
                self.send_header('X-DeepPulse-Refresh-Policy',
                                 device.get('refresh_policy') or 'smart')
                if delivery_item:
                    item_id = re.sub(r'[\r\n]', '', str(delivery_item.get('id') or ''))[:160]
                    self.send_header('X-DeepPulse-Delivery-Item', item_id)
                self.end_headers()
                self.wfile.write(frame)
            else:
                self.send_json({'ok': False, 'error': 'unknown device endpoint'}, 404)
        except Exception as exc:
            with _device_gateway_lock:
                _device_runtime['last_error'] = str(exc)[:200]
            self.send_json({'ok': False, 'error': str(exc)}, 502)

    def do_POST(self):
        cfg = load_device_config()
        if not cfg.get('enabled'):
            self.send_json({'ok': False, 'error': 'device gateway disabled'}, 503)
            return
        if not device_token_matches(self._token(), cfg):
            self.send_json({'ok': False, 'error': 'invalid device token'}, 401)
            return
        u = urllib.parse.urlparse(self.path)
        if u.path != '/device/v1/delivery/ack':
            self.send_json({'ok': False, 'error': 'unknown device endpoint'}, 404)
            return
        try:
            length = min(int(self.headers.get('Content-Length') or 0), 4096)
            body = json.loads(self.rfile.read(length).decode('utf-8')) if length else {}
            receipt = acknowledge_attention_delivery(
                'epaper', body.get('itemId'), body.get('status') or 'delivered',
                'waveshare-esp32', body.get('error') or '')
            self._record()
            self.send_json({'ok': True, 'data': receipt})
        except Exception as exc:
            self.send_json({'ok': False, 'error': str(exc)}, 400)


def stop_device_gateway():
    global _device_gateway_server, _device_gateway_thread
    with _device_gateway_lock:
        server = _device_gateway_server
        _device_gateway_server = None
        _device_gateway_thread = None
        _device_runtime['running'] = False
    if server:
        try:
            server.shutdown()
            server.server_close()
        except Exception:
            pass


def sync_device_gateway(config=None):
    """Start or stop the restricted LAN listener according to local configuration."""
    global _device_gateway_server, _device_gateway_thread
    cfg = dict(config or load_device_config())
    enabled = bool(cfg.get('enabled'))
    port = int(cfg.get('port') or DEVICE_DEFAULT_PORT)
    with _device_gateway_lock:
        current = _device_gateway_server
        current_port = current.server_address[1] if current else None
    if current and (not enabled or current_port != port):
        stop_device_gateway()
        current = None
    if not enabled or current:
        return device_gateway_status(cfg, include_token=False)
    try:
        server = DeviceHTTPServer(('0.0.0.0', port), DeviceHandler)
        thread = threading.Thread(target=server.serve_forever,
                                  name='deeppulse-epaper-gateway', daemon=True)
        with _device_gateway_lock:
            _device_gateway_server = server
            _device_gateway_thread = thread
            _device_runtime['running'] = True
            _device_runtime['started_at'] = now_bj().isoformat(timespec='seconds')
            _device_runtime['last_error'] = None
        thread.start()
        log('e-paper device gateway start on 0.0.0.0:%d' % port)
    except Exception as exc:
        with _device_gateway_lock:
            _device_runtime['running'] = False
            _device_runtime['last_error'] = str(exc)[:200]
        log('e-paper device gateway fail: %s' % exc)
    return device_gateway_status(cfg, include_token=False)


# ---------------------------------------------------------------- 入口

def main():
    os.makedirs(DATA, exist_ok=True)
    ensure_config()
    device_config = load_device_config(persist=True)
    port = 8971
    for p in range(8971, 8981):
        if port_is_listening('127.0.0.1', p):
            continue
        try:
            srv = DeepPulseHTTPServer(('127.0.0.1', p), Handler)
            port = p
            break
        except OSError:
            continue
    else:
        print('no port available')
        sys.exit(1)
    try:
        with open(PORT_FILE, 'w') as f:
            f.write(str(port))
        with open(os.path.join(DATA, 'server.pid'), 'w') as f:
            f.write(str(os.getpid()))
    except Exception:
        pass
    log('=== DeepPulse server start on 127.0.0.1:%d ===' % port)
    print('深脉 DeepPulse 已启动: http://127.0.0.1:%d  (Ctrl+C 退出)' % port)
    sync_device_gateway(device_config)
    start_background_monitor()
    start_market_routine()
    start_event_service()

    # 预热线程：启动 2s 后后台装配一次情绪全景，让首个页面请求命中热缓存
    def warmup():
        time.sleep(2)
        try:
            t0 = time.monotonic()
            assemble_emotion()
            log('warmup done in %.1fs' % (time.monotonic() - t0))
        except Exception as e:
            log('warmup fail: %s' % e)
    threading.Thread(target=warmup, daemon=True).start()

    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log('server stopped')
        print('\n已退出。')
    finally:
        stop_event_service()
        stop_market_routine()
        stop_background_monitor()
        stop_device_gateway()


if __name__ == '__main__':
    main()
