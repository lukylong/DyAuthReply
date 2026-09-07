import asyncio
import unittest
from datetime import timedelta
from types import SimpleNamespace

from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from unittest.mock import AsyncMock, patch

from core.douyin.runtime.worker import (
    DouyinWorker,
    ReplyGuardSnapshot,
    _can_process_reply,
    _claim_worker_command,
    _client_business_allowed,
    _fetch_pending_worker_commands,
    _load_reply_guard_snapshot,
    _mark_worker_command_consumed,
    _should_enforce_daily_peer_limit,
)
from core.douyin.runtime.message_store import ScannedMessage
from core.douyin.runtime.transport.sign_types import (
    LoginExpiredError,
    SendRiskControlError,
)


class DouyinWorkerRuntimeTests(SimpleTestCase):
    def test_can_process_reply_allows_live_session_on_non_disabled_account(self):
        self.assertTrue(_can_process_reply(2, True, True))
        self.assertTrue(_can_process_reply(1, True, False))

    def test_can_process_reply_blocks_disabled_or_disabled_auto_reply(self):
        self.assertFalse(_can_process_reply(3, True, True))
        self.assertFalse(_can_process_reply(1, False, True))
        self.assertFalse(_can_process_reply(2, True, False))

    def test_can_process_reply_blocks_receive_only_and_invalid_credentials(self):
        self.assertFalse(_can_process_reply(1, True, True, 'receive_only'))
        self.assertFalse(_can_process_reply(1, True, True, 'invalid'))
        self.assertTrue(_can_process_reply(1, True, True, 'sendable'))

    def test_daily_peer_limit_disabled_by_default(self):
        self.assertFalse(_should_enforce_daily_peer_limit())

    @override_settings(DOUYIN_ENFORCE_DAILY_PEER_REPLY_LIMIT=True)
    def test_daily_peer_limit_can_be_enabled_by_setting(self):
        self.assertTrue(_should_enforce_daily_peer_limit())

    @override_settings(
        DOUYIN_PENDING_RECOVERY_INTERVAL_S=60,
        DOUYIN_PENDING_RECOVERY_JITTER_RATIO=0,
    )
    def test_pending_recovery_is_rate_limited_per_account(self):
        worker = DouyinWorker(transport_factory=lambda: SimpleNamespace())

        self.assertTrue(worker._claim_pending_recovery("account-a", now=100.0))
        self.assertFalse(worker._claim_pending_recovery("account-a", now=159.9))
        self.assertTrue(worker._claim_pending_recovery("account-a", now=160.0))
        self.assertTrue(worker._claim_pending_recovery("account-b", now=100.0))

    @patch('core.client.license_auth.client_can_use_business', return_value=True)
    def test_client_business_allowed_when_license_ok(self, _mock):
        self.assertTrue(_client_business_allowed())

    @patch('core.client.license_auth.client_can_use_business', return_value=False)
    def test_client_business_blocked_when_license_invalid(self, _mock):
        self.assertFalse(_client_business_allowed())


