from django.test import TestCase
from django.utils import timezone
from core.user.user_model import User
from core.douyin.douyin_account_model import DouyinAccount
from core.douyin.douyin_conversation_model import DouyinConversation
from core.douyin.douyin_message_model import DouyinMessage
from core.douyin.runtime.message_store import _upsert_conversation_and_message


class DouyinMediaUpsertTests(TestCase):

    def setUp(self):
        # Create a test owner user
        self.owner = User.objects.create(
            username="test_owner",
            password="test_password",
            email="test@example.com",
        )
        # Create a test account
        self.account = DouyinAccount.objects.create(
            id="test-account-id",
            nickname="TestAccount",
            sec_uid="test-account-sec-uid",
            status=1,
            auto_reply_enabled=True,
            owner=self.owner,
        )
        self.peer_sec_uid = "test-peer-sec-uid"
        self.received_at = timezone.now()

    async def test_upsert_normal_text_message(self):
        res = await _upsert_conversation_and_message(
            account_id=self.account.id,
            peer_sec_uid=self.peer_sec_uid,
            peer_nickname="PeerUser",
            text="Hello world",
            received_at=self.received_at,
            raw={
                "source": "test",
                "content_json": '{"text": "Hello world"}'
            },
            external_msg_id="ext_msg_1",
            direction="in"
        )
        self.assertIsNotNone(res)
        conv_id, msg_id = res
        
        # Verify DB entry
        msg = await DouyinMessage.objects.aget(id=msg_id)
        self.assertEqual(msg.content_type, "text")
        self.assertEqual(msg.content, "Hello world")

        conv = await DouyinConversation.objects.aget(id=conv_id)
        self.assertEqual(conv.last_message_preview, "Hello world")

    async def test_upsert_image_message(self):
        res = await _upsert_conversation_and_message(
            account_id=self.account.id,
            peer_sec_uid=self.peer_sec_uid,
            peer_nickname="PeerUser",
            text="[图片]",
            received_at=self.received_at,
            raw={
                "source": "test",
                "content_json": '{"image": {"url": "https://example.com/image.jpg", "width": 100, "height": 100}}'
            },
            external_msg_id="ext_msg_2",
            direction="in"
        )
        self.assertIsNotNone(res)
        conv_id, msg_id = res

        msg = await DouyinMessage.objects.aget(id=msg_id)
        self.assertEqual(msg.content_type, "text")
        self.assertEqual(msg.content, "[图片]")

        # The preview in the conversation list should still be "[图片]"
        conv = await DouyinConversation.objects.aget(id=conv_id)
        self.assertEqual(conv.last_message_preview, "[图片]")

    async def test_upsert_sticker_emoji_message(self):
        res = await _upsert_conversation_and_message(
            account_id=self.account.id,
            peer_sec_uid=self.peer_sec_uid,
            peer_nickname="PeerUser",
            text="[表情]",
            received_at=self.received_at,
            raw={
                "source": "test",
                "content_json": '{"emoji": {"url_list": ["https://example.com/sticker.png"]}}'
            },
            external_msg_id="ext_msg_3",
            direction="in"
        )
        self.assertIsNotNone(res)
        _, msg_id = res

        msg = await DouyinMessage.objects.aget(id=msg_id)
        self.assertEqual(msg.content_type, "text")
        self.assertEqual(msg.content, "[表情]")

    async def test_upsert_video_message(self):
        res = await _upsert_conversation_and_message(
            account_id=self.account.id,
            peer_sec_uid=self.peer_sec_uid,
            peer_nickname="PeerUser",
            text="[视频]",
            received_at=self.received_at,
            raw={
                "source": "test",
                "content_json": '{"video": {"url": "https://example.com/video.mp4"}}'
            },
            external_msg_id="ext_msg_4",
            direction="in"
        )
        self.assertIsNotNone(res)
        _, msg_id = res

        msg = await DouyinMessage.objects.aget(id=msg_id)
        self.assertEqual(msg.content_type, "text")
        self.assertEqual(msg.content, "[视频]")

    async def test_upsert_real_world_emoji_message(self):
        res = await _upsert_conversation_and_message(
            account_id=self.account.id,
            peer_sec_uid=self.peer_sec_uid,
            peer_nickname="PeerUser",
            text="[表情]",
            received_at=self.received_at,
            raw={
                "source": "test",
                "content_json": '{"display_name":"早上好","emoji_type":"lite_emoji","url":{"url_list":["https://example.com/real_sticker.png"]}}'
            },
            external_msg_id="ext_msg_5",
            direction="in"
        )
        self.assertIsNotNone(res)
        _, msg_id = res

        msg = await DouyinMessage.objects.aget(id=msg_id)
        self.assertEqual(msg.content_type, "text")
        self.assertEqual(msg.content, "[表情]")
