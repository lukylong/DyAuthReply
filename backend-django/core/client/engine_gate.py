"""Cross-engine local message ownership. No Django or protocol dependency.

The lock file is permanent. Rust selection is sticky even when its process is
stopped, because legacy and native reply histories must not be interchanged.
"""
from __future__ import annotations

import json
import os
import sys
from contextlib import nullcontext
from pathlib import Path

ENGINE_LOCK = 'message-engine.lock'
ENGINE_SELECTION = 'message-engine.json'


class EngineGateError(RuntimeError):
    pass


def _reject_link(path: Path) -> None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise EngineGateError('消息引擎元数据不是普通文件')


def read_selection(root: Path) -> dict | None:
    path = root / ENGINE_SELECTION
    _reject_link(path)
    try:
        with path.open('rb') as stream:
            raw = stream.read(4097)
    except FileNotFoundError:
        return None
    if len(raw) > 4096:
        raise EngineGateError('消息引擎选择记录超过大小限制')
    try:
        value = json.loads(raw)
        if (not isinstance(value, dict) or set(value) != {'version', 'selected', 'native_data_dir'}
                or type(value['version']) is not int or value['version'] != 1
                or value['selected'] != 'rust' or not isinstance(value['native_data_dir'], str)
                or not Path(value['native_data_dir']).is_absolute()):
            raise ValueError('unknown engine selection')
    except (ValueError, TypeError, KeyError) as exc:
        raise EngineGateError('消息引擎选择记录损坏或版本不兼容') from exc
    return value


class LegacyEngineLease:
    def __init__(self, stream):
        self._stream = stream

    @classmethod
    def acquire(cls, root: Path) -> 'LegacyEngineLease':
        root = root.resolve()
        root.mkdir(parents=True, exist_ok=True)
        path = root / ENGINE_LOCK
        _reject_link(path)
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        stream = os.fdopen(fd, 'r+b', buffering=0)
        try:
            if sys.platform == 'win32':
                import msvcrt
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            stream.close()
            raise EngineGateError('已有本地消息引擎运行，旧 Worker 停止启动') from exc
        try:
            if read_selection(root) is not None:
                raise EngineGateError('消息引擎已选择 Rust，旧 Worker 停止启动；引擎回退需先迁移回复记录')
        except BaseException:
            stream.close()
            raise
        return cls(stream)

    def close(self) -> None:
        if self._stream is not None:
            self._stream.close()
            self._stream = None

    def __enter__(self) -> 'LegacyEngineLease':
        return self

    def __exit__(self, *_args) -> None:
        self.close()


def acquire_worker_engine():
    """Only desktop/client mode takes this lock; remote Django workers stay unchanged."""
    if os.environ.get('ZQ_ENV') != 'client':
        return nullcontext()
    explicit = os.environ.get('CLIENT_DATA_DIR')
    if explicit:
        root = Path(explicit)
        if not root.is_absolute():
            raise EngineGateError('CLIENT_DATA_DIR 必须是绝对路径，避免消息引擎锁分散到不同目录')
    else:
        from env import CLIENT_DATA_DIR
        root = Path(CLIENT_DATA_DIR)
    return LegacyEngineLease.acquire(root)
