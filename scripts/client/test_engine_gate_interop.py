#!/usr/bin/env python3
"""Actual Python/Rust process exclusion and sticky crash recovery, no platform I/O."""
from pathlib import Path
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[2]
BINARY = ROOT / 'dyauthreply-client/agent/target/debug/engine-gate'
ENV = dict(os.environ, PYTHONPATH=str(ROOT / 'backend-django'), PYTHONUTF8='1')
LEGACY = "from pathlib import Path;from core.client.engine_gate import LegacyEngineLease;import sys;g=LegacyEngineLease.acquire(Path(sys.argv[1]));print('LEGACY_ENGINE_OWNED',flush=True);sys.stdin.readline()"


def start(args, expected):
    process = subprocess.Popen(args, env=ENV, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True)
    line = queue.Queue()
    threading.Thread(target=lambda: line.put(process.stdout.readline()), daemon=True).start()
    try:
        assert line.get(timeout=10).strip() == expected
    except BaseException:
        process.kill(); process.communicate(timeout=5)
        raise
    return process


def fail(args, expected):
    result = subprocess.run(args, env=ENV, input='', capture_output=True, text=True, timeout=15)
    assert result.returncode != 0 and expected in result.stderr, result.stderr


def close(process):
    if process.poll() is None:
        process.communicate(input='stop\n', timeout=10)
    assert process.returncode == 0


def main():
    assert BINARY.is_file()
    with tempfile.TemporaryDirectory(prefix='dy-engine-process-') as directory:
        root = Path(directory) / 'client'; native = Path(directory) / 'native'
        legacy_args = [sys.executable, '-c', LEGACY, str(root)]
        native_args = [str(BINARY), str(root), str(native)]
        bad_env = dict(ENV, CLIENT_DATA_DIR=str(root), DY_AGENT_DATA_DIR=str(native),
                       DY_AGENT_MESSAGING_CONFIG=str(root/'missing-config.json'))
        bad = subprocess.run([str(ROOT/'dyauthreply-client/agent/target/debug/dy-agent')],
                             env=bad_env,input='',capture_output=True,text=True,timeout=15)
        assert bad.returncode != 0 and not (root/'message-engine.json').exists()
        legacy = start(legacy_args, 'LEGACY_ENGINE_OWNED')
        try:
            fail(native_args, 'another client engine')
            assert not (root / 'message-engine.json').exists()
        finally:
            close(legacy)
        engine = start(native_args, 'NATIVE_ENGINE_OWNED')
        try:
            fail(legacy_args, '已有本地消息引擎运行')
            fail([str(BINARY), str(root), str(native)+'-other'], 'another client engine')
            marker = (root / 'message-engine.json').read_bytes()
            assert json.loads(marker)['selected'] == 'rust'
            assert (root / 'launcher.lock').read_bytes() == b''
            # Updated launcher cannot race, truncate metadata or replace the live inode.
            launcher = "import sys;from pathlib import Path;sys.path.insert(0,sys.argv[1]);from instance_lock import acquire_instance_lock;assert acquire_instance_lock(Path(sys.argv[2])) is None;print('LAUNCHER_BLOCKED')"
            result = subprocess.run([sys.executable, '-c', launcher, str(ROOT/'dyauthreply-client/launcher'), str(root)], capture_output=True, text=True, timeout=10)
            assert result.returncode == 0 and 'LAUNCHER_BLOCKED' in result.stdout
            engine.kill(); engine.communicate(timeout=10)
        finally:
            if engine.poll() is None: engine.kill(); engine.communicate(timeout=10)
        fail(legacy_args, '已选择 Rust')
        fail([str(BINARY), str(root), str(native)+'-other'], 'bound to another data directory')
        assert (root / 'message-engine.json').read_bytes() == marker
        restarted = start(native_args, 'NATIVE_ENGINE_OWNED')
        close(restarted)
        fail(legacy_args, '已选择 Rust')
        # The real worker entry must reject BEFORE account/command loops, even after Rust exit.
        worker_env = dict(ENV, ZQ_ENV='client', CLIENT_DATA_DIR=str(root))
        result = subprocess.run([sys.executable, str(ROOT/'backend-django/start_douyin_worker.py')],
                                env=worker_env, cwd=ROOT/'backend-django', capture_output=True, text=True, timeout=25)
        assert result.returncode != 0 and '消息引擎已选择 Rust' in result.stdout + result.stderr, (result.stdout + result.stderr)[-1500:]
        assert not (root/'db.sqlite3').exists(), 'worker touched DB before engine exclusion'
        print('INVALID_CONFIGURATION_DOES_NOT_SELECT_ENGINE_PASS')
        print('PYTHON_RUST_MUTUAL_EXCLUSION_PASS')
        print('RUST_SIGKILL_STICKY_SELECTION_AND_RESTART_PASS')
        print('DIFFERENT_NATIVE_DATA_DIRECTORY_REJECTED_PASS')
        print('LEGACY_LAUNCHER_BLOCKED_PASS')
        print('REAL_WORKER_ENTRY_BLOCKED_BEFORE_ACCOUNT_IO_PASS')
        print('NEW_PLATFORM_SENDS=0')


if __name__ == '__main__':
    main()
