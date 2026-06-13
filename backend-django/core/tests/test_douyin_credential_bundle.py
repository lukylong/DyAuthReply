import base64
import json

from django.test import SimpleTestCase

from core.douyin.runtime.credential import parse_credential_bundle


def _make_bundle(payload: dict) -> str:
    raw = json.dumps(payload)
    b64 = base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")
    return f"DYCRED1.{b64}"


class DouyinCredentialBundleTests(SimpleTestCase):
    def test_parse_prefixed_base64_bundle(self):
        bundle = _make_bundle(
            {
                "cookie": "sessionid=abc; ttwid=xyz",
                "web_protect": '{"ticket":"t"}',
                "keys": '{"ec_privateKey":"k"}',
                "ua": "Mozilla/5.0 Test",
            }
        )
        out = parse_credential_bundle(bundle)
        self.assertEqual(out["cookie"], "sessionid=abc; ttwid=xyz")
        self.assertEqual(out["web_protect"], '{"ticket":"t"}')
        self.assertEqual(out["keys"], '{"ec_privateKey":"k"}')
        self.assertEqual(out["user_agent"], "Mozilla/5.0 Test")

    def test_parse_plain_json_fallback(self):
        out = parse_credential_bundle(
            '{"cookie":"sessionid=1","user_agent":"UA"}'
        )
        self.assertEqual(out["cookie"], "sessionid=1")
        self.assertEqual(out["user_agent"], "UA")
        self.assertEqual(out["web_protect"], "")
        self.assertEqual(out["keys"], "")

    def test_utf8_payload_roundtrip(self):
        bundle = _make_bundle({"cookie": "nick=测试用户; sessionid=9"})
        out = parse_credential_bundle(bundle)
        self.assertEqual(out["cookie"], "nick=测试用户; sessionid=9")

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            parse_credential_bundle("")

    def test_garbage_raises(self):
        with self.assertRaises(ValueError):
            parse_credential_bundle("not-a-bundle-not-json")

    def test_non_object_raises(self):
        with self.assertRaises(ValueError):
            parse_credential_bundle("[1,2,3]")
