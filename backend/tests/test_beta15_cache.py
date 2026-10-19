"""beta15 分级有界回源缓存测试（高并发专项 §8、§12；纯进程内，不涉及真实 Redis）。

覆盖：TTL 到期回源、LRU 有界淘汰、开关关闭全部直连、AUTH_SENSITIVE/NO_CACHE 永不命中、
PUBLIC_READ 回源失败返回 None/过期值且不抛异常、prefix 写后失效、VIEWER_SCOPED TTL。
时钟可注入，测试不依赖 sleep。
"""

import unittest
from unittest.mock import patch

from app.config import settings
from app.core import cache
from app.core.enums import CacheClass


class _Clock:
    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t


class _Loader:
    """记录调用次数的回源函数工厂。"""

    def __init__(self) -> None:
        self.calls: dict[str, int] = {}

    def ok(self, key: str, value):
        async def loader():
            self.calls[key] = self.calls.get(key, 0) + 1
            return value

        return loader

    def boom(self, key: str):
        async def loader():
            self.calls[key] = self.calls.get(key, 0) + 1
            raise RuntimeError("cache backend down")

        return loader


class Beta15CacheTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        cache.reset()
        self.clock = _Clock()
        self._clock_patch = patch.object(cache, "_monotonic", self.clock)
        self._clock_patch.start()
        self.loader = _Loader()

    def tearDown(self):
        self._clock_patch.stop()
        cache.reset()

    async def test_ttl_expiry_refetches(self):
        with patch.object(settings, "CACHE_PUBLIC_TTL_SECONDS", 10):
            first = await cache.get_or_set(
                "k", self.loader.ok("k", "v1"), cache_class=CacheClass.PUBLIC_READ
            )
            second = await cache.get_or_set(
                "k", self.loader.ok("k", "v2"), cache_class=CacheClass.PUBLIC_READ
            )
            self.assertEqual((first, second), ("v1", "v1"))
            self.assertEqual(self.loader.calls["k"], 1)  # 命中，未回源

            self.clock.t += 11  # 越过 TTL
            third = await cache.get_or_set(
                "k", self.loader.ok("k", "v3"), cache_class=CacheClass.PUBLIC_READ
            )
            self.assertEqual(third, "v3")
            self.assertEqual(self.loader.calls["k"], 2)  # 过期后回源一次

    async def test_lru_evicts_oldest_at_max_entries(self):
        with patch.object(settings, "CACHE_MAX_ENTRIES", 2):
            for key in ("a", "b"):
                await cache.get_or_set(
                    key, self.loader.ok(key, key), cache_class=CacheClass.PUBLIC_READ
                )
            # 访问 a 使其成为最近使用项；随后插入 c 应淘汰最久未用的 b。
            await cache.get_or_set(
                "a", self.loader.ok("a", "a"), cache_class=CacheClass.PUBLIC_READ
            )
            await cache.get_or_set(
                "c", self.loader.ok("c", "c"), cache_class=CacheClass.PUBLIC_READ
            )

            state = cache.stats()
            self.assertEqual(state["entries"], 2)
            self.assertEqual(state["evictions"], 1)

            # b 已被淘汰：再次请求会回源；a 仍在缓存中，不回源。
            before_a = self.loader.calls["a"]
            await cache.get_or_set(
                "a", self.loader.ok("a", "a"), cache_class=CacheClass.PUBLIC_READ
            )
            self.assertEqual(self.loader.calls["a"], before_a)
            before_b = self.loader.calls["b"]
            await cache.get_or_set(
                "b", self.loader.ok("b", "b"), cache_class=CacheClass.PUBLIC_READ
            )
            self.assertEqual(self.loader.calls["b"], before_b + 1)

    async def test_cache_disabled_bypasses_cache(self):
        with patch.object(settings, "CACHE_ENABLED", False):
            for _ in range(2):
                await cache.get_or_set(
                    "k", self.loader.ok("k", 1), cache_class=CacheClass.PUBLIC_READ
                )
        self.assertEqual(self.loader.calls["k"], 2)
        state = cache.stats()
        self.assertEqual(state["entries"], 0)
        self.assertEqual(state["hits"], 0)

    async def test_auth_sensitive_never_cached(self):
        for _ in range(2):
            value = await cache.get_or_set(
                "perm", self.loader.ok("perm", "granted"), cache_class=CacheClass.AUTH_SENSITIVE
            )
            self.assertEqual(value, "granted")
        self.assertEqual(self.loader.calls["perm"], 2)  # 每次都直连
        state = cache.stats()
        self.assertEqual(state["entries"], 0)
        self.assertEqual(state["hits"], 0)

    async def test_no_cache_never_cached(self):
        for _ in range(2):
            await cache.get_or_set(
                "signed", self.loader.ok("signed", "url"), cache_class=CacheClass.NO_CACHE
            )
        self.assertEqual(self.loader.calls["signed"], 2)
        self.assertEqual(cache.stats()["entries"], 0)

    async def test_public_read_loader_failure_returns_none_without_raising(self):
        result = await cache.get_or_set(
            "k", self.loader.boom("k"), cache_class=CacheClass.PUBLIC_READ
        )
        self.assertIsNone(result)  # 有界回源：不抛异常
        self.assertEqual(cache.stats()["misses"], 1)

    async def test_public_read_loader_failure_returns_stale_value(self):
        with patch.object(settings, "CACHE_PUBLIC_TTL_SECONDS", 10):
            first = await cache.get_or_set(
                "k", self.loader.ok("k", "cached"), cache_class=CacheClass.PUBLIC_READ
            )
            self.assertEqual(first, "cached")
            self.clock.t += 11  # 过期
            stale = await cache.get_or_set(
                "k", self.loader.boom("k"), cache_class=CacheClass.PUBLIC_READ
            )
            self.assertEqual(stale, "cached")  # 回源失败时返回过期值

    async def test_viewer_scoped_loader_failure_propagates(self):
        with self.assertRaises(RuntimeError):
            await cache.get_or_set(
                "viewer:1", self.loader.boom("viewer:1"), cache_class=CacheClass.VIEWER_SCOPED
            )

    async def test_invalidate_prefix_removes_entries(self):
        with patch.object(settings, "CACHE_VIEWER_TTL_SECONDS", 60):
            for key in ("viewer:1", "viewer:2"):
                await cache.get_or_set(
                    key,
                    self.loader.ok(key, key),
                    cache_class=CacheClass.VIEWER_SCOPED,
                    prefix="viewer",
                )
            self.assertEqual(cache.stats()["entries"], 2)

            removed = cache.invalidate("viewer")
            self.assertEqual(removed, 2)
            self.assertEqual(cache.stats()["entries"], 0)

            # 失效后再次读取必须回源。
            await cache.get_or_set(
                "viewer:1",
                self.loader.ok("viewer:1", "viewer:1"),
                cache_class=CacheClass.VIEWER_SCOPED,
                prefix="viewer",
            )
            self.assertEqual(self.loader.calls["viewer:1"], 2)

    async def test_viewer_scoped_uses_viewer_ttl(self):
        with patch.object(settings, "CACHE_VIEWER_TTL_SECONDS", 5), patch.object(
            settings, "CACHE_PUBLIC_TTL_SECONDS", 100
        ):
            await cache.get_or_set(
                "v", self.loader.ok("v", "x"), cache_class=CacheClass.VIEWER_SCOPED
            )
            self.clock.t += 6  # 超过 viewer TTL，但未超过 public TTL
            await cache.get_or_set(
                "v", self.loader.ok("v", "x"), cache_class=CacheClass.VIEWER_SCOPED
            )
        self.assertEqual(self.loader.calls["v"], 2)

    async def test_stats_reports_class_distribution(self):
        await cache.get_or_set(
            "pub", self.loader.ok("pub", 1), cache_class=CacheClass.PUBLIC_READ
        )
        await cache.get_or_set(
            "view", self.loader.ok("view", 2), cache_class=CacheClass.VIEWER_SCOPED
        )
        state = cache.stats()
        self.assertEqual(state["by_class"]["public_read"]["entries"], 1)
        self.assertEqual(state["by_class"]["viewer_scoped"]["entries"], 1)
        self.assertTrue(state["enabled"])


if __name__ == "__main__":
    unittest.main()
