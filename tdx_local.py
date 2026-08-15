#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""通达信 TQ-Local 的只读 HTTP JSON-RPC 适配器。

本模块只连接固定的本机回环地址，不导入 tqcenter，也不暴露任何交易方法。
通达信未安装、未运行或本地服务不可用时，调用方应自动回退到其他数据源。
"""

import json
import os
import platform
import subprocess
import time
import urllib.request
from datetime import datetime, timezone, timedelta


TDX_RPC_URL = 'http://127.0.0.1:17709/'
TDX_HOST = '127.0.0.1:17709'
TDX_HELP_URL = 'https://help.tdx.com.cn/quant/'
TDX_INSTALLER_URL = 'https://data.tdx.com.cn/level2/new_tdx64.exe'

REGISTRY_PATHS = (
    r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\通达信金融终端64',
    r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\通达信专业版',
    r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\通达信金融终端(量化模拟)',
    r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\通达信金融终端(测试)',
)

# 防止深脉被误用为交易通道。即使上游支持下单，这里也只允许行情和市场统计查询。
READ_ONLY_METHODS = frozenset({
    'get_match_stkinfo', 'get_market_snapshot', 'get_market_data',
    'get_stock_info', 'get_more_info', 'get_scjy_value_by_date',
    'get_trading_calendar', 'get_trading_dates',
})

BJ = timezone(timedelta(hours=8))
_signed_install_cache = None


class TdxLocalError(RuntimeError):
    pass


def _now_iso():
    return datetime.now(BJ).isoformat(timespec='seconds')


def _registry_install():
    if platform.system() != 'Windows':
        return None
    try:
        import winreg
    except Exception:
        return None
    views = [getattr(winreg, 'KEY_WOW64_64KEY', 0), getattr(winreg, 'KEY_WOW64_32KEY', 0)]
    for path in REGISTRY_PATHS:
        for view in views:
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0,
                                    winreg.KEY_READ | view) as key:
                    try:
                        name = winreg.QueryValueEx(key, 'DisplayName')[0]
                    except OSError:
                        name = path.rsplit('\\', 1)[-1]
                    try:
                        location = winreg.QueryValueEx(key, 'InstallLocation')[0]
                    except OSError:
                        location = ''
                    return {'name': str(name), 'location': str(location), 'registry_key': path}
            except OSError:
                continue
    return None


def _process_running():
    if platform.system() != 'Windows':
        return False
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    try:
        result = subprocess.run(
            ['tasklist', '/FI', 'IMAGENAME eq TdxW.exe', '/FO', 'CSV', '/NH'],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=3,
            creationflags=flags, check=False,
        )
        return b'tdxw.exe' in (result.stdout or b'').lower()
    except Exception:
        return False


def _signed_running_install():
    """处理部分 Python 进程看不到卸载注册表、但官方客户端确已运行的情况。

    只接受 Windows 验证为有效且签发给财富趋势公司的 TdxW.exe，避免仅凭文件名信任
    任意进程。PowerShell 命令固定且不包含外部输入。
    """
    global _signed_install_cache
    if _signed_install_cache:
        return dict(_signed_install_cache)
    command = (
        "$p=Get-Process -Name TdxW -ErrorAction SilentlyContinue|Select-Object -First 1;"
        "if($p){$s=Get-AuthenticodeSignature -LiteralPath $p.Path;"
        "[pscustomobject]@{Path=$p.Path;Status=[string]$s.Status;"
        "Signer=if($s.SignerCertificate){$s.SignerCertificate.Subject}else{$null}}|"
        "ConvertTo-Json -Compress}else{'{}'}"
    )
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    try:
        result = subprocess.run(
            ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=5,
            creationflags=flags, check=False,
        )
        payload = json.loads((result.stdout or b'{}').decode('utf-8-sig', 'replace').strip() or '{}')
    except Exception:
        return None
    signer = str(payload.get('Signer') or '')
    path = str(payload.get('Path') or '')
    trusted_signer = ('Shenzhen Fortune Trend technology Co., Ltd' in signer or
                      '深圳市财富趋势科技股份有限公司' in signer)
    if payload.get('Status') != 'Valid' or not trusted_signer or not path:
        return None
    _signed_install_cache = {
        'name': '通达信金融终端64',
        'location': os.path.dirname(path),
        'executable': path,
        'detection': 'signed_running_process',
        'signer': signer,
    }
    return dict(_signed_install_cache)


def environment_status():
    """按 TQ-Local 技能约束检查 Windows、安装注册表和 TdxW 进程。"""
    system = platform.system()
    base = {
        'supported': system == 'Windows',
        'system': system,
        'installed': False,
        'install': None,
        'process_running': False,
        'service_ready': False,
        'status': 'unsupported',
        'checked_at': _now_iso(),
        'rpc_url': TDX_RPC_URL,
        'help_url': TDX_HELP_URL,
        'installer_url': TDX_INSTALLER_URL,
        'read_only': True,
    }
    if system != 'Windows':
        return base
    process_running = _process_running()
    install = _registry_install() or _signed_install_cache
    if not install and process_running:
        install = _signed_running_install()
    base['install'] = install
    base['installed'] = bool(install)
    base['process_running'] = process_running
    if not install:
        base['status'] = 'not_installed'
        return base
    if not base['process_running']:
        base['status'] = 'not_running'
        return base
    base['status'] = 'unobserved'
    return base


def rpc_call(method, params=None, timeout=2.5):
    """调用固定回环地址上的只读 JSON-RPC 接口。"""
    if method not in READ_ONLY_METHODS:
        raise TdxLocalError('深脉只允许通达信只读接口：%s' % method)
    payload = json.dumps({
        'id': int(time.time() * 1000) % 1000000000,
        'method': method,
        'params': params or {},
    }, ensure_ascii=False).encode('utf-8')
    request = urllib.request.Request(
        TDX_RPC_URL, data=payload, method='POST',
        headers={'Content-Type': 'application/json; charset=utf-8'},
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                raise TdxLocalError('HTTP %s' % response.status)
            body = response.read().decode('utf-8', 'replace')
    except Exception as exc:
        raise TdxLocalError('TQ-Local 连接失败：%s' % exc) from exc
    latency_ms = int((time.monotonic() - started) * 1000)
    try:
        data = json.loads(body)
    except Exception as exc:
        raise TdxLocalError('TQ-Local 返回的不是有效 JSON') from exc
    if data.get('error'):
        err = data['error']
        raise TdxLocalError(str(err.get('message') if isinstance(err, dict) else err))
    if 'result' not in data:
        raise TdxLocalError('TQ-Local 响应缺少 result')
    result = data['result']
    if isinstance(result, dict) and 'ErrorId' in result:
        if str(result.get('ErrorId')) != '0':
            raise TdxLocalError(str(result.get('ErrorMsg') or result.get('Msg') or
                                    ('ErrorId=' + str(result.get('ErrorId')))))
        # 部分接口（如 get_market_snapshot / get_stock_info）把字段直接放在 result，
        # 只有批量接口才包在 Value 中。
        value = result.get('Value') if 'Value' in result else result
    else:
        value = result
    return {'value': value, 'latency_ms': latency_ms, 'result': result}


def probe_status():
    """完整就绪检查；探测请求只做名称模糊查询，不涉及账户或交易。"""
    status = environment_status()
    if status['status'] != 'unobserved':
        return status
    started = time.monotonic()
    try:
        probe = rpc_call('get_match_stkinfo', {'key_word': '茅台'}, timeout=3)
        status.update({
            'service_ready': True,
            'status': 'ok',
            'latency_ms': probe['latency_ms'],
            'probe': 'get_match_stkinfo',
        })
    except Exception as exc:
        status.update({
            'status': 'unavailable',
            'latency_ms': int((time.monotonic() - started) * 1000),
            'error': str(exc)[:240],
        })
    return status


def stock_code(code):
    """深脉纯数字 A 股代码转为 TQ-Local 标准代码。"""
    digits = ''.join(ch for ch in str(code) if ch.isdigit())
    if not digits:
        raise TdxLocalError('证券代码为空')
    if digits.startswith(('4', '8', '92')):
        suffix = 'BJ'
    elif digits.startswith(('5', '6', '9')):
        suffix = 'SH'
    else:
        suffix = 'SZ'
    return '%s.%s' % (digits, suffix)


def _number(value, default=0.0):
    if value is None or value == '' or value == '--':
        return default
    try:
        return float(str(value).replace(',', '').replace('%', '').strip())
    except Exception:
        return default


def _get_ci(mapping, key, default=None):
    if not isinstance(mapping, dict):
        return default
    wanted = key.lower()
    for actual, value in mapping.items():
        if str(actual).lower() == wanted:
            return value
    return default


def _first_mapping(value, expected=()):
    """兼容 Value 直接为对象、列表或按证券代码分组的常见返回形态。"""
    if isinstance(value, dict):
        lower = {str(k).lower() for k in value}
        if not expected or any(str(k).lower() in lower for k in expected):
            return value
        for nested in value.values():
            found = _first_mapping(nested, expected)
            if found:
                return found
    elif isinstance(value, list):
        for nested in value:
            found = _first_mapping(nested, expected)
            if found:
                return found
    return {}


def _row_list(value):
    if isinstance(value, list):
        if value and all(isinstance(row, dict) for row in value):
            return value
        for nested in value:
            rows = _row_list(nested)
            if rows:
                return rows
    if isinstance(value, dict):
        dates = _get_ci(value, 'Date')
        closes = _get_ci(value, 'Close')
        if isinstance(dates, list) and isinstance(closes, list):
            # TQ-Local 实际 K 线返回是列式对象：每个字段对应一个数组。
            columns = {str(key): column for key, column in value.items()
                       if isinstance(column, list)}
            size = max([len(column) for column in columns.values()] or [0])
            return [{key: (column[index] if index < len(column) else None)
                     for key, column in columns.items()} for index in range(size)]
        if _get_ci(value, 'Date') is not None and _get_ci(value, 'Close') is not None:
            return [value]
        for nested in value.values():
            rows = _row_list(nested)
            if rows:
                return rows
    return []


def quote(code):
    standard = stock_code(code)
    snapshot = rpc_call('get_market_snapshot', {
        'stock_code': standard,
        'field_list': ['LastClose', 'Open', 'Max', 'Min', 'Now', 'Volume', 'Amount'],
    })
    snap = _first_mapping(snapshot['value'], ('Now', 'LastClose'))
    if not snap:
        raise TdxLocalError('TQ-Local 实时行情为空')
    try:
        more_call = rpc_call('get_more_info', {
            'stock_code': standard,
            'field_list': ['ZAF', 'fHSL', 'fLianB', 'Zsz', 'Ltsz'],
        })
        more = _first_mapping(more_call['value'], ('ZAF', 'fHSL'))
    except Exception:
        more_call, more = {'latency_ms': 0}, {}
    try:
        info_call = rpc_call('get_stock_info', {
            'stock_code': standard, 'field_list': ['Name'],
        })
        info = _first_mapping(info_call['value'], ('Name',))
    except Exception:
        info_call, info = {'latency_ms': 0}, {}

    price = _number(_get_ci(snap, 'Now'))
    previous = _number(_get_ci(snap, 'LastClose'))
    change = price - previous if previous else 0.0
    calculated_pct = change / previous * 100 if previous else 0.0
    pct = _number(_get_ci(more, 'ZAF'), calculated_pct)
    return {
        'code': ''.join(ch for ch in str(code) if ch.isdigit()),
        'name': str(_get_ci(info, 'Name') or code),
        'price': price,
        'prev_close': previous,
        'open': _number(_get_ci(snap, 'Open')),
        'high': _number(_get_ci(snap, 'Max')),
        'low': _number(_get_ci(snap, 'Min')),
        'volume': _number(_get_ci(snap, 'Volume')),
        # 快照 Amount 的实际单位为万元，与 K 线文档一致；统一转为元。
        'amount': _number(_get_ci(snap, 'Amount')) * 10000,
        'turnover': _number(_get_ci(more, 'fHSL')),
        'vol_ratio': _number(_get_ci(more, 'fLianB')),
        'mktcap': _number(_get_ci(more, 'Zsz')) * 100000000,
        'float_mktcap': _number(_get_ci(more, 'Ltsz')) * 100000000,
        'pe': 0.0,
        'pb': 0.0,
        'chg': change,
        'pct': pct,
        'source': 'tdx_local',
        'source_name': '通达信 TQ-Local',
        'latency_ms': snapshot['latency_ms'] + more_call['latency_ms'] + info_call['latency_ms'],
    }


def kline(code, n=320, klt=101, fqt=1, explicit_code=None):
    standard = explicit_code or stock_code(code)
    period = {101: '1d', 102: '1w', 103: '1mon'}.get(int(klt), '1d')
    dividend = {0: 'none', 1: 'front', 2: 'back'}.get(int(fqt), 'front')
    call = rpc_call('get_market_data', {
        'field_list': ['Date', 'Open', 'High', 'Low', 'Close', 'Volume', 'Amount'],
        'stock_list': [standard], 'period': period, 'count': int(n),
        'dividend_type': dividend, 'fill_data': True,
    }, timeout=5)
    rows = []
    previous = None
    for item in _row_list(call['value']):
        close = _number(_get_ci(item, 'Close'))
        open_ = _number(_get_ci(item, 'Open'))
        high = _number(_get_ci(item, 'High'))
        low = _number(_get_ci(item, 'Low'))
        pct = ((close - previous) / previous * 100) if previous else 0.0
        amp = ((high - low) / previous * 100) if previous else 0.0
        rows.append({
            'date': str(_get_ci(item, 'Date') or _get_ci(item, 'Time') or ''),
            'open': open_, 'close': close, 'high': high, 'low': low,
            'volume': _number(_get_ci(item, 'Volume')),
            'amount': _number(_get_ci(item, 'Amount')) * 10000,
            'amp': amp, 'pct': pct, 'chg': close - previous if previous else 0.0,
            'turn': 0.0,
        })
        previous = close
    if not rows:
        raise TdxLocalError('TQ-Local K线为空')
    minimum = min(int(n), 20)
    if len(rows) < minimum:
        raise TdxLocalError('TQ-Local 本地K线仅有 %d/%d 条，交给公开行情备援' %
                            (len(rows), minimum))
    return {
        'name': str(code), 'code': str(code), 'pre': None, 'rows': rows[-int(n):],
        'source': 'tdx_local', 'source_name': '通达信 TQ-Local',
        'latency_ms': call['latency_ms'],
    }


EMOTION_FIELDS = {
    'SC03': '涨停/曾涨停家数', 'SC04': '跌停/曾跌停家数',
    'SC23': '连板家数', 'SC24': '非ST涨停/跌停家数',
    'SC30': '市场高度/二板以上家数', 'SC31': '上涨/下跌家数',
    'SC33': '涨停/跌停封单金额', 'SC35': '换手板家数/回封率',
    'SC39': '涨跌5%家数',
}


def emotion_snapshot():
    """读取通达信市场专业指标，作为情绪数据的独立交叉验证层。"""
    call = rpc_call('get_scjy_value_by_date', {
        'field_list': list(EMOTION_FIELDS), 'year': 0, 'mmdd': 0,
    }, timeout=5)
    row = _first_mapping(call['value'], tuple(EMOTION_FIELDS))
    fields = {}
    for key, label in EMOTION_FIELDS.items():
        value = _get_ci(row, key)
        if value is not None:
            fields[key] = {'label': label, 'value': value}
    if not fields:
        raise TdxLocalError('TQ-Local 市场情绪统计为空')
    return {
        'status': 'ok', 'source': 'tdx_local', 'source_name': '通达信 TQ-Local',
        'read_only': True, 'as_of': _now_iso(), 'latency_ms': call['latency_ms'],
        'fields': fields,
    }
