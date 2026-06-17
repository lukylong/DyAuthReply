#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""DyAuthReply 桌面客户端 — API 服务入口。"""
from __future__ import annotations

import os
import sys


def main() -> None:
    os.environ.setdefault('ZQ_ENV', 'client')
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'application.settings')

    import django

    django.setup()

    from django.conf import settings
    import shutil
    import traceback

    db_conf = settings.DATABASES.get('default', {})
    is_sqlite = db_conf.get('ENGINE') == 'django.db.backends.sqlite3'
    db_path = db_conf.get('NAME') if is_sqlite else None
    backup_path = f"{db_path}.bak" if db_path else None

    # Step 1: Create backup of SQLite DB if it exists
    if db_path and os.path.exists(db_path):
        try:
            print(f"[start_client] Creating automatic database backup: {db_path} -> {backup_path}", flush=True)
            shutil.copy2(db_path, backup_path)
        except Exception as e:
            print(f"[start_client] Warning: Failed to create database backup: {e}", flush=True)

    # Step 2: Run migration inside safety try-except wrapper
    from django.core.management import call_command
    try:
        print("[start_client] Running Django database migrations...", flush=True)
        call_command('migrate', '--noinput')
        print("[start_client] Migrations completed successfully.", flush=True)
    except Exception as err:
        print(f"[start_client] ERROR: Database migration failed: {err}", file=sys.stderr, flush=True)
        traceback.print_exc()
        
        # Step 3: Rollback database to backup
        if db_path and backup_path and os.path.exists(backup_path):
            try:
                print(f"[start_client] Initiating rollback. Restoring backup database: {backup_path} -> {db_path}", file=sys.stderr, flush=True)
                if os.path.exists(db_path):
                    # Save the corrupted database for investigation
                    corrupt_path = f"{db_path}.corrupted"
                    shutil.move(db_path, corrupt_path)
                    print(f"[start_client] Corrupted database preserved at: {corrupt_path}", file=sys.stderr, flush=True)
                shutil.copy2(backup_path, db_path)
                print("[start_client] Rollback completed. Database restored to previous state.", file=sys.stderr, flush=True)
            except Exception as rollback_err:
                print(f"[start_client] CRITICAL: Rollback failed: {rollback_err}", file=sys.stderr, flush=True)
        
        # Step 4: Write crash log and terminate launcher
        try:
            crash_log_path = os.path.join(os.path.dirname(db_path) if db_path else os.getcwd(), "migration_error.log")
            with open(crash_log_path, "w", encoding="utf-8") as f:
                f.write(f"Migration Failed at {os.environ.get('CLIENT_HTTP_HOST', '127.0.0.1')}\n")
                f.write(f"Error: {err}\n\nTraceback:\n")
                traceback.print_exc(file=f)
            print(f"[start_client] Migration crash report written to: {crash_log_path}", file=sys.stderr, flush=True)
        except Exception:
            pass
        sys.exit(1)

    from core.client.bootstrap import get_or_create_local_user

    get_or_create_local_user()

    # Step 5: Check for UI updates in the background
    from core.client.ui_updater import check_for_ui_updates_async
    manifest_url = os.environ.get('CLIENT_UI_MANIFEST_URL') or 'https://releases.dyauthreply.com/ui/manifest.json'
    check_for_ui_updates_async(manifest_url)

    port = int(os.environ.get('CLIENT_HTTP_PORT', '8765'))
    host = os.environ.get('CLIENT_HTTP_HOST', '127.0.0.1')

    import uvicorn

    uvicorn.run(
        'application.asgi:application',
        host=host,
        port=port,
        log_level=os.environ.get('UVICORN_LOG_LEVEL', 'info'),
        reload=False,
    )


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
