"""Offline tests for local engine exclusion; no Django/settings/account database."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from core.client.engine_gate import (
    ENGINE_LOCK, ENGINE_SELECTION, EngineGateError, LegacyEngineLease, acquire_worker_engine,
)


class ClientEngineGateTests(unittest.TestCase):
    def test_legacy_is_exclusive_and_crash_safe_lock_file_is_not_deleted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with LegacyEngineLease.acquire(root):
                with self.assertRaises(EngineGateError):
                    LegacyEngineLease.acquire(root)
            self.assertTrue((root / ENGINE_LOCK).exists())
            with LegacyEngineLease.acquire(root):
                pass

    def test_sticky_native_selection_denies_old_worker_without_native_process(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ENGINE_SELECTION).write_text(json.dumps({
                'version': 1, 'selected': 'rust', 'native_data_dir': str(root / 'native'),
            }))
            with self.assertRaisesRegex(EngineGateError, '已选择 Rust'):
                LegacyEngineLease.acquire(root)
            with patch.dict(os.environ, {'ZQ_ENV': 'client', 'CLIENT_DATA_DIR': directory}):
                with self.assertRaisesRegex(EngineGateError, '已选择 Rust'):
                    acquire_worker_engine()

    def test_corrupt_future_and_oversized_markers_do_not_fall_back_to_legacy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for raw in ('broken', '{}', 'x' * 4097, '{"version":2}'):
                (root / ENGINE_SELECTION).write_text(raw)
                with self.assertRaises(EngineGateError):
                    LegacyEngineLease.acquire(root)

    def test_relative_environment_root_is_rejected(self):
        with patch.dict(os.environ, {'ZQ_ENV': 'client', 'CLIENT_DATA_DIR': './relative'}):
            with self.assertRaisesRegex(EngineGateError, '绝对路径'):
                acquire_worker_engine()

    def test_server_mode_does_not_take_client_gate(self):
        with patch.dict(os.environ, {'ZQ_ENV': 'prd'}):
            with acquire_worker_engine():
                pass

    def test_launcher_contender_does_not_truncate_pid_or_replace_inode(self):
        # tests/ -> core/ -> backend-django/ -> repository
        path = Path(__file__).resolve().parents[3] / 'dyauthreply-client/launcher/instance_lock.py'
        spec = importlib.util.spec_from_file_location('launcher_engine_gate_test', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            owner = module.acquire_instance_lock(root)
            self.assertIsNotNone(owner)
            try:
                lock = root / 'launcher.lock'
                before = lock.read_bytes()
                inode = lock.stat().st_ino
                self.assertIsNone(module.acquire_instance_lock(root))
                module._clear_stale_lock(lock)
                self.assertEqual(lock.read_bytes(), before)
                self.assertEqual(lock.stat().st_ino, inode)
            finally:
                owner.close()
