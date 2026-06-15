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


class DouyinSelfUidInferenceTests(SimpleTestCase):
    def test_infer_from_multiple_conversations(self):
        from core.douyin.runtime.transport.http_protocol import HttpProtocolTransport
        
        # Managed account: 88888 (appears in both conversations)
        # Peers: 11111, 22222
        conv_ids = [
            "0:1:11111:88888",
            "0:1:22222:88888",
        ]
        inferred = HttpProtocolTransport._infer_self_uid_from_conversation_ids(conv_ids)
        self.assertEqual(inferred, 88888)

    def test_infer_from_single_conversation_with_peer_exclusion(self):
        from core.douyin.runtime.transport.http_protocol import HttpProtocolTransport
        
        # 1 conversation: UIDs are 12345 and 67890 (both have frequency 1)
        # Known peer: 67890 (from message logs)
        # Should exclude peer and return 12345
        conv_ids = ["0:1:12345:67890"]
        inferred = HttpProtocolTransport._infer_self_uid_from_conversation_ids(
            conv_ids, peer_uids={67890}
        )
        self.assertEqual(inferred, 12345)

    def test_infer_empty_or_invalid_returns_zero(self):
        from core.douyin.runtime.transport.http_protocol import HttpProtocolTransport
        
        self.assertEqual(HttpProtocolTransport._infer_self_uid_from_conversation_ids([]), 0)
        self.assertEqual(HttpProtocolTransport._infer_self_uid_from_conversation_ids(["invalid"]), 0)

