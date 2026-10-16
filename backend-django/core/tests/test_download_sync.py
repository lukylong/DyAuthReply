import json
import tempfile
from pathlib import Path
from unittest import mock

import httpx
from django.test import Client, TestCase, override_settings

from core.license import download_sync

BASE = "https://mirror.test/dist"
FILES = {"mac.dmg": b"m" * 30, "win.exe": b"w" * 20, "ext.zip": b"e" * 10}


def transport(version="0.1.36", broken=None, wrong_size=None, calls=None):
    def handler(request: httpx.Request) -> httpx.Response:
        name = request.url.path.rsplit("/", 1)[-1]
        if calls is not None and request.method == "GET":
            calls.append(name)
        if name == "latest.json":
            return httpx.Response(200, json={"version": version, "pub_date": "2026-10-14T09:06:53Z"})
        if name == "client-release.json":
            return httpx.Response(200, json={"version": version, "notes": f"notes for {version}"})
        if name not in FILES or name == broken:
            return httpx.Response(404)
        body = FILES[name]
        if request.method == "HEAD":
            size = len(body) + (1 if name == wrong_size else 0)
            return httpx.Response(200, headers={"Content-Length": str(size)})
        return httpx.Response(200, content=body)

    return httpx.MockTransport(handler)


def patched(mock_transport):
    real = httpx.Client
    return mock.patch.object(
        download_sync.httpx, "Client", lambda **kw: real(transport=mock_transport, **kw)
    )


class DownloadSyncTests(TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.settings = override_settings(
            DOWNLOAD_LOCAL_DIR=self.dir.name,
            DOWNLOAD_PUBLIC_BASE_URL="https://pro.example.cn",
            DOWNLOAD_MACOS_FILE="mac.dmg",
            DOWNLOAD_WINDOWS_FILE="win.exe",
            DOWNLOAD_EXTENSION_FILE="ext.zip",
            DOWNLOAD_LATEST_VERSION="0.1.35",
            DOWNLOAD_RELEASE_NOTES="settings notes",
        )
        self.settings.enable()

    def tearDown(self):
        self.settings.disable()
        self.dir.cleanup()

    def version_api(self):
        return Client().get("/api/client-auth/app-version").json()

    def test_version_is_published_only_after_every_installer_is_in_place(self):
        self.assertEqual(self.version_api()["version"], "0.1.35")
        with patched(transport()):
            result = download_sync.sync_release(source_base=BASE)
        self.assertEqual(result["status"], "synced")
        for name, body in FILES.items():
            self.assertEqual((Path(self.dir.name) / name).read_bytes(), body)
        api = self.version_api()
        self.assertEqual((api["version"], api["notes"]), ("0.1.36", "notes for 0.1.36"))

    def test_a_failed_installer_keeps_the_previous_version_advertised(self):
        for kwargs in ({"broken": "win.exe"}, {"wrong_size": "mac.dmg"}):
            with patched(transport(**kwargs)), self.assertRaises(RuntimeError):
                download_sync.sync_release(source_base=BASE)
            self.assertIsNone(download_sync.local_release())
            self.assertEqual(self.version_api()["version"], "0.1.35")
        self.assertFalse(any(p.name.endswith(".part") for p in Path(self.dir.name).iterdir()))

    def test_current_release_is_not_downloaded_again(self):
        with patched(transport()):
            download_sync.sync_release(source_base=BASE)
        calls = []
        with patched(transport(calls=calls)):
            result = download_sync.sync_release(source_base=BASE)
        self.assertEqual(result["status"], "current")
        self.assertEqual(calls, ["latest.json"])
        marker = json.loads((Path(self.dir.name) / "release.json").read_text())
        self.assertEqual(marker["version"], "0.1.36")

    def test_landing_page_and_download_names_follow_the_synced_release(self):
        """Releasing must not need a server deploy: everything public shows the
        version whose installers are in place, not the one written in settings."""
        def landing_version():
            return Client().get("/").context["version"]

        def download_name(name):
            response = Client().get(f"/downloads/{name}")
            return response["Content-Disposition"]

        with patched(transport()):
            download_sync.sync_release(source_base=BASE)
        self.assertEqual(landing_version(), "0.1.36")
        self.assertIn('filename="win-v0.1.36.exe"', download_name("win.exe"))
        self.assertIn('filename="mac-v0.1.36.dmg"', download_name("mac.dmg"))

    def test_without_a_synced_release_settings_still_name_the_version(self):
        self.assertEqual(Client().get("/").context["version"], "0.1.35")
