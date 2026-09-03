from types import SimpleNamespace
from unittest.mock import patch

from django.test import Client, TestCase, override_settings

from core.douyin.runtime.health import (
    _audit_ticket_reimport_required,
    run_ticket_autorenew,
)


class TicketGuardMigrationTests(TestCase):
    def test_openapi_schema_resolves_credential_types(self):
        response = Client().get("/api/openapi.json")

        self.assertEqual(response.status_code, 200)
        schema = response.json()
        field = schema["components"]["schemas"]["DouyinCredentialImportIn"][
            "properties"
        ]["web_protect"]
        self.assertIn("server_data", field["description"])

    @override_settings(DOUYIN_TICKET_AUTORENEW_ENABLED=False)
    def test_legacy_scheduler_entry_is_safe_when_disabled(self):
        result = run_ticket_autorenew()

        self.assertTrue(result["success"])
        self.assertEqual(result["strategy"], "login_response_reimport")
        self.assertEqual(result["renewed"], 0)
        self.assertEqual(result["reimport_required"], 0)

    @override_settings(DOUYIN_TICKET_REFRESH_AGE_HOURS=18)
    @patch("core.douyin.runtime.health._maybe_warn_ticket_aging")
    @patch("core.douyin.runtime.health._bd_ticket_create_time")
    @patch("core.douyin.runtime.health._load_online_accounts")
    @patch("core.douyin.runtime.health.time.time", return_value=200_000)
    def test_enabled_entry_only_audits_age(
        self,
        _now,
        load_accounts,
        create_time,
        warn,
    ):
        load_accounts.return_value = [SimpleNamespace(id="fresh"), SimpleNamespace(id="old")]
        create_time.side_effect = [199_000, 100_000]

        result = _audit_ticket_reimport_required()

        self.assertEqual(result, {
            "success": True,
            "strategy": "login_response_reimport",
            "checked": 2,
            "renewed": 0,
            "reimport_required": 1,
        })
        warn.assert_called_once_with("old")
