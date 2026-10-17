import json
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

from django.test import Client, TestCase
from django.utils import timezone

from core.license.diagnostic_model import ClientDiagnosticEvent, ClientDiagnosticState
from core.license.diagnostic_service import list_devices, purge_expired
from core.license.license_model import ClientDevice, LicenseActivation, LicenseKey, LicensePlan
from core.license.license_service import generate_license_code, hash_activation_token, hash_license_code

URL = "/api/client-auth/telemetry/ingest"


def client_time(delta=timedelta()):
    """Exactly what the agent sends: UTC RFC 3339 with an explicit offset."""
    return (datetime.now(dt_timezone.utc) + delta).isoformat()


class ClientDiagnosticsIngestTests(TestCase):
    def setUp(self):
        self.client = Client()
        plan = LicensePlan.objects.create(code="diag", name="诊断", max_devices=1, valid_days=30)
        key = LicenseKey.objects.create(
            plan=plan,
            code_hash=hash_license_code(generate_license_code()),
            masked_code="DIAG****CODE",
        )
        self.device = ClientDevice.objects.create(device_fingerprint="diag-device", device_name="Win11 Laptop")
        self.activation = LicenseActivation.objects.create(
            license_key=key,
            client_device=self.device,
            activation_token_hash=hash_activation_token("diag-token"),
            activated_at=timezone.now(),
        )

    def payload(self, **overrides):
        body = {
            "activation_id": str(self.activation.id),
            "activation_token": "diag-token",
            "app_version": "0.1.36",
            "os": "windows",
            "arch": "x86_64",
            "snapshot": {
                "runtime": {"phase": "running"},
                "accounts": [{"account": "a1b2c3", "inbound": "ws_healthy", "send": "sendable"}],
            },
            "events": [
                {
                    "at": client_time(),
                    "level": "WARN",
                    "target": "dy_agent::protocol::account_session",
                    "message": "platform request rejected",
                    "fields": {"step": "self_works", "status": 403, "text": "私信正文", "cookie": "sessionid=x"},
                },
                {"at": client_time(), "level": "DEBUG", "target": "t", "message": "dropped", "fields": {}},
                {
                    "at": client_time(-timedelta(days=40)),
                    "level": "ERROR",
                    "target": "t",
                    "message": "too old",
                    "fields": {},
                },
            ],
        }
        body.update(overrides)
        return body

    def post(self, body):
        return self.client.post(URL, data=json.dumps(body), content_type="application/json")

    def test_ingest_keeps_only_allow_listed_fields_and_valid_events(self):
        response = self.post(self.payload())
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["accepted"], 1)
        event = ClientDiagnosticEvent.objects.get(device=self.device)
        self.assertEqual(event.fields, {"step": "self_works", "status": 403})
        # Stored in the server's own time convention, within a minute of now.
        self.assertLess(abs((event.occurred_at - timezone.now()).total_seconds()), 60)
        self.assertEqual(event.app_version, "0.1.36")
        state = ClientDiagnosticState.objects.get(device=self.device)
        self.assertEqual((state.os, state.arch, state.report_count), ("windows", "x86_64", 1))
        self.assertEqual(state.snapshot["runtime"]["phase"], "running")

    def test_reports_are_rate_limited_per_device(self):
        self.assertEqual(self.post(self.payload()).status_code, 200)
        self.assertEqual(self.post(self.payload()).status_code, 429)

    def test_invalid_token_blocked_device_and_oversized_batches_are_rejected(self):
        self.assertEqual(self.post(self.payload(activation_token="wrong")).status_code, 401)
        too_many = [self.payload()["events"][0]] * 501
        self.assertEqual(self.post(self.payload(events=too_many)).status_code, 413)
        self.assertEqual(self.post(self.payload(snapshot={"blob": "x" * 70_000})).status_code, 413)
        ClientDevice.objects.filter(id=self.device.id).update(status=ClientDevice.STATUS_BLOCKED)
        self.assertEqual(self.post(self.payload()).status_code, 403)
        self.assertFalse(ClientDiagnosticEvent.objects.exists())

    def test_admin_listing_counts_recent_warnings_and_purge_respects_retention(self):
        self.post(self.payload())
        ClientDiagnosticEvent.objects.create(
            device=self.device,
            occurred_at=timezone.now() - timedelta(hours=2),
            level="ERROR",
            target="t",
            message="recent error",
        )
        stale = ClientDiagnosticEvent.objects.create(
            device=self.device, occurred_at=timezone.now(), level="WARN", target="t", message="stale"
        )
        ClientDiagnosticEvent.objects.filter(id=stale.id).update(occurred_at=timezone.now() - timedelta(days=31))
        rows = list_devices()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["warn_24h"], rows[0]["error_24h"]), (1, 1))
        self.assertEqual(rows[0]["runtime_phase"], "running")
        self.assertEqual(rows[0]["device_name"], "Win11 Laptop")
        self.assertEqual(purge_expired(), 1)
        self.assertEqual(list_devices(os="macos"), [])

    def test_device_summary_exposes_only_existing_operational_metrics(self):
        snapshot = {
            'runtime': {'phase': 'running', 'actor_count': 4, 'queue_depth': 2, 'queue_rejected': 7, 'open_circuit_count': 0, 'signer_failed': 1, 'not_public': 'ignored'},
            'storage': {'pressure': 'high'}, 'protocol_parity': True,
            'accounts': [{'account': 'anonymous', 'identity_verified': True, 'inbound': 'ws_healthy', 'send': 'unknown', 'nickname': 'not-public'}, 'malformed'],
        }
        self.assertEqual(self.post(self.payload(snapshot=snapshot)).status_code, 200)
        row = list_devices()[0]
        self.assertEqual(row['runtime']['queue_depth'], 2)
        self.assertNotIn('not_public', row['runtime'])
        self.assertEqual(row['storage_pressure'], 'high')
        self.assertTrue(row['protocol_parity'])
        self.assertEqual(len(row['accounts']), 1)
        self.assertNotIn('nickname', row['accounts'][0])

    def test_missing_summary_fields_are_unknown_not_zero_or_healthy(self):
        self.assertEqual(self.post(self.payload(snapshot={})).status_code, 200)
        row = list_devices()[0]
        self.assertIsNone(row['runtime']['queue_depth'])
        self.assertIsNone(row['runtime_phase'])
        self.assertIsNone(row['protocol_parity'])
        self.assertIsNone(row['storage_pressure'])
