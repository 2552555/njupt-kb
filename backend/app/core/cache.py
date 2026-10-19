"""进程内有界回源缓存（高并发专项 §8.1、§8.2、§12）。

不引入 Redis 或新依赖：本模块是进程内实现，作为缓存不可用时的回退，并按业务
分类明确「谁可以缓存、缓存故障时怎么办」，避免把缓存故障放大成数据库雪崩。

为什么这样分级（§8.1、§12.1）：
- ``PUBLIC_READ``：纯公共目录、热门候选等非敏感数据。可缓存；回源（loader）失败时
  返回过期值或 None，**绝不抛异常**——这是「有界回源」，Redis 故障时只有有限公共读
  打到数据库，不把故障放大为雪崩。
- ``VIEWER_SCOPED``：随查看者变化的数据（本人工作台等）。短 TTL，写后按 prefix 失效；
  它不是热点公共数据，回源失败按普通错误抛出，不静默吞错。
- ``AUTH_SENSITIVE``：权限、会话撤销、硬配额。**永不缓存**，缓存不可用时也必须直连
  数据库——§12.1 明确「不绕过权限」「不能用旧权限缓存放行」。
- ``NO_CACHE``：no-store 类（短期签发、水印令牌）。**永不缓存**。

Redis/缓存故障时的行为边界（§12.1 故障行为矩阵摘要）：
公共读有限回源；个性化非核心可暂缺；权限、会话撤销与硬配额不受缓存影响，始终直连
权威数据源。缓存只服务于性能，不决定业务权限或配额。

有界：条目上限 ``settings.CACHE_MAX_ENTRIES``，超出按 LRU 淘汰；命中/未命中/淘汰计数
只用于只读观测（§13），不自动处置。``settings.CACHE_ENABLED=False`` 时全部直连。
"""

from __future__ import annotations

import time
from collections import OrderedDict, defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.config import settings
from app.core.enums import CacheClass

__all__ = ["get_or_set", "invalidate", "stats", "reset"]

_Loader = Callable[[], Awaitable[Any]]

# 时钟可注入：默认单调时钟，测试可替换以确定性地验证 TTL 与淘汰，不依赖 sleep。
_monotonic: Callable[[], float] = time.monotonic


@dataclass
class _Entry:
    value: Any
    expires_at: float
    cache_class: str
    prefix: str | None


_store: "OrderedDict[str, _Entry]" = OrderedDict()
_hits = 0
_misses = 0
_evictions = 0
_invalidations = 0
_class_hits: dict[str, int] = defaultdict(int)
_class_misses: dict[str, int] = defaultdict(int)


def _is_cacheable(cache_class: CacheClass) -> bool:
    """只有公共读与查看者维度可缓存；权限/配额/会话与 no-store 类永不缓存（§8.1）。"""
    return cache_class in (CacheClass.PUBLIC_READ, CacheClass.VIEWER_SCOPED)


def _default_ttl(cache_class: CacheClass) -> int:
    if cache_class == CacheClass.VIEWER_SCOPED:
        return settings.CACHE_VIEWER_TTL_SECONDS
    return settings.CACHE_PUBLIC_TTL_SECONDS


def _evict_if_needed() -> None:
    """超过条目上限时按 LRU（最久未使用在队首）淘汰（§8.2 有界）。"""
    global _evictions
    max_entries = settings.CACHE_MAX_ENTRIES
    while len(_store) > max_entries:
        _store.popitem(last=False)
        _evictions += 1


async def get_or_set(
    key: str,
    loader: _Loader,
    *,
    cache_class: CacheClass,
    ttl: int | None = None,
    prefix: str | None = None,
) -> Any:
    """读缓存，未命中则用 loader 回源并写入；返回数据。

    - 缓存关闭或该分类不可缓存时直接 ``await loader()``，不读写进程内缓存。
    - ``PUBLIC_READ`` 回源失败时返回过期值或 None，不抛异常（有界回源，§8.2、§12.1）。
    - 其余可缓存分类回源失败按普通异常抛出。
    """
    global _hits, _misses

    cache_class = CacheClass(cache_class)
    if not settings.CACHE_ENABLED or not _is_cacheable(cache_class):
        return await loader()

    entry = _store.get(key)
    if entry is not None and entry.expires_at > _monotonic():
        _store.move_to_end(key)
        _hits += 1
        _class_hits[cache_class.value] += 1
        return entry.value

    # 过期或不存在：记一次未命中；保留过期值作为有界回源的回退值。
    stale = entry.value if entry is not None else None
    if entry is not None:
        del _store[key]
    _misses += 1
    _class_misses[cache_class.value] += 1

    try:
        value = await loader()
    except Exception:
        if cache_class == CacheClass.PUBLIC_READ:
            return stale
        raise

    _store[key] = _Entry(
        value=value,
        expires_at=_monotonic() + (ttl if ttl is not None else _default_ttl(cache_class)),
        cache_class=cache_class.value,
        prefix=prefix,
    )
    _store.move_to_end(key)
    _evict_if_needed()
    return value


def invalidate(prefix: str) -> int:
    """写后失效：移除键以 prefix 开头、或注册了同一 prefix 的条目（§8.2）。返回移除条数。"""
    global _invalidations
    removed = 0
    for key in list(_store.keys()):
        entry = _store[key]
        if key.startswith(prefix) or (entry.prefix is not None and entry.prefix == prefix):
            del _store[key]
            removed += 1
    _invalidations += removed
    return removed


def stats() -> dict:
    """只读缓存统计：条目数、上限、命中/未命中/淘汰/失效与按分类分布（§13）。"""
    by_class: dict[str, dict[str, int]] = {}
    for entry in _store.values():
        by_class.setdefault(
            entry.cache_class, {"entries": 0, "hits": 0, "misses": 0}
        )["entries"] += 1
    for cls, count in _class_hits.items():
        by_class.setdefault(cls, {"entries": 0, "hits": 0, "misses": 0})["hits"] = count
    for cls, count in _class_misses.items():
        by_class.setdefault(cls, {"entries": 0, "hits": 0, "misses": 0})["misses"] = count
    return {
        "enabled": settings.CACHE_ENABLED,
        "entries": len(_store),
        "max_entries": settings.CACHE_MAX_ENTRIES,
        "hits": _hits,
        "misses": _misses,
        "evictions": _evictions,
        "invalidations": _invalidations,
        "by_class": by_class,
    }


def reset() -> None:
    """清空缓存与计数（测试用）。"""
    global _hits, _misses, _evictions, _invalidations
    _store.clear()
    _class_hits.clear()
    _class_misses.clear()
    _hits = 0
    _misses = 0
    _evictions = 0
    _invalidations = 0
