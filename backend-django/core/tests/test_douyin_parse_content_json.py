"""parse_content_json 单测：喂真实账号采样的 content_json 结构，
断言 content_type / 占位文本 / media 提取正确。

样本字段来自 .trellis/tasks/06-24-rich-media-inbound/research/content_json_field_map.md
"""
import json

from django.test import SimpleTestCase

from core.douyin.runtime.transport.wire.im_protocol_recv import parse_content_json


def _cj(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


class ParseContentJsonTests(SimpleTestCase):
    def test_text(self):
        r = parse_content_json(_cj({"aweType": 0, "text": "你好", "richTextInfos": []}))
        self.assertEqual(r.content_type, "text")
        self.assertEqual(r.text, "你好")
        self.assertEqual(r.media, {})

    def test_empty_and_invalid(self):
        self.assertEqual(parse_content_json("").content_type, "text")
        self.assertEqual(parse_content_json("not json").text, "")
        self.assertEqual(parse_content_json("[]").text, "")

    def test_system_read_receipt(self):
        r = parse_content_json(_cj({"command_type": 1, "read_index": 123}))
        self.assertEqual(r.content_type, "system")
        self.assertEqual(r.text, "")

    def test_image(self):
        obj = {
            "resource_url": {
                "origin_url_list": ["https://p.douyinpic.com/origin.image"],
                "medium_url_list": ["https://p.douyinpic.com/medium.image"],
            },
            "cover_width": 1080,
            "cover_height": 1920,
            "md5": "abc",
        }
        r = parse_content_json(_cj(obj))
        self.assertEqual(r.content_type, "image")
        self.assertEqual(r.text, "[图片]")
        self.assertEqual(r.media["kind"], "image")
        self.assertEqual(r.media["url"], "https://p.douyinpic.com/origin.image")
        self.assertEqual(r.media["width"], 1080)

    def test_voice(self):
        obj = {
            "resource_url": {"url_list": ["https://sf.douyin.com/voice.mp3"]},
            "duration": 2600,
            "voice_wave": [1, 2, 3],
            "ai_audio_text": "你好呀",
        }
        r = parse_content_json(_cj(obj))
        self.assertEqual(r.content_type, "other")
        self.assertEqual(r.text, "[语音]")
        self.assertEqual(r.media["kind"], "voice")
        self.assertEqual(r.media["url"], "https://sf.douyin.com/voice.mp3")
        self.assertEqual(r.media["duration_ms"], 2600)
        self.assertEqual(r.media["ai_text"], "你好呀")

    def test_video_file(self):
        obj = {
            "aweType": 0,
            "duration": "1.333333",
            "width": 1080,
            "height": 1920,
            "video": {"vid": "v0203dg10000", "is_new_encrypt": 1},
            "poster": {
                "origin_url_list": ["https://p.douyinpic.com/cover.image"],
                "medium_url_list": ["https://p.douyinpic.com/cover-m.image"],
            },
        }
        r = parse_content_json(_cj(obj))
        self.assertEqual(r.content_type, "video")
        self.assertEqual(r.text, "[视频]")
        self.assertEqual(r.media["kind"], "video")
        self.assertEqual(r.media["cover_url"], "https://p.douyinpic.com/cover.image")
        self.assertEqual(r.media["vid"], "v0203dg10000")
        self.assertEqual(r.media["duration_s"], "1.333333")

    def test_emoji_sticker(self):
        obj = {
            "display_name": "[比心]",
            "emoji_type": "lite_emoji",
            "sticker_type": 1,
            "url": {"url_list": ["https://p.douyinpic.com/sticker.png"]},
            "width": 200,
            "height": 200,
        }
        r = parse_content_json(_cj(obj))
        self.assertEqual(r.content_type, "other")
        self.assertEqual(r.text, "[比心]")
        self.assertEqual(r.media["kind"], "emoji")
        self.assertEqual(r.media["url"], "https://p.douyinpic.com/sticker.png")

    def test_share_video_card(self):
        r = parse_content_json(_cj({"itemId": "7654321", "title": "分享的视频"}))
        self.assertEqual(r.content_type, "card")
        self.assertEqual(r.text, "[分享视频]")
        self.assertEqual(r.media["kind"], "share_video")
        self.assertEqual(r.media["item_id"], "7654321")

    def test_display_name_as_text_fallback(self):
        # 无 url/emoji 标记，display_name 当文本
        r = parse_content_json(_cj({"display_name": "早上好"}))
        self.assertEqual(r.content_type, "text")
        self.assertEqual(r.text, "早上好")

    def test_unknown_returns_empty_text(self):
        r = parse_content_json(_cj({"some_unknown_field": 1}))
        self.assertEqual(r.text, "")
