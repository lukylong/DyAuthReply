import base64
import json

from django.test import SimpleTestCase

from core.douyin.runtime.credential import (
    has_send_credential,
    merge_storage_state,
    parse_bd_ticket_from_cookie,
    parse_credential_bundle,
    parse_ticket_guard_server_data,
)


def _make_bundle(payload: dict) -> str:
    raw = json.dumps(payload)
    b64 = base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")
    return f"DYCRED1.{b64}"


class DouyinCredentialBundleTests(SimpleTestCase):
    def test_parse_login_response_server_data(self):
        payload = {
            "ticket": "ticket-new",
            "ts_sign": "sign-new",
            "client_cert": "cert-new",
            "create_time": 1234567890,
        }
        raw = base64.urlsafe_b64encode(
            json.dumps(payload).encode("utf-8")
        ).decode("ascii").rstrip("=")

        self.assertEqual(
            parse_ticket_guard_server_data(raw),
            {
                "ticket": "ticket-new",
                "ts_sign": "sign-new",
                "client_cert": "cert-new",
                "create_time": "1234567890",
            },
        )

    def test_cookie_server_data_has_priority_over_client_v2(self):
        server_data = base64.b64encode(
            json.dumps({
                "ticket": "ticket-cookie",
                "ts_sign": "server-sign",
                "client_cert": "cert-cookie",
            }).encode("utf-8")
        ).decode("ascii")
        client_v2 = base64.urlsafe_b64encode(
            b'{"ts_sign":"client-sign","ree_public_key":"ree"}'
        ).decode("ascii").rstrip("=")

        out = parse_bd_ticket_from_cookie({
            "BD_TICKET_GUARD_SERVER_DATA": server_data,
            "bd_ticket_guard_client_data_v2": client_v2,
        })

        self.assertEqual(out["ticket"], "ticket-cookie")
        self.assertEqual(out["ts_sign"], "server-sign")
        self.assertEqual(out["client_cert"], "cert-cookie")
        self.assertEqual(out["ree_public_key"], "ree")

    def test_bundle_accepts_server_data_alias(self):
        out = parse_credential_bundle(json.dumps({
            "cookie": "sessionid=1",
            "ticket_guard_server_data": "server-data-value",
        }))
        self.assertEqual(out["web_protect"], "server-data-value")

    def test_new_bundle_builds_complete_send_credential(self):
        server_data = base64.b64encode(json.dumps({
            "ticket": "ticket",
            "ts_sign": "ts-sign",
            "client_cert": "client-cert",
            "create_time": 123,
        }).encode("utf-8")).decode("ascii")
        unpacked = parse_credential_bundle(json.dumps({
            "cookie": f"sessionid=sid; bd_ticket_guard_server_data={server_data}",
            "ticket_guard_server_data": server_data,
            "keys": json.dumps({"ec_privateKey": "private-key"}),
        }))

        state = merge_storage_state(
            {},
            unpacked["cookie"],
            web_protect=unpacked["web_protect"],
            keys=unpacked["keys"],
        )

        self.assertTrue(has_send_credential(state))
        self.assertEqual(state["_bd_ticket"]["client_cert"], "client-cert")
        self.assertEqual(state["_bd_ticket"]["create_time"], "123")

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

    def test_merge_storage_state_updates_create_time_when_ts_sign_changes(self):
        import time

        base_state = {
            "cookies": [
                {"name": "sessionid", "value": "old_session", "domain": ".douyin.com", "path": "/"}
            ],
            "_bd_ticket": {
                "ts_sign": "old_sign",
                "create_time": "12345678"
            }
        }

        cookie_data = base64.urlsafe_b64encode(b'{"ts_sign": "new_sign"}').decode("ascii").rstrip("=")
        new_cookie_str = f"sessionid=new_session; bd_ticket_guard_client_data_v2={cookie_data}"

        now = int(time.time())
        merged = merge_storage_state(base_state, new_cookie_str)

        bd = merged.get("_bd_ticket", {})
        self.assertEqual(bd.get("ts_sign"), "new_sign")
        ct = int(bd.get("create_time", 0))
        self.assertTrue(now - 5 <= ct <= now + 5)

    def test_merge_storage_state_keeps_create_time_when_ts_sign_unchanged(self):
        cookie_data = base64.urlsafe_b64encode(b'{"ts_sign": "same_sign"}').decode("ascii").rstrip("=")
        base_state = {
            "cookies": [
                {"name": "sessionid", "value": "old_session", "domain": ".douyin.com", "path": "/"}
            ],
            "_bd_ticket": {
                "ts_sign": "same_sign",
                "create_time": "12345678"
            }
        }
        new_cookie_str = f"sessionid=new_session; bd_ticket_guard_client_data_v2={cookie_data}"

        merged = merge_storage_state(base_state, new_cookie_str)

        bd = merged.get("_bd_ticket", {})
        self.assertEqual(bd.get("create_time"), "12345678")

    def test_account_switch_drops_old_signing_material(self):
        base_state = {
            "cookies": [
                {"name": "sessionid", "value": "old", "domain": ".douyin.com", "path": "/"}
            ],
            "_bd_ticket": {
                "ticket": "old-ticket",
                "ts_sign": "old-sign",
                "client_cert": "old-cert",
                "private_key": "old-private-key",
            },
        }
        server_data = base64.b64encode(json.dumps({
            "ticket": "new-ticket",
            "ts_sign": "new-sign",
            "client_cert": "new-cert",
            "create_time": 222,
        }).encode("utf-8")).decode("ascii")

        merged = merge_storage_state(
            base_state,
            f"sessionid=new; bd_ticket_guard_server_data={server_data}",
        )

        self.assertEqual(merged["_bd_ticket"]["ticket"], "new-ticket")
        self.assertEqual(merged["_bd_ticket"]["create_time"], "222")
        self.assertNotIn("private_key", merged["_bd_ticket"])
