#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: storage.py
@Desc: Storage State Manager - Playwright 登录态加密存储

功能：
  - 读写 storage_state.json（Playwright BrowserContext 的 cookie/localStorage 快照）
  - 使用 Fernet 对称加密，密钥来自 settings.DOUYIN_STORAGE_ENCRYPTION_KEY
  - 每个账号独立的 user_data_dir（浏览器指纹与缓存隔离）

目录布局（DOUYIN_DATA_DIR 由 settings 决定，默认 /var/lib/zq-platform/douyin）：
    {DATA_DIR}/
        contexts/
            {account_id}/      # Playwright user_data_dir，保存缓存、Service Worker
        storage/
            {account_id}.bin   # Fernet 加密后的 storage_state.json
"""
from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

logger = logging.getLogger(__name__)


def _data_dir() -> Path:
    """登录态数据根目录（若不存在会自动创建）。"""
    root = Path(getattr(settings, 'DOUYIN_DATA_DIR', '/var/lib/zq-platform/douyin'))
    root.mkdir(parents=True, exist_ok=True)
    (root / 'contexts').mkdir(exist_ok=True)
    (root / 'storage').mkdir(exist_ok=True)
    return root


def get_user_data_dir(account_id: str) -> Path:
    """返回指定账号的 Playwright user_data_dir（首次调用自动创建）"""
    d = _data_dir() / 'contexts' / str(account_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _storage_file(account_id: str) -> Path:
    return _data_dir() / 'storage' / f'{account_id}.bin'


def _fernet() -> Fernet:
    """
    读取 Fernet 密钥；若未配置则抛错提示用户在 .env 中生成。
    生成方法：python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    """
    key = getattr(settings, 'DOUYIN_STORAGE_ENCRYPTION_KEY', '')
    if not key:
        raise RuntimeError(
            "DOUYIN_STORAGE_ENCRYPTION_KEY 未配置，请在 .env 中设置 Fernet 密钥。\n"
            "生成方法：python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    if isinstance(key, str):
        key = key.encode('utf-8')
    return Fernet(key)


def save_storage_state(account_id: str, state: dict) -> str:
    """
    将 Playwright storage_state 字典加密后落盘。
    返回相对于 DOUYIN_DATA_DIR 的路径（用于写回 DouyinAccount.storage_state_path）。
    """
    blob = json.dumps(state, ensure_ascii=False).encode('utf-8')
    token = _fernet().encrypt(blob)
    path = _storage_file(account_id)
    path.write_bytes(token)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    rel = str(path.relative_to(_data_dir()))
    logger.info(f"[storage] 登录态已加密落盘: account={account_id} path={rel} size={len(token)}B")
    return rel


def load_storage_state(account_id: str) -> Optional[dict]:
    """
    读取并解密账号登录态。文件不存在 / 密钥错误 / 文件损坏均返回 None。
    """
    path = _storage_file(account_id)
    if not path.exists():
        return None
    try:
        token = path.read_bytes()
        blob = _fernet().decrypt(token)
        return json.loads(blob.decode('utf-8'))
    except InvalidToken:
        logger.warning(f"[storage] 登录态解密失败（密钥变更或文件损坏）: account={account_id}")
        return None
    except Exception as e:  # noqa: BLE001
        logger.error(f"[storage] 登录态读取异常: account={account_id} err={e}")
        return None


def delete_storage_state(account_id: str) -> None:
    """删除账号登录态（登出时调用）"""
    path = _storage_file(account_id)
    if path.exists():
        try:
            path.unlink()
            logger.info(f"[storage] 登录态已删除: account={account_id}")
        except OSError as e:
            logger.warning(f"[storage] 删除登录态失败: account={account_id} err={e}")


def delete_user_data_dir(account_id: str) -> None:
    """删除账号 Chromium 持久化 profile 目录，彻底清掉本地 cookie / localStorage / SW。"""
    path = _data_dir() / 'contexts' / str(account_id)
    if path.exists():
        try:
            shutil.rmtree(path)
            logger.info(f"[storage] 浏览器 profile 已删除: account={account_id} path={path}")
        except OSError as e:
            logger.warning(f"[storage] 删除浏览器 profile 失败: account={account_id} err={e}")


def delete_account_runtime_state(account_id: str) -> None:
    """删除账号所有持久化登录痕迹。"""
    delete_storage_state(account_id)
    delete_user_data_dir(account_id)
