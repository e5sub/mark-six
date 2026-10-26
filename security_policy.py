"""口令与登录防爆破策略常量。

集中定义，避免各入口（Web 注册、移动端 API、后台改密、个人中心改密）
的校验强度不一致。
"""

from __future__ import annotations

import re

__all__ = [
    "MIN_PASSWORD_LENGTH",
    "MAX_PASSWORD_LENGTH",
    "password_policy_error",
    "LOGIN_FAILURE_WINDOW_SECONDS",
    "LOGIN_ACCOUNT_FAILURE_LIMIT",
    "LOGIN_IP_FAILURE_LIMIT",
]

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128

# 登录失败计数窗口与阈值：账号维度更严格，用于阻断针对单一账号的在线爆破；
# IP 维度放宽，避免同一出口 NAT 下的正常用户被误伤。
LOGIN_FAILURE_WINDOW_SECONDS = 900
LOGIN_ACCOUNT_FAILURE_LIMIT = 5
LOGIN_IP_FAILURE_LIMIT = 30

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def password_policy_error(password) -> str:
    """返回不合规原因，合规则返回空串。"""
    raw = str(password or "")
    if len(raw) < MIN_PASSWORD_LENGTH:
        return f"密码长度至少 {MIN_PASSWORD_LENGTH} 位"
    if len(raw) > MAX_PASSWORD_LENGTH:
        return f"密码长度不能超过 {MAX_PASSWORD_LENGTH} 位"
    if not raw.strip():
        return "密码不能全部为空白字符"
    if _CONTROL_CHARS.search(raw):
        return "密码不能包含控制字符"
    return ""
