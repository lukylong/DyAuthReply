from django.test import SimpleTestCase, override_settings

from core.douyin.runtime.worker import (
    _can_process_reply,
    _should_enforce_daily_peer_limit,
)


class DouyinWorkerRuntimeTests(SimpleTestCase):
    def test_can_process_reply_allows_live_session_on_non_disabled_account(self):
        self.assertTrue(_can_process_reply(2, True, True))
        self.assertTrue(_can_process_reply(1, True, False))

    def test_can_process_reply_blocks_disabled_or_disabled_auto_reply(self):
        self.assertFalse(_can_process_reply(3, True, True))
        self.assertFalse(_can_process_reply(1, False, True))
        self.assertFalse(_can_process_reply(2, True, False))

    def test_daily_peer_limit_disabled_by_default(self):
        self.assertFalse(_should_enforce_daily_peer_limit())

    @override_settings(DOUYIN_ENFORCE_DAILY_PEER_REPLY_LIMIT=True)
    def test_daily_peer_limit_can_be_enabled_by_setting(self):
        self.assertTrue(_should_enforce_daily_peer_limit())
