"""限流（设计文档 §28）。

结合用户、会话与来源判断，不以单一 IP 判定校园共享网络全部异常。
Redis 不可用时降级为进程内计数并明确放宽，不因依赖故障默认放行关键权限。
"""

from __future__ import annotations

import time
from collections import defaultdict

from app.config import settings

_memory_buckets: dict[str, list[float]] = defaultdict(list)
_redis = None
_redis_checked = False


async def _get_redis():
    global _redis, _redis_checked
    if _redis_checked:
        return _redis
    _redis_checked = True
    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        await client.ping()
        _redis = client
    except Exception:  # noqa: BLE001 - 降级而非失败
        _redis = None
    return _redis


async def check_rate_limit(bucket: str, *, limit: int, window_seconds: int) -> bool:
    """返回 True 表示允许。超过限制返回 False，由调用方抛 RateLimited。"""
    key = f"rl:{bucket}"
    client = await _get_redis()
    if client is not None:
        try:
            count = await client.incr(key)
            if count == 1:
                await client.expire(key, window_seconds)
            return count <= limit
        except Exception:  # noqa: BLE001 - 回落到内存计数
            pass

    now = time.monotonic()
    window_start = now - window_seconds
    hits = [t for t in _memory_buckets[key] if t >= window_start]
    hits.append(now)
    _memory_buckets[key] = hits
    return len(hits) <= limit


async def reset_rate_limit(bucket: str) -> None:
    key = f"rl:{bucket}"
    client = await _get_redis()
    if client is not None:
        try:
            await client.delete(key)
        except Exception:  # noqa: BLE001
            pass
    _memory_buckets.pop(key, None)


async def cache_get(key: str) -> str | None:
    client = await _get_redis()
    if client is None:
        return None
    try:
        return await client.get(key)
    except Exception:  # noqa: BLE001
        return None


async def cache_set(key: str, value: str, ttl_seconds: int) -> None:
    client = await _get_redis()
    if client is None:
        return
    try:
        await client.set(key, value, ex=ttl_seconds)
    except Exception:  # noqa: BLE001
        return


async def cache_delete(*keys: str) -> None:
    client = await _get_redis()
    if client is None or not keys:
        return
    try:
        await client.delete(*keys)
    except Exception:  # noqa: BLE001
        return


async def redis_healthy() -> bool:
    client = await _get_redis()
    if client is None:
        return False
    try:
        await client.ping()
        return True
    except Exception:  # noqa: BLE001
        return False
