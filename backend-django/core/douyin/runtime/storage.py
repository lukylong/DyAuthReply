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


def update_bd_ticket(account_id: str, **fields: str) -> bool:
    """把若干 bd-ticket 字段增量写回 storage_state 的 _bd_ticket（仅覆盖非空值）。

    用于自动续期：刷新到新的 ts_sign / client_cert / ticket 后写回，private_key 等未提供的
    字段保持不变。state 不存在则不创建（必须先有登录态），返回是否写入成功。
    """
    state = load_storage_state(account_id)
    if not state or not isinstance(state, dict):
        logger.warning(f"[storage] update_bd_ticket 跳过：account={account_id} 无登录态")
        return False
    bd = dict(state.get("_bd_ticket") or {})
    changed = False
    for key, value in fields.items():
        if value:
            bd[key] = str(value)
            changed = True
    if not changed:
        return False
    state["_bd_ticket"] = bd
    save_storage_state(account_id, state)
    logger.info(
        f"[storage] bd-ticket 已续期写回: account={account_id} "
        f"fields={[k for k, v in fields.items() if v]}"
    )
    return True


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
    delete_scan_cursor(account_id)
    delete_conversation_scan_cursors(account_id)


# ---------------- scan_inbox cursor ----------------
# Phase 3: HTTP 协议 scan_inbox 走 get_by_user(cursor_us=...) 增量拉，需要把
# "上次拉到的最大 create_time_us" 持久化下来；否则 worker 重启时 cursor 会重置
# 为 0，第一轮就会把"最近 50 条 message"当成全新的入向消息。
#
# 不加密：cursor 只是 int64 时间戳，不敏感；保持简单更不容易出错（Fernet 解密
# 失败时不至于丢登录态）。
def _scan_cursor_file(account_id: str) -> Path:
    return _data_dir() / 'storage' / f'{account_id}.cursor.json'


def save_scan_cursor(account_id: str, cursor_us: int) -> None:
    """落盘账号最近一次 scan_inbox 见到的 create_time_us（微秒）。"""
    if cursor_us <= 0:
        return
    path = _scan_cursor_file(account_id)
    payload = {"cursor_us": int(cursor_us)}
    try:
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError as e:
        logger.warning(f"[storage] 写 scan_cursor 失败 account={account_id} err={e}")


def load_scan_cursor(account_id: str) -> int:
    """
    读取上次落盘的 cursor_us。

    返回 0 表示"没读到"——调用方应进入"基线模式"（首轮不分发，只记录）。
    """
    path = _scan_cursor_file(account_id)
    if not path.exists():
        return 0
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[storage] 读 scan_cursor 失败 account={account_id} err={e}")
        return 0
    if not isinstance(obj, dict):
        return 0
    val = obj.get("cursor_us")
    return int(val) if isinstance(val, (int, str)) and str(val).isdigit() else 0


def delete_scan_cursor(account_id: str) -> None:
    """登出 / 重置账号时连带清掉 cursor，避免新号继承旧号 cursor。"""
    path = _scan_cursor_file(account_id)
    if path.exists():
        try:
            path.unlink()
        except OSError:
            pass


# ---------------- conversation scan cursor ----------------
# Phase 4: 按 conversation_id 走 get_by_conversation(cursor=server_message_id)
# 增量拉取时，需要为每个会话单独持久化 cursor。
def _conversation_cursor_file(account_id: str) -> Path:
    return _data_dir() / 'storage' / f'{account_id}.conversation_cursors.json'


def load_conversation_scan_cursors(account_id: str) -> dict[str, int]:
    """读取会话级 cursor 映射。"""
    path = _conversation_cursor_file(account_id)
    if not path.exists():
        return {}
    try:
        obj = json.loads(path.read_text(encoding='utf-8'))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[storage] 读 conversation_cursor 失败 account={account_id} err={e}")
        return {}
    if not isinstance(obj, dict):
        return {}
    out: dict[str, int] = {}
    for key, val in obj.items():
        if not isinstance(key, str):
            continue
        try:
            num = int(val)
        except Exception:
            continue
        if num > 0:
            out[key] = num
    return out


def load_conversation_scan_cursor(account_id: str, conversation_id: str) -> int:
    """读取单个会话的 cursor；不存在返回 0。"""
    return load_conversation_scan_cursors(account_id).get(str(conversation_id), 0)


def save_conversation_scan_cursor(account_id: str, conversation_id: str, server_message_id: int) -> None:
    """落盘会话级 cursor。"""
    if not conversation_id or server_message_id <= 0:
        return
    path = _conversation_cursor_file(account_id)
    data = load_conversation_scan_cursors(account_id)
    data[str(conversation_id)] = int(server_message_id)
    try:
        path.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True), encoding='utf-8')
    except OSError as e:
        logger.warning(
            f"[storage] 写 conversation_cursor 失败 account={account_id} conv={conversation_id} err={e}"
        )


def delete_conversation_scan_cursors(account_id: str) -> None:
    """删除会话级 cursor 映射。"""
    path = _conversation_cursor_file(account_id)
    if path.exists():
        try:
            path.unlink()
        except OSError:
            pass
