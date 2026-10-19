"""预览水印防篡改校验构建块（prevew.md §十二）。

水印令牌绑定到 (用户, 资源, 版本)，用于校验预览水印未被篡改/冒用：
客户端请求预览时携带由本模块签发的令牌，服务端在真正渲染水印时用
verify_watermark_token 复核 (用户, 资源, 版本) 三要素是否一致。

这是"防篡改校验构建块"，真实的水印渲染由 content 模块负责（本模块不改动）；
令牌本身不带任何权限判断，权限仍由 authorize 负责。签名为 HMAC-SHA256，
密钥取自 settings.SECRET_KEY，比较用 hmac.compare_digest 做恒定时间比对。
"""

from __future__ import annotations

import hashlib
import hmac

from app.config import settings


def _build_message(*, user_id: str | None, resource_id: str, version_id: str) -> str:
    """拼装受签名的明文；匿名用户以 "anon" 占位，避免空串碰撞。"""
    uid = user_id or "anon"
    return f"{uid}|{resource_id}|{version_id}"


def compute_watermark_token(*, user_id: str | None, resource_id: str, version_id: str) -> str:
    """为 (用户, 资源, 版本) 生成 HMAC-SHA256 水印令牌。

    入参为对应 ID 的字符串形式；user_id 为 None 时按匿名处理。
    """
    message = _build_message(user_id=user_id, resource_id=resource_id, version_id=version_id)
    signature = hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return signature


def verify_watermark_token(
    token: str, *, user_id: str | None, resource_id: str, version_id: str
) -> bool:
    """校验水印令牌是否对 (用户, 资源, 版本) 有效；任何入参被篡改即返回 False。

    恒定时间比较，避免时序侧信道；令牌类型异常时返回 False 而非抛错。
    """
    expected = compute_watermark_token(
        user_id=user_id, resource_id=resource_id, version_id=version_id
    )
    try:
        return hmac.compare_digest(expected, token)
    except (TypeError, ValueError):
        return False
