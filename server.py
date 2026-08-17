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
import os
import re
import socket
import sys
import threading
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta, date as _date
from http.client import RemoteDisconnected
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    import tdx_local as tdx_local_api
except Exception:
    tdx_local_api = None

# ---------------------------------------------------------------- 基础配置
BASE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(BASE, 'web')
DATA = os.path.join(BASE, 'data')
HISTORY_FILE = os.path.join(DATA, 'history.json')
PROFILE_FILE = os.path.join(DATA, 'profile.json')
SECTOR_HISTORY_FILE = os.path.join(DATA, 'sector_history.json')
PORT_FILE = os.path.join(DATA, 'port.txt')
LOG_FILE = os.path.join(DATA, 'server.log')
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
VERSION = '1.4.2'

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
]


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
            'circuit_seconds': circuit,
        })
        if environment is not None:
            item['environment'] = environment
        items.append(item)
    return {
        'generated_at': now_bj().isoformat(timespec='seconds'),
        'items': items,
        'policy': '官方披露优先；通达信本地源仅作只读行情增强和交叉验证；市场聚合用于备援与线索；未观测不等于可用。',
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
           '&fields=f12,f13,f14' % '{HOST}')
    j = json_loads(fetch_clist_any(url))
    out = []
    for d in ((j.get('data') or {}).get('diff') or []):
        out.append({'code': d.get('f12'), 'market': d.get('f13'),
                    'name': d.get('f14')})
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
        '变化方向：%s，单期变化 %s°，三期变化 %s°；数据覆盖率 %s%%，可信度 %s%%，信号一致度 %s%%。'
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
        '引擎研究区间：仓位 %s，%s，可执行=%s。阶段说明：%s'
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


# ---------------------------------------------------------------- HTTP 服务

class Handler(BaseHTTPRequestHandler):
    server_version = 'DeepPulse/1.4.2'
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
                          'bridge_protocol': 2,
                          'emotion_context_full': True,
                      }}
            # 顶层字段兼容桌面宿主，data 字段供统一 Web API 客户端使用。
            self.send_json(dict({'ok': True, 'data': health}, **health))
        elif path == '/api/sources':
            self.send_json({'ok': True, 'data': source_catalog()})
        elif path == '/api/tdx/status':
            probe = qs.get('probe', '1') != '0'
            fresh = qs.get('fresh') == '1'
            self.send_json({'ok': True, 'data': tdx_status(probe=probe, fresh=fresh)})
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
            else:
                self.send_json({'ok': False, 'error': 'unknown api'}, 404)
        except Exception as e:
            self.send_json({'ok': False, 'error': str(e)}, 502)


# ---------------------------------------------------------------- 入口

def main():
    os.makedirs(DATA, exist_ok=True)
    ensure_config()
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


if __name__ == '__main__':
    main()
