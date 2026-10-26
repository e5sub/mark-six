"""统一限流工具：可信客户端 IP 解析 + 滑动窗口计数器。

设计要点
--------
1. 默认**不信任**任何转发头。只有在显式声明部署在可信代理后方时，才读取
   ``CF-Connecting-IP``（``TRUST_CF_HEADERS=1``）；``TRUST_PROXY_HEADERS=1``
   时由 ``werkzeug.middleware.proxy_fix.ProxyFix`` 把 ``X-Forwarded-For``
   解析进 ``request.remote_addr``，因此这里无需再读原始头。
   这样就堵住了「客户端自带 X-Forwarded-For 即可绕过限流」的漏洞。
2. 提供三原语，方便「失败才计数」的场景（登录爆破防护）：
   ``rate_blocked`` 只读判断，``rate_record`` 记账，``rate_reset`` 清零。
"""

from __future__ import annotations

import ipaddress
import os
import threading
import time

from flask import has_request_context, request

__all__ = [
    "client_ip",
    "rate_blocked",
    "rate_record",
    "rate_reset",
    "rate_limited",
    "trust_proxy_headers",
    "trust_cf_headers",
]

_TRUTHY = {"1", "true", "yes", "on"}

trust_proxy_headers = os.environ.get("TRUST_PROXY_HEADERS", "").strip().lower() in _TRUTHY
trust_cf_headers = os.environ.get("TRUST_CF_HEADERS", "").strip().lower() in _TRUTHY

_BUCKETS: dict[str, list[float]] = {}
_LOCK = threading.Lock()
_MAX_BUCKETS = 20000


def _valid_ip(raw: str) -> str:
    """规范化 IP 字面量，非法输入返回空串（顺带挡掉超长垃圾值）。"""
    text = str(raw or "").strip()
    if not text or len(text) > 45:
        return ""
    try:
        return str(ipaddress.ip_address(text))
    except ValueError:
        return ""


def client_ip() -> str:
    """返回可用于限流的客户端标识。

    优先使用 Cloudflare 提供的真实 IP（仅在显式开启时），否则使用 TCP 层
    对端地址。绝不无条件读取 ``X-Forwarded-For``。
    """
    if not has_request_context():
        return "unknown"

    if trust_cf_headers:
        cf_ip = _valid_ip(request.headers.get("CF-Connecting-IP", ""))
        if cf_ip:
            return cf_ip

    return _valid_ip(request.remote_addr or "") or "unknown"


def _prune(bucket, window_seconds: float, now: float):
    cutoff = now - window_seconds
    return [timestamp for timestamp in bucket if timestamp >= cutoff]


def _load(key: str, window_seconds: float, now: float):
    bucket = _prune(_BUCKETS.get(key, ()), window_seconds, now)
    if bucket:
        _BUCKETS[key] = bucket
    else:
        _BUCKETS.pop(key, None)
    return bucket


def _evict(now: float) -> None:
    """容量保护：桶过多时丢弃最早的一半，避免内存被随机 key 打爆。"""
    if len(_BUCKETS) <= _MAX_BUCKETS:
        return
    for key in list(_BUCKETS)[: _MAX_BUCKETS // 2]:
        _BUCKETS.pop(key, None)


def rate_blocked(key: str, limit: int, window_seconds: float) -> bool:
    """只判断是否已达上限，不记账。"""
    limit = max(int(limit), 1)
    now = time.time()
    with _LOCK:
        return len(_load(key, window_seconds, now)) >= limit


def rate_record(key: str, window_seconds: float, amount: int = 1) -> int:
    """记录若干次尝试，返回当前窗口内的累计次数。"""
    step = max(int(amount), 1)
    now = time.time()
    with _LOCK:
        bucket = _load(key, window_seconds, now)
        bucket.extend([now] * step)
        _BUCKETS[key] = bucket
        _evict(now)
        return len(bucket)


def rate_reset(key: str) -> None:
    with _LOCK:
        _BUCKETS.pop(key, None)


def rate_limited(key: str, limit: int, window_seconds: float) -> bool:
    """记录一次访问并返回是否超限（先判断、未超限才记账）。"""
    limit = max(int(limit), 1)
    now = time.time()
    with _LOCK:
        bucket = _load(key, window_seconds, now)
        if len(bucket) >= limit:
            return True
        bucket.append(now)
        _BUCKETS[key] = bucket
        _evict(now)
        return False
