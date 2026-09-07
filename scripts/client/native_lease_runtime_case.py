"""Actual dy-agent serve lifecycle against the isolated Django authority."""
import json
from contextlib import closing
import os
import socket
import sqlite3
import subprocess
import time
import urllib.request
from uuid import uuid4


def eventually(check, process, timeout=20):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        assert process.poll() is None, 'native service exited prematurely'
        result = check()
        if result:
            return result
        time.sleep(0.1)
    raise AssertionError('runtime condition timed out')


def run_runtime_case(root, temp, public, request, server_port):
    from core.license.license_model import AgentAccountLease, LicenseActivation
    import core.license.agent_lease_service as authority
    from django.db import close_old_connections

    def run_case(name, short_ttl=False):
        accounts = [dict(request['accounts'][0], local_account_id=str(uuid4()), platform_account_id=f'synthetic-{name}')]
        config = {k: request[k] for k in ('activation_id', 'activation_token')}
        auth_file = temp / f'{name}-auth.json'
        auth = dict(activation_id=request['activation_id'], activation_token=request['activation_token'], server_url=f'http://127.0.0.1:{server_port}', local_state='active')
        with os.fdopen(os.open(auth_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as f:
            json.dump(auth, f)
        config.update(server=f'http://127.0.0.1:{server_port}', public_key_pem=public, accounts=accounts, auth_state_file=str(auth_file))
        config_file = temp / f'{name}.json'
        with os.fdopen(os.open(config_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as f:
            json.dump(config, f)
        native = temp / name
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        env = dict(os.environ, DY_AGENT_DATA_DIR=str(native), DY_AGENT_HOSTED_CONFIG=str(config_file),
                   DY_AGENT_BIND=f'127.0.0.1:{port}', RUST_LOG="dy_agent=info")
        log = temp / f'{name}.log'
        def local_row():
            path = native / 'core.sqlite3'
            if not path.exists():
                return None
            uri = f'file:{path}?mode=ro' + ('&immutable=1' if process.poll() is not None else '')
            with closing(sqlite3.connect(uri, uri=True)) as conn:
                try:
                    return conn.execute('SELECT fence_epoch,lease_until_ms,status FROM account_leases WHERE account_id=?',
                                        (accounts[0]['local_account_id'],)).fetchone()
                except sqlite3.OperationalError as error:
                    if "no such table" not in str(error):
                        raise
                    return None
        def remote_row():
            close_old_connections()
            return AgentAccountLease.objects.filter(platform_account_id=accounts[0]['platform_account_id']).first()
        with log.open('w') as output:
            process = subprocess.Popen([str(root / 'dyauthreply-client/agent/target/debug/dy-agent')],
                                       env=env, stdout=output, stderr=subprocess.STDOUT)
            try:
                first = eventually(lambda: (row if (row := local_row()) and row[2] == 'active' else None), process)
                with urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=3) as response:
                    health = json.load(response)
                assert health['runtime']['central_timer_tasks'] == 1
                assert health['protocol_execution']['account_worker'] == 'shadow_disabled'
                if not short_ttl:
                    renewed = eventually(lambda: (row if (row := local_row()) and row[1] > first[1] else None), process)
                    assert renewed[0] == first[0], 'renewal changed fence'
                    print('NATIVE_SERVE_RENEW_SAME_FENCE_PASS')
                    from core.license.license_service import hash_activation_token
                    rotated_token = uuid4().hex + uuid4().hex
                    LicenseActivation.objects.filter(pk=request['activation_id']).update(activation_token_hash=hash_activation_token(rotated_token))
                    auth['activation_token'] = rotated_token
                    with auth_file.open('w') as f:
                        json.dump(auth, f)
                    request['activation_token'] = rotated_token
                    rotated = eventually(lambda: (row if (row := local_row()) and row[1] > renewed[1] else None), process)
                    assert rotated[0] == first[0]
                    print('NATIVE_AUTH_TOKEN_ROTATION_RENEW_PASS')
                else:
                    # First short grant expires locally; controller must retire
                    # it, explicitly release remotely, then acquire a NEW epoch.
                    authority.TTL_SECONDS = 45
                    reacquired = eventually(lambda: (row if (row := local_row()) and row[0] > first[0] and row[2] == 'active' else None), process)
                    assert reacquired[0] > first[0]
                    print('NATIVE_SERVE_EXPIRY_RELEASE_REACQUIRE_PASS')
                    LicenseActivation.objects.filter(pk=request['activation_id']).update(status=LicenseActivation.STATUS_REVOKED)
                    eventually(lambda: (row if (row := local_row()) and row[2] == 'released' else None), process)
                    print('NATIVE_SERVE_REVOCATION_INVALIDATES_LOCAL_PASS')
                process.terminate()
                assert process.wait(timeout=20) == 0
                reopened = subprocess.run([str(root / 'dyauthreply-client/agent/target/debug/dy-agent'), '--check'], env=env, capture_output=True, text=True, timeout=15)
                assert reopened.returncode == 0, reopened.stderr
                assert local_row()[2] == 'released'
                if not short_ttl:
                    assert remote_row().released
                print('NATIVE_SERVE_SHUTDOWN_LOCAL_RELEASE_PASS')
            except Exception:
                print(log.read_text())
                raise
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=20)
                    except subprocess.TimeoutExpired:
                        process.kill(); process.wait(timeout=5)
    run_case('normal')
    authority.TTL_SECONDS = 8
    try:
        run_case('expiry', short_ttl=True)
    finally:
        authority.TTL_SECONDS = 45
    print('NATIVE_LEASE_RUNTIME_E2E_PASS; platform_messages_sent=0')
