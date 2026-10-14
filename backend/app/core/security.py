"""密码与会话令牌（设计文档 §14.2）。

- 密码采用专用密码哈希方案：Argon2id。
- 同站 Web 使用随机会话令牌，数据库只保存令牌摘要，不保存可直接使用的明文令牌。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError, VerificationError

# 参数取 argon2-cffi 默认值（time_cost=3, memory_cost=65536, parallelism=4），
# 属于库维护的当前推荐基线，不在应用侧另设一套数字。
_hasher = PasswordHasher()

MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 256

SESSION_TOKEN_BYTES = 32
CSRF_TOKEN_BYTES = 32
ONE_TIME_TOKEN_BYTES = 32


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def generate_token(nbytes: int = SESSION_TOKEN_BYTES) -> str:
    """生成可直接下发给客户端的一次性随机令牌。"""
    return secrets.token_urlsafe(nbytes)


def token_digest(token: str) -> str:
    """数据库只保存摘要；摘要用于等值查找，不需要加盐。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def constant_time_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)


def password_problem(password: str) -> str | None:
    """返回不满足要求的原因，满足时返回 None。错误响应不暴露账号是否存在（§14.2）。"""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"密码至少 {MIN_PASSWORD_LENGTH} 位"
    if len(password) > MAX_PASSWORD_LENGTH:
        return "密码过长"
    return None
