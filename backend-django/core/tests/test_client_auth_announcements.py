from datetime import timedelta

from django.test import Client, TestCase, override_settings
from django.utils import timezone

from core.client_announcement.client_announcement_model import ClientAnnouncement


@override_settings(ROOT_URLCONF="application.urls")
class ClientAuthAnnouncementTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_control_plane_returns_only_current_published_announcements(self):
        now = timezone.now()
        current = ClientAnnouncement.objects.create(
            title="当前公告",
            content="第一行\n第二行",
            level="warning",
            status="published",
            publish_time=now,
        )
        ClientAnnouncement.objects.create(
            title="草稿",
            content="hidden",
            status="draft",
            publish_time=now,
        )
        ClientAnnouncement.objects.create(
            title="已过期",
            content="hidden",
            status="published",
            publish_time=now - timedelta(days=2),
            expire_time=now - timedelta(days=1),
        )

        response = self.client.get("/api/client-auth/announcements?limit=10")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["id"], current.id)
        self.assertEqual(payload[0]["level"], "warning")
        self.assertEqual(payload[0]["content"], "第一行\n第二行")

    def test_control_plane_rejects_out_of_range_limit(self):
        for limit in (0, 51):
            with self.subTest(limit=limit):
                response = self.client.get(f"/api/client-auth/announcements?limit={limit}")
                self.assertEqual(response.status_code, 400)

    @override_settings(
        DOWNLOAD_LATEST_VERSION="0.1.25",
        DOWNLOAD_EXTENSION_VERSION="2.2.0",
        DOWNLOAD_EXTENSION_URL="https://downloads.example.com/douyin-cred-extractor.zip",
        DOWNLOAD_EXTENSION_FILE="douyin-cred-extractor.zip",
    )
    def test_app_version_exposes_latest_browser_extension_asset(self):
        response = self.client.get("/api/client-auth/app-version")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["extension_version"], "2.2.0")
        self.assertEqual(
            payload["extension_url"],
            "https://downloads.example.com/douyin-cred-extractor.zip",
        )
        self.assertEqual(payload["extension_file"], "douyin-cred-extractor.zip")
