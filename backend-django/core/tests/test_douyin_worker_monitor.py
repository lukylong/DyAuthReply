from django.test import TestCase, override_settings

from core.douyin.douyin_worker_monitor_collector import collect_worker_monitor_overview
from core.douyin.runtime.sharding import stable_bucket


class DouyinWorkerMonitorCollectorTests(TestCase):
    @override_settings(
        DOUYIN_WORKER_SHARD_COUNT=3,
        DOUYIN_WORKER_LEASE_ENABLED=True,
        DOUYIN_WORKER_LEASE_TTL=45,
    )
    def test_overview_structure(self):
        data = collect_worker_monitor_overview()
        self.assertEqual(data['shard_count'], 3)
        self.assertEqual(len(data['shards']), 3)
        self.assertIn('issues', data)
        self.assertIn('redis_ok', data)
        self.assertIn('workers', data)

    def test_stable_bucket_in_shard_range(self):
        for i in range(50):
            aid = f'test-acc-{i}'
            b = stable_bucket(aid, 3)
            self.assertIn(b, (0, 1, 2))
