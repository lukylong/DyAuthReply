from django.test import SimpleTestCase, override_settings

from core.douyin.runtime.sharding import (
    owns,
    shard_count,
    shard_index,
    stable_bucket,
)


class DouyinShardingTests(SimpleTestCase):
    def test_default_single_shard_owns_everything(self):
        self.assertEqual(shard_count(), 1)
        self.assertEqual(shard_index(), 0)
        for aid in ("a", "b", "c", "1234", "abcdef-uuid"):
            self.assertTrue(owns(aid))

    def test_stable_bucket_is_deterministic(self):
        a = stable_bucket("account-xyz", 8)
        b = stable_bucket("account-xyz", 8)
        self.assertEqual(a, b)
        self.assertTrue(0 <= a < 8)

    def test_partition_is_disjoint_and_complete(self):
        """每个账号恰好属于一个分片，且所有分片并起来覆盖全集。"""
        n = 4
        account_ids = [f"acc-{i}" for i in range(200)]
        owned_counts = {i: 0 for i in range(n)}
        for aid in account_ids:
            hits = [i for i in range(n) if owns(aid, index=i, count=n)]
            self.assertEqual(len(hits), 1, f"{aid} 应恰好属于一个分片，实际 {hits}")
            owned_counts[hits[0]] += 1
        self.assertEqual(sum(owned_counts.values()), len(account_ids))
        # 各分片大致均衡（不要求严格相等，但不应有空分片）
        for i in range(n):
            self.assertGreater(owned_counts[i], 0)

    @override_settings(DOUYIN_WORKER_SHARD_COUNT=3, DOUYIN_WORKER_SHARD_INDEX=5)
    def test_out_of_range_index_is_normalized(self):
        self.assertEqual(shard_count(), 3)
        self.assertEqual(shard_index(), 2)  # 5 % 3

    @override_settings(DOUYIN_WORKER_SHARD_COUNT=2, DOUYIN_WORKER_SHARD_INDEX=0)
    def test_owns_reads_settings(self):
        results = {owns(f"x{i}") for i in range(50)}
        # 分片 0 不会拥有全部，也不会一个都不拥有
        self.assertIn(True, results)
