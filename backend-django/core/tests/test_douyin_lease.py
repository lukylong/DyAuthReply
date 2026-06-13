import asyncio

from django.test import SimpleTestCase, override_settings

from core.douyin.runtime import lease


class _FakeRedis:
    """最小化的内存假 Redis，支持 set(nx,ex)/get/expire/eval(本模块的 CAS Lua)。"""

    def __init__(self):
        self.store: dict[str, bytes] = {}

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def get(self, key):
        return self.store.get(key)

    async def expire(self, key, ttl):
        return 1 if key in self.store else 0

    async def eval(self, script, numkeys, key, value, *args):
        # 复刻 lease.py 里 RENEW/RELEASE 的 CAS 语义：值匹配才生效
        if self.store.get(key) == value:
            if "del" in script:
                self.store.pop(key, None)
            return 1
        return 0


@override_settings(DOUYIN_WORKER_LEASE_ENABLED=True, DOUYIN_WORKER_LEASE_TTL=45)
class DouyinLeaseTests(SimpleTestCase):
    def _run(self, coro):
        return asyncio.run(coro)

    def test_acquire_is_exclusive(self):
        async def scenario():
            r = _FakeRedis()
            self.assertTrue(await lease.acquire(r, "acc1", "worker-A"))
            # 另一个 worker 抢不到
            self.assertFalse(await lease.acquire(r, "acc1", "worker-B"))
            # 持有者重入 ok
            self.assertTrue(await lease.acquire(r, "acc1", "worker-A"))

        self._run(scenario())

    def test_renew_only_owner(self):
        async def scenario():
            r = _FakeRedis()
            await lease.acquire(r, "acc1", "worker-A")
            self.assertTrue(await lease.renew(r, "acc1", "worker-A"))
            self.assertFalse(await lease.renew(r, "acc1", "worker-B"))

        self._run(scenario())

    def test_release_transfers_to_other_worker(self):
        async def scenario():
            r = _FakeRedis()
            await lease.acquire(r, "acc1", "worker-A")
            # 非持有者 release 无效
            await lease.release(r, "acc1", "worker-B")
            self.assertFalse(await lease.acquire(r, "acc1", "worker-B"))
            # 持有者 release 后可被接管（模拟崩溃/优雅退出）
            await lease.release(r, "acc1", "worker-A")
            self.assertTrue(await lease.acquire(r, "acc1", "worker-B"))

        self._run(scenario())

    def test_disabled_when_redis_none(self):
        async def scenario():
            self.assertTrue(await lease.acquire(None, "acc1", "w"))
            self.assertTrue(await lease.renew(None, "acc1", "w"))
            await lease.release(None, "acc1", "w")

        self._run(scenario())