class DouyinWorkerCommandClaimTests(TestCase):
    def _create_command(self, **kwargs):
        from core.douyin.douyin_worker_command_model import DouyinWorkerCommand

        defaults = {
            'channel': 'douyin:cmd:manual_reply:account-a',
            'payload': {'text': 'claim-test'},
        }
        defaults.update(kwargs)
        return DouyinWorkerCommand.objects.create(**defaults)

    def test_two_workers_fetching_same_row_only_one_can_claim_and_dispatch(self):
        command = self._create_command()

        first_snapshot = _fetch_pending_worker_commands.func()
        second_snapshot = _fetch_pending_worker_commands.func()
        self.assertEqual([row['id'] for row in first_snapshot], [str(command.id)])
        self.assertEqual([row['id'] for row in second_snapshot], [str(command.id)])

        self.assertTrue(_claim_worker_command.func(str(command.id), 'worker-a'))
        self.assertFalse(_claim_worker_command.func(str(command.id), 'worker-b'))
        self.assertEqual(_fetch_pending_worker_commands.func(), [])

        result = {'status': 'success', 'server_msg_id': 'server-message-a'}
        self.assertFalse(
            _mark_worker_command_consumed.func(str(command.id), 'worker-b', result)
        )
        self.assertTrue(
            _mark_worker_command_consumed.func(str(command.id), 'worker-a', result)
        )

        command.refresh_from_db()
        self.assertIsNotNone(command.consumed_at)
        self.assertIsNone(command.claimed_at)
        self.assertEqual(command.claim_owner, '')
        self.assertEqual(command.payload['_result'], result)

    @override_settings(DOUYIN_DB_COMMAND_CLAIM_TTL_S=60)
    def test_fresh_claim_is_hidden_but_stale_claim_can_be_recovered(self):
        fresh = self._create_command(
            payload={'text': 'fresh'},
            claimed_at=timezone.now(),
            claim_owner='worker-old',
        )
        stale = self._create_command(
            payload={'text': 'stale'},
            claimed_at=timezone.now() - timedelta(seconds=61),
            claim_owner='worker-dead',
        )

        pending_ids = {
            row['id'] for row in _fetch_pending_worker_commands.func()
        }
        self.assertNotIn(str(fresh.id), pending_ids)
        self.assertIn(str(stale.id), pending_ids)
        self.assertFalse(_claim_worker_command.func(str(fresh.id), 'worker-new'))
        self.assertTrue(_claim_worker_command.func(str(stale.id), 'worker-new'))

        stale.refresh_from_db()
        self.assertEqual(stale.claim_owner, 'worker-new')
        self.assertGreater(stale.claimed_at, timezone.now() - timedelta(seconds=5))


class DouyinProfileBackfillSchedulingTests(unittest.IsolatedAsyncioTestCase):
    @override_settings(DOUYIN_PROFILE_BACKFILL_INTERVAL_S=300)
    async def test_profile_backfill_is_background_and_rate_limited(self):
        worker = DouyinWorker(transport_factory=lambda: SimpleNamespace())
        account = SimpleNamespace(id="account-profile")
        transport = SimpleNamespace()
        backfill = AsyncMock(return_value=None)

        with patch(
            "core.douyin.runtime.worker._backfill_missing_peer_profiles",
            new=backfill,
        ):
            worker._schedule_profile_backfill(account, transport, "account-profile")
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            worker._schedule_profile_backfill(account, transport, "account-profile")
            await asyncio.sleep(0)

        self.assertEqual(backfill.await_count, 1)


class DouyinManualReplyFailureLoggingTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_send_failure_branches_await_event_logging(self):
        cases = (
            (
                LoginExpiredError(
                    "session expired",
                    http_status=401,
                    proto_status_code=1001,
                ),
                "手动发送失败",
            ),
            (
                SendRiskControlError(
                    "send blocked",
                    biz_status_code=8610,
                    raw_check_code=2,
                ),
                "手动发送被封控",
            ),
            (RuntimeError("transport failed"), "手动发送失败"),
        )

        for send_error, expected_title in cases:
            with self.subTest(error_type=type(send_error).__name__):
                worker = DouyinWorker(transport_factory=lambda: SimpleNamespace())
                account = SimpleNamespace(
                    id="account-manual",
                    owner_id="owner-manual",
                    credential_state="sendable",
                    last_probe_error="",
                )
                conversation = SimpleNamespace(peer_nickname="peer")
                transport = SimpleNamespace(
                    send_text=AsyncMock(side_effect=send_error),
                )
                event_log = AsyncMock(return_value=None)

                with (
                    patch(
                        "core.douyin.runtime.worker._client_business_allowed_async",
                        new=AsyncMock(return_value=True),
                    ),
                    patch(
                        "core.douyin.runtime.worker._fetch_account_orm",
                        new=AsyncMock(return_value=account),
                    ),
                    patch(
                        "core.douyin.runtime.worker._fetch_conversation",
                        new=AsyncMock(return_value=conversation),
                    ),
                    patch.object(
                        worker,
                        "_get_or_create_transport",
                        new=AsyncMock(return_value=transport),
                    ),
                    patch(
                        "core.douyin.runtime.worker.mark_account_login_invalid",
                        new=AsyncMock(return_value=None),
                    ),
                    patch(
                        "core.douyin.runtime.worker._mark_credential_invalid",
                        new=AsyncMock(return_value=None),
                    ),
                    patch(
                        "core.douyin.runtime.worker.mark_account_send_restricted",
                        new=AsyncMock(return_value=None),
                    ),
                    patch(
                        "core.douyin.runtime.worker.push_to_user",
                        new=AsyncMock(return_value=None),
                    ),
                    patch(
                        "core.douyin.runtime.worker._log_event",
                        new=event_log,
                    ),
                ):
                    result = await worker._send_manual_reply(
                        "account-manual",
                        conversation_id="conversation-manual",
                        text="hello",
                    )

                self.assertEqual(result["status"], "failed")
                event_log.assert_awaited_once()
                self.assertEqual(event_log.await_args.args[3], expected_title)


class DouyinReplyGuardBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_uses_one_snapshot_boundary_for_duplicate(self):
        worker = DouyinWorker(transport_factory=lambda: SimpleNamespace())
        account = SimpleNamespace(
            id="account-guard",
            status=1,
            auto_reply_enabled=True,
            credential_state="sendable",
            group_id=None,
        )
        msg = ScannedMessage(
            message_id="message-guard",
            conversation_id="conversation-guard",
            peer_sec_uid="peer-guard",
            peer_nickname="peer",
            text="hello",
            received_at="2026-09-04T12:00:00",
        )
        snapshot = AsyncMock(
            return_value=ReplyGuardSnapshot(
                conversation_owned=True,
                trigger_replied=True,
                session_active=True,
            )
        )
        mark_processed = AsyncMock(return_value=None)

        with (
            patch(
                "core.douyin.runtime.worker._load_reply_guard_snapshot",
                new=snapshot,
            ),
            patch(
                "core.douyin.runtime.worker._mark_message_processed",
                new=mark_processed,
            ),
            patch(
                "core.douyin.runtime.worker._trigger_already_replied",
                new=AsyncMock(),
            ) as legacy_trigger,
            patch(
                "core.douyin.runtime.worker._session_is_active",
                new=AsyncMock(),
            ) as legacy_session,
            patch(
                "core.douyin.runtime.worker._conversation_belongs_to_account",
                new=AsyncMock(),
            ) as legacy_ownership,
        ):
            await worker._handle_one_message(account, msg, [], "owner-guard")

        snapshot.assert_awaited_once()
        mark_processed.assert_awaited_once_with("message-guard")
        legacy_trigger.assert_not_awaited()
        legacy_session.assert_not_awaited()
        legacy_ownership.assert_not_awaited()


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


