#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""桌面客户端 — UI 热更新引擎。"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import urllib.request
import zipfile
from pathlib import Path
import threading

logger = logging.getLogger(__name__)

# Resolved from environment settings or fallback
def get_client_data_dir() -> Path:
    from env import CLIENT_DATA_DIR
    return Path(CLIENT_DATA_DIR)

def get_ui_updates_dir() -> Path:
    d = get_client_data_dir() / 'ui_updates'
    d.mkdir(parents=True, exist_ok=True)
    return d

def get_active_ui_dist() -> str | None:
    """获取当前已解压的、有效的热更新 UI 目录。如果没有，返回 None。"""
    active_dir = get_ui_updates_dir() / 'active'
    index_html = active_dir / 'index.html'
    if active_dir.is_dir() and index_html.is_file():
        return str(active_dir.resolve())
    return None

def check_sha256(file_path: Path, expected_hash: str) -> bool:
    sha = hashlib.sha256()
    try:
        with open(file_path, 'rb') as f:
            while True:
                chunk = f.read(8192)
                if not chunk:
                    break
                sha.update(chunk)
        return sha.hexdigest().lower() == expected_hash.lower()
    except OSError:
        return False

def _download_and_extract_ui(url: str, expected_sha: str, version: str) -> bool:
    try:
        updates_dir = get_ui_updates_dir()
        download_dest = updates_dir / f"ui_v{version}.zip"
        extract_temp = updates_dir / f"temp_v{version}"
        active_dir = updates_dir / 'active'

        # 1. Download bundle
        logger.info(f"[ui_updater] Downloading UI bundle from: {url}")
        with urllib.request.urlopen(url, timeout=30) as response, open(download_dest, 'wb') as out_file:
            shutil.copyfileobj(response, out_file)
        logger.info("[ui_updater] Download completed.")

        # 2. Verify Checksum
        if not check_sha256(download_dest, expected_sha):
            logger.error("[ui_updater] Checksum verification failed. Corrupted file.")
            if download_dest.is_file():
                download_dest.unlink()
            return False

        # 3. Extract to temp directory
        if extract_temp.exists():
            shutil.rmtree(extract_temp)
        extract_temp.mkdir(parents=True)

        logger.info(f"[ui_updater] Extracting UI bundle to: {extract_temp}")
        with zipfile.ZipFile(download_dest, 'r') as zip_ref:
            zip_ref.extractall(extract_temp)

        # Confirm index.html exists in extracted files (could be nested)
        target_src = extract_temp
        if not (target_src / 'index.html').is_file():
            # Check for nested single directory (common in zip archives)
            subdirs = [x for x in extract_temp.iterdir() if x.is_dir()]
            if len(subdirs) == 1 and (subdirs[0] / 'index.html').is_file():
                target_src = subdirs[0]
            else:
                logger.error("[ui_updater] Extracted UI bundle has no index.html. Invalid structure.")
                shutil.rmtree(extract_temp)
                return False

        # 4. Atomic swap active directory
        backup_active = updates_dir / 'active_old'
        if backup_active.exists():
            shutil.rmtree(backup_active)

        if active_dir.exists():
            shutil.move(str(active_dir), str(backup_active))

        try:
            shutil.move(str(target_src), str(active_dir))
            logger.info(f"[ui_updater] UI successfully updated to version: {version}")
            # Write version info file
            (updates_dir / 'version.json').write_text(json.dumps({"version": version}))
            
            # Clean up old backup & downloaded zip
            if backup_active.exists():
                shutil.rmtree(backup_active)
            if download_dest.is_file():
                download_dest.unlink()
            if extract_temp.exists():
                shutil.rmtree(extract_temp)
            return True
        except Exception as e:
            logger.error(f"[ui_updater] Failed to swap active UI folder: {e}")
            # Restore backup
            if backup_active.exists():
                if active_dir.exists():
                    shutil.rmtree(active_dir)
                shutil.move(str(backup_active), str(active_dir))
            return False

    except Exception as e:
        logger.error(f"[ui_updater] Exception during UI hot update process: {e}")
        return False

def check_for_ui_updates_async(manifest_url: str) -> None:
    """在后台线程检查并下载 UI 热更新，避免阻塞 Django 启动。"""
    def run():
        try:
            logger.info(f"[ui_updater] Checking for UI updates at: {manifest_url}")
            req = urllib.request.Request(
                manifest_url, 
                headers={'User-Agent': 'Mozilla/5.0 DyAuthReply-Client'}
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                manifest = json.loads(response.read().decode('utf-8'))
            
            version = manifest.get('version')
            url = manifest.get('url')
            sha256 = manifest.get('sha256')

            if not version or not url or not sha256:
                logger.warning("[ui_updater] Invalid update manifest format.")
                return

            # Compare version
            current_ver = "0.0.0"
            version_file = get_ui_updates_dir() / 'version.json'
            if version_file.is_file():
                try:
                    current_ver = json.loads(version_file.read_text(encoding='utf-8')).get('version', '0.0.0')
                except Exception:
                    pass

            if version != current_ver:
                logger.info(f"[ui_updater] New UI version found: {version} (Current: {current_ver})")
                _download_and_extract_ui(url, sha256, version)
            else:
                logger.info("[ui_updater] UI is already up to date.")

        except Exception as e:
            logger.debug(f"[ui_updater] Failed to check for UI updates: {e}")

    threading.Thread(target=run, daemon=True).start()
