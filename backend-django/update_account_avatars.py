#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
临时脚本：批量更新账号头像和抖音号
从抖音 API 拉取账号资料并更新到数据库
"""
import os
import sys
import django

# 设置 Django 环境
sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'application.settings')
django.setup()

import asyncio
from core.douyin.douyin_account_model import DouyinAccount
from core.douyin.runtime.transport.http_protocol import HttpProtocolTransport


async def update_account_avatar(account):
    """更新单个账号的头像和抖音号"""
    print(f"\n处理账号: {account.nickname} (ID: {account.id})")

    transport = HttpProtocolTransport()
    try:
        await transport.start(account)

        # 尝试获取账号资料
        profile = await transport.fetch_self_profile(account)

        if profile:
            updated_fields = []

            # 更新头像
            if profile.get('avatar'):
                account.avatar = profile['avatar']
                updated_fields.append('avatar')
                print(f"  ✓ 头像: {profile['avatar'][:60]}...")

            # 更新抖音号
            if profile.get('unique_id'):
                account.unique_id = profile['unique_id']
                updated_fields.append('unique_id')
                print(f"  ✓ 抖音号: {profile['unique_id']}")

            # 保存更新
            if updated_fields:
                updated_fields.append('sys_update_datetime')
                account.save(update_fields=updated_fields)
                print(f"  ✓ 已保存到数据库")
            else:
                print(f"  ⚠ 未获取到新数据")
        else:
            print(f"  ✗ 无法获取账号资料（可能未登录或凭证失效）")

    except Exception as e:
        print(f"  ✗ 错误: {e}")

    finally:
        await transport.stop(account)


async def main():
    """主函数"""
    # 获取所有在线账号
    accounts = DouyinAccount.objects.filter(status=1).order_by('nickname')

    if not accounts.exists():
        print("没有找到在线账号")
        return

    print(f"找到 {accounts.count()} 个在线账号")
    print("=" * 60)

    # 逐个更新
    for account in accounts:
        await update_account_avatar(account)

    print("\n" + "=" * 60)
    print("✓ 全部处理完成")


if __name__ == '__main__':
    asyncio.run(main())
