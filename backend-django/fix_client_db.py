#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
修复客户端数据库迁移冲突问题
"""
import os
import sys
from pathlib import Path

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'application.settings')
os.environ.setdefault('ZQ_ENV', 'client')

import django
django.setup()

from django.db import connection

def fix_worker_command_migration():
    """修复 DouyinWorkerCommand 迁移问题"""
    with connection.cursor() as cursor:
        # 检查表是否存在
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='core_douyin_worker_command'
        """)
        if not cursor.fetchone():
            print("[fix_db] core_douyin_worker_command 表不存在，无需修复")
            return

        # 获取表的字段列表
        cursor.execute("PRAGMA table_info(core_douyin_worker_command)")
        columns = {row[1] for row in cursor.fetchall()}

        # 检查是否已有 sys_creator_id（0021 创建时就带了）
        has_root_fields = 'sys_creator_id' in columns

        # 检查迁移记录
        cursor.execute("""
            SELECT id FROM django_migrations
            WHERE app='core' AND name='0022_douyin_worker_command_root_fields'
        """)
        migration_applied = cursor.fetchone() is not None

        if has_root_fields and migration_applied:
            print("[fix_db] ✓ core_douyin_worker_command 字段完整，迁移已记录，无需修复")
            return

        if has_root_fields and not migration_applied:
            # 字段已存在，但迁移未记录 → 补记录
            print("[fix_db] 字段已存在，补记录迁移 0022...")
            cursor.execute("""
                INSERT INTO django_migrations (app, name, applied)
                VALUES ('core', '0022_douyin_worker_command_root_fields', datetime('now'))
            """)
            print("[fix_db] ✓ 已补记录迁移 0022")
            return

        if not has_root_fields and migration_applied:
            # 迁移已记录，但字段不存在 → 删除记录，让迁移重新跑
            print("[fix_db] 迁移已记录但字段缺失，删除迁移记录...")
            cursor.execute("""
                DELETE FROM django_migrations
                WHERE app='core' AND name='0022_douyin_worker_command_root_fields'
            """)
            print("[fix_db] ✓ 已删除迁移记录，启动时会自动重新应用")
            return

def main():
    print("[fix_db] 开始检查数据库...")
    try:
        fix_worker_command_migration()
        print("[fix_db] ✓ 数据库检查完成")
    except Exception as e:
        print(f"[fix_db] ✗ 修复失败: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1
    return 0

if __name__ == '__main__':
    sys.exit(main())
