"""Real Django HTTP -> Rust verifier -> CoreStore -> release, isolated synthetic tenant."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from uuid import uuid4
from wsgiref.simple_server import make_server, WSGIRequestHandler

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend-django'))


def main():
    with tempfile.TemporaryDirectory(prefix='dy-hosted-lease-e2e-') as temp:
        temp = Path(temp)
        os.environ['ZQ_ENV'] = 'client'
        os.environ['CLIENT_DATA_DIR'] = str(temp / 'django')
        os.environ['DJANGO_SETTINGS_MODULE'] = 'application.settings'
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives import serialization
        key = Ed25519PrivateKey.generate()
        os.environ['LICENSE_LEASE_PRIVATE_KEY'] = key.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
        public = key.public_key().public_bytes(serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        import django
        django.setup()
        from django.conf import settings
        from django.core.management import call_command
        from django.core.wsgi import get_wsgi_application
        from core.license.license_model import LicensePlan, LicenseKey, AgentAccountLease
        from core.license.license_service import activate_license, generate_license_code, hash_license_code
        # Exercise the actual hosted URL mount, API authentication overrides,
        # renderer and complete middleware stack, using an isolated database.
        settings.ROOT_URLCONF = 'application.urls'
        settings.ALLOWED_HOSTS = ['127.0.0.1']
        call_command('migrate', interactive=False, verbosity=0)
        plan = LicensePlan.objects.create(code='e2e', name='Synthetic E2E', valid_days=1,
            feature_flags={'auto_reply': True})
        code = generate_license_code()
        LicenseKey.objects.create(plan=plan, code_hash=hash_license_code(code), masked_code='SYNTHETIC')
        activation = activate_license(license_code=code, device_fingerprint='synthetic-native-e2e')
        request = {'activation_id': activation['activation_id'], 'activation_token': activation['activation_token'],
            'instance_id': str(uuid4()), 'boot_id': str(uuid4()), 'request_id': str(uuid4()), 'sequence': 1,
            'accounts': [{'platform': 'douyin', 'platform_account_id': 'synthetic-platform-user',
                          'local_account_id': str(uuid4()), 'action': 'acquire', 'expected_epoch': 0}]}
        request_file = temp / 'request.json'
        fd = os.open(request_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'w') as f:
            json.dump(request, f)
        public_file = temp / 'public.pem'
        public_file.write_text(public)
        class QuietHandler(WSGIRequestHandler):
            def log_message(self, *args):
                pass
        server = make_server('127.0.0.1', 0, get_wsgi_application(), handler_class=QuietHandler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            if '--runtime' in sys.argv:
                from native_lease_runtime_case import run_runtime_case
                run_runtime_case(ROOT, temp, public, request, server.server_port)
                return
            command = [str(ROOT / 'dyauthreply-client/agent/target/debug/lease-probe'),
                '--server', f'http://127.0.0.1:{server.server_port}', '--public-key', str(public_file),
                '--request', str(request_file), '--data-dir', str(temp / 'native')]
            result = subprocess.run(command, capture_output=True, text=True, timeout=30)
            if result.returncode:
                raise RuntimeError(result.stderr)
            report = json.loads(result.stdout)
            assert report['verified_grants'] == report['installed_leases'] == report['released_leases'] == 1
            assert report['platform_messages_sent'] == 0
            assert AgentAccountLease.objects.get().released
            print(json.dumps(report, indent=2))
            print('DJANGO_HTTP_RUST_CORESTORE_RELEASE_E2E_PASS')
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()
            assert not thread.is_alive()


if __name__ == '__main__':
    main()