class DouyinReplyGuardSnapshotTests(TestCase):
    def setUp(self):
        from django.utils import timezone
        from core.douyin.douyin_account_model import DouyinAccount
        from core.douyin.douyin_conversation_model import DouyinConversation
        from core.douyin.douyin_message_model import DouyinMessage
        from core.user.user_model import User

        self.now = timezone.now()
        owner = User.objects.create(
            username="reply_guard_owner",
            password="test-password",
            email="reply-guard@example.com",
        )
        self.account = DouyinAccount.objects.create(
            nickname="reply-guard-account",
            owner=owner,
            sec_uid="reply-guard-self",
            status=1,
        )
        self.conversation = DouyinConversation.objects.create(
            account=self.account,
            peer_sec_uid="reply-guard-peer",
            platform_conversation_id="0:1:30001:40002",
        )
        self.inbound = DouyinMessage.objects.create(
            conversation=self.conversation,
            external_msg_id="guard-inbound",
            direction="in",
            content="hello world",
            received_at=self.now,
            processed=False,
        )

    def _snapshot(self):
        return _load_reply_guard_snapshot.func(
            account_id=str(self.account.id),
            account_status=1,
            account_group_id="",
            message_id=str(self.inbound.id),
            conversation_id=str(self.conversation.id),
            peer_sec_uid=self.conversation.peer_sec_uid,
            peer_nickname="peer",
            text="hello   world",
            rule_id="",
            cooldown_seconds=0,
            enforce_daily_peer_limit=True,
            is_mutual_follow=False,
        )

    def test_echo_guard_uses_one_async_boundary_and_three_sql_reads(self):
        from core.douyin.douyin_message_model import DouyinMessage

        DouyinMessage.objects.create(
            conversation=self.conversation,
            external_msg_id="guard-outbound",
            direction="out",
            content="hello world",
            received_at=self.now,
            processed=True,
        )
        with (
            patch(
                "core.douyin.runtime.worker._enabled_blacklist_cached",
                return_value=[],
            ),
            self.assertNumQueries(3),
        ):
            snapshot = self._snapshot()

        self.assertTrue(snapshot.conversation_owned)
        self.assertTrue(snapshot.session_active)
        self.assertTrue(snapshot.echo_match)

    def test_duplicate_and_daily_guard_share_primary_sql_snapshot(self):
        from core.douyin.douyin_reply_log_model import DouyinReplyLog

        DouyinReplyLog.objects.create(
            account=self.account,
            conversation=self.conversation,
            trigger_message=self.inbound,
            reply_text="sent",
            result="success",
            sent_at=self.now,
        )
        with self.assertNumQueries(1):
            snapshot = self._snapshot()

        self.assertTrue(snapshot.trigger_replied)
        self.assertTrue(snapshot.daily_peer_replied)
        self.assertFalse(snapshot.echo_match)


class DouyinSendRestrictionStatusTests(TestCase):
    async def test_mark_send_restricted_is_persisted_for_account_list(self):
        from core.douyin.douyin_account_model import DouyinAccount
        from core.douyin.runtime.account_status import mark_account_send_restricted
        from core.user.user_model import User

        owner = await User.objects.acreate(
            username='risk_owner',
            password='test_password',
            email='risk@example.com',
        )
        account = await DouyinAccount.objects.acreate(
            nickname='发送受限账号',
            owner=owner,
            status=1,
            credential_state='sendable',
        )

        await mark_account_send_restricted(
            str(account.id),
            '账号发送封控（business=7911 raw_check=2）',
        )

        await account.arefresh_from_db()
        self.assertEqual(account.status, 1)
        self.assertEqual(account.credential_state, 'receive_only')
        self.assertIn('7911', account.last_probe_error)
        self.assertIsNotNone(account.last_probe_at)

    async def test_reconcile_restores_restriction_cleared_by_legacy_probe(self):
        from datetime import timedelta

        from core.douyin.douyin_account_model import DouyinAccount
        from core.douyin.douyin_event_model import DouyinEvent
        from core.douyin.runtime.account_status import reconcile_send_restrictions
        from core.user.user_model import User
        from django.utils import timezone

        owner = await User.objects.acreate(
            username='legacy_probe_owner',
            password='test_password',
            email='legacy-probe@example.com',
        )
        account = await DouyinAccount.objects.acreate(
            nickname='被旧探活覆盖的账号',
            owner=owner,
            status=1,
            credential_state='sendable',
            last_login_at=timezone.now() - timedelta(hours=2),
        )
        await DouyinEvent.objects.acreate(
            account=account,
            event_type='risk_alert',
            level='warning',
            title='账号 被旧探活覆盖的账号 发送被封控',
            detail='账号发送封控（business=7911 raw_check=2）',
            occurred_at=timezone.now() - timedelta(hours=1),
        )

        restored = await reconcile_send_restrictions()

        await account.arefresh_from_db()
        self.assertEqual(restored, 1)
        self.assertEqual(account.credential_state, 'receive_only')
        self.assertIn('7911', account.last_probe_error)
