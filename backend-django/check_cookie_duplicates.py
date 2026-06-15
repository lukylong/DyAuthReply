#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
诊断工具：检查抖音账号的 cookie 重复情况
"""
import os
import sys
import django

# 设置 Django 环境
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'application.settings')
django.setup()

from core.douyin.douyin_account_model import DouyinAccount
from core.douyin.runtime.storage import load_storage_state
from core.douyin.runtime.credential import session_fingerprint_from_state


def check_duplicate_cookies():
    """检查所有账号的 cookie 是否有重复"""
    print("=" * 80)
    print("抖音账号 Cookie 重复检查")
    print("=" * 80)

    accounts = DouyinAccount.objects.filter(is_deleted=False).exclude(status=3).order_by('sys_create_datetime')

    if not accounts.exists():
        print("\n未找到任何账号")
        return

    print(f"\n共找到 {accounts.count()} 个账号\n")

    # 收集所有账号的 sessionid
    session_map = {}  # sessionid -> [(account_id, nickname)]
    uid_map = {}      # uid_tt -> [(account_id, nickname)]

    for account in accounts:
        state = load_storage_state(str(account.id))
        sessionid, uid_tt = session_fingerprint_from_state(state)

        print(f"账号: {account.nickname} (ID: {str(account.id)[:8]}...)")
        print(f"  状态: {account.get_status_display()}")
        print(f"  凭证状态: {account.get_credential_state_display()}")
        print(f"  sessionid: {sessionid[:20] if sessionid else '(无)'}...")
        print(f"  uid_tt: {uid_tt[:20] if uid_tt else '(无)'}...")
        print(f"  存储路径: {account.storage_state_path or '(无)'}")
        print()

        if sessionid:
            if sessionid not in session_map:
                session_map[sessionid] = []
            session_map[sessionid].append((str(account.id), account.nickname))

        if uid_tt:
            if uid_tt not in uid_map:
                uid_map[uid_tt] = []
            uid_map[uid_tt].append((str(account.id), account.nickname))

    # 检查重复
    print("=" * 80)
    print("重复检测结果")
    print("=" * 80)

    has_duplicates = False

    # 检查 sessionid 重复
    print("\n【sessionid 重复检查】")
    for sessionid, accounts_list in session_map.items():
        if len(accounts_list) > 1:
            has_duplicates = True
            print(f"\n⚠️  发现重复的 sessionid: {sessionid[:20]}...")
            print("  以下账号共用相同的 sessionid（会导致互相顶号）：")
            for acc_id, nickname in accounts_list:
                print(f"    - {nickname} (ID: {acc_id[:8]}...)")

    if not has_duplicates:
        print("  ✓ 没有发现 sessionid 重复")

    # 检查 uid_tt 重复
    print("\n【uid_tt 重复检查】")
    has_uid_duplicates = False
    for uid_tt, accounts_list in uid_map.items():
        if len(accounts_list) > 1:
            has_uid_duplicates = True
            print(f"\n⚠️  发现重复的 uid_tt: {uid_tt[:20]}...")
            print("  以下账号共用相同的 uid_tt：")
            for acc_id, nickname in accounts_list:
                print(f"    - {nickname} (ID: {acc_id[:8]}...)")

    if not has_uid_duplicates:
        print("  ✓ 没有发现 uid_tt 重复")

    # 给出建议
    print("\n" + "=" * 80)
    print("诊断建议")
    print("=" * 80)

    if has_duplicates or has_uid_duplicates:
        print("\n发现了重复的登录态！这会导致以下问题：")
        print("  1. Worker 会自动去重，只托管其中一个账号")
        print("  2. 导入新 Cookie 时会被拦截（409 错误）")
        print("  3. 抖音服务器会认为是同一个登录会话")
        print("\n解决方案：")
        print("  1. 在浏览器中切换到不同的抖音账号（确认右上角头像）")
        print("  2. 从不同浏览器/无痕窗口导出 Cookie（每个账号独立登录）")
        print("  3. 删除重复的账号槽位，只保留一个")
        print("  4. 重新导入时确保浏览器登录的是对应的抖音账号")
    else:
        print("\n✓ Cookie 隔离正常，没有发现重复")
        print("\n当前配置：")
        print(f"  - 数据目录: {os.getenv('DOUYIN_DATA_DIR', '/var/lib/zq-platform/douyin')}")
        print(f"  - 加密密钥: {'已配置' if os.getenv('DOUYIN_STORAGE_ENCRYPTION_KEY') else '未配置'}")


if __name__ == '__main__':
    try:
        check_duplicate_cookies()
    except Exception as e:
        print(f"\n❌ 检查失败: {e}")
        import traceback
        traceback.print_exc()
