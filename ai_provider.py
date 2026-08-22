"""Safe, explicit onboarding rules for the independent DeepSeek API provider.

This module contains validation and public-status shaping only.  It never reads
or writes files and never performs network I/O.  The server keeps credentials
out of profiles, diagnostics and API responses.
"""

from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from urllib.parse import urlparse


MODEL_VERSION = 'ai-provider-trust-gate-v1'
SCHEMA_VERSION = 'research-draft-schema-v1'
BJC = timezone(timedelta(hours=8))
ALLOWED_LOOPBACK = {'127.0.0.1', 'localhost', '::1'}


def _now(value=None):
    current = value if isinstance(value, datetime) else datetime.now(BJC)
    return current.astimezone(BJC) if current.tzinfo else current.replace(tzinfo=BJC)


def _iso(value=None):
    return _now(value).isoformat(timespec='seconds')


def _clean_base_url(value):
    text = str(value or 'https://api.deepseek.com').strip().rstrip('/')
    parsed = urlparse(text)
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname:
        raise ValueError('API 地址必须是完整的 HTTPS 地址')
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('API 地址不能包含账号、密码、查询参数或片段')
    if parsed.scheme != 'https' and parsed.hostname not in ALLOWED_LOOPBACK:
        raise ValueError('外部 API 地址必须使用 HTTPS；HTTP 只允许本机 loopback')
    if len(text) > 500:
        raise ValueError('API 地址过长')
    return text


def _clean_model(value):
    text = str(value or 'deepseek-chat').strip()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}', text):
        raise ValueError('模型名称格式无效')
    return text


def _clean_key(value):
    text = str(value or '').strip()
    if not (8 <= len(text) <= 4096) or any(ch.isspace() for ch in text):
        raise ValueError('API Key 格式无效')
    return text


def provider_fingerprint(base_url, model, key):
    canonical = '\n'.join((_clean_base_url(base_url), _clean_model(model), _clean_key(key)))
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def provider_host(base_url):
    parsed = urlparse(_clean_base_url(base_url))
    host = parsed.hostname or ''
    if parsed.port:
        host += ':' + str(parsed.port)
    return host


def candidate(values, current=None):
    payload = values if isinstance(values, dict) else {}
    existing = current if isinstance(current, dict) else {}
    base_url = _clean_base_url(payload.get('baseUrl') or existing.get('deepseek_base_url'))
    model = _clean_model(payload.get('model') or existing.get('deepseek_model'))
    supplied = str(payload.get('apiKey') or '').strip()
    key = _clean_key(supplied or existing.get('deepseek_api_key'))
    return {
        'baseUrl': base_url, 'model': model, 'apiKey': key,
        'host': provider_host(base_url),
        'fingerprint': provider_fingerprint(base_url, model, key),
        'keyReused': not bool(supplied),
    }


def config_fingerprint(config):
    value = config if isinstance(config, dict) else {}
    try:
        return provider_fingerprint(value.get('deepseek_base_url'), value.get('deepseek_model'),
                                    value.get('deepseek_api_key'))
    except ValueError:
        return ''


def public_status(config, now=None):
    value = config if isinstance(config, dict) else {}
    key = str(value.get('deepseek_api_key') or '').strip()
    configured = bool(key)
    try:
        base_url = _clean_base_url(value.get('deepseek_base_url'))
        model = _clean_model(value.get('deepseek_model'))
        host = provider_host(base_url)
        fingerprint = config_fingerprint(value)
        safe = True
    except ValueError:
        model = str(value.get('deepseek_model') or 'deepseek-chat')[:120]
        host = ''
        fingerprint = ''
        safe = False
    verified_fingerprint = str(value.get('deepseek_provider_verified_fingerprint') or '')
    verified_at = str(value.get('deepseek_provider_verified_at') or '')
    verified = configured and safe and bool(fingerprint) and fingerprint == verified_fingerprint
    state = 'verified' if verified else ('saved_unverified' if configured else 'unconfigured')
    chat_flag = value.get('deepseek_chat_enabled')
    # Backward-compatible migration: a pre-v1.41 manually configured key kept
    # its previous chat behavior until the user explicitly changes it.
    chat_enabled = configured and (bool(key) if chat_flag is None else chat_flag is True)
    return {
        'modelVersion': MODEL_VERSION,
        'provider': 'deepseek_api', 'state': state,
        'configured': configured, 'verified': verified, 'ready': verified,
        'host': host, 'model': model,
        'verifiedAt': verified_at if verified else None,
        'latencyMs': int(value.get('deepseek_provider_latency_ms') or 0) if verified else None,
        'schemaVersion': SCHEMA_VERSION,
        'credentialStoredInProfile': False,
        'credentialReturnedByApi': False,
        'harnessSessionAllowed': False,
        'boundary': '保存配置不会开启 AI 值班；每条研究流程仍需单独授权。',
        'services': {
            'chat': {'enabled': chat_enabled, 'separateAuthorization': True},
            'researchDuty': {'enabled': verified, 'workflowAuthorizationRequired': True},
        },
        'checkedAt': _iso(now),
        # This fingerprint is safe to expose only as a stale-write token.  It is
        # one-way and includes a high-entropy credential; never expose key hints.
        'configRevision': fingerprint[:20] if fingerprint else 'unconfigured',
    }


def test_preview(candidate_value, result, expected_revision, now=None):
    current = _now(now)
    receipt = result if isinstance(result, dict) else {}
    if not receipt.get('passed'):
        raise ValueError('只有通过连接与结构验证的结果才能保存')
    canonical = {
        'host': candidate_value['host'], 'model': candidate_value['model'],
        'fingerprint': candidate_value['fingerprint'],
        'expectedRevision': str(expected_revision or 'unconfigured'),
        'schemaVersion': SCHEMA_VERSION,
        'verifiedAt': current.isoformat(timespec='seconds'),
    }
    encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return {
        'testId': 'ai-provider-test:' + hashlib.sha256(encoded.encode('utf-8')).hexdigest()[:24],
        **canonical,
        'latencyMs': max(0, int(receipt.get('latencyMs') or 0)),
        'fieldsPassed': 6, 'fieldsTotal': 6,
        'syntheticOnly': True, 'usesResearchData': False,
        'countsAgainstDutyBudget': False,
        'expiresAt': (current + timedelta(minutes=10)).isoformat(timespec='seconds'),
        'confirmations': [
            {'id': 'store-local-credential', 'label': '将 API 配置仅保存在这台电脑的本地数据目录'},
            {'id': 'separate-duty-authorization', 'label': '理解保存配置不会自动开启任何 AI 研判值班'},
        ],
    }


def validate_confirmation(preview, confirmations, current_revision, now=None):
    value = preview if isinstance(preview, dict) else {}
    if value.get('expectedRevision') != str(current_revision or 'unconfigured'):
        raise ValueError('AI 配置已变化，请重新测试后再保存')
    try:
        if _now(now) > datetime.fromisoformat(str(value.get('expiresAt') or '')):
            raise ValueError('连接测试已过期，请重新测试')
    except (TypeError, ValueError):
        raise ValueError('连接测试已过期，请重新测试')
    confirmed = set(confirmations if isinstance(confirmations, list) else [])
    required = {row['id'] for row in value.get('confirmations') or []}
    if required - confirmed or 'confirm:ai-provider' not in confirmed:
        raise ValueError('请确认本机保存与独立授权边界')
    return True
