#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
客户端公告模块功能测试
测试创建、发布公告的完整流程
"""
import os
import sys
import django

# 设置 Django 环境
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'application.settings')
django.setup()

from datetime import datetime, timedelta
from core.client_announcement.client_announcement_model import ClientAnnouncement
from core.user.user_model import User


def test_create_announcement():
    """测试创建公告"""
    print("\n=== 测试 1: 创建公告 ===")

    # 获取管理员用户
    admin = User.objects.filter(username='superadmin').first()
    if not admin:
        print("❌ 未找到 superadmin 用户")
        return None

    # 创建测试公告
    announcement = ClientAnnouncement.objects.create(
        title="测试公告 - 版本更新通知",
        content="DyAuthReply 客户端已发布 v0.1.13 版本，包含以下更新：\n1. 新增客户端公告功能\n2. 新增版本更新检测\n3. 优化用户体验",
        level="info",
        status="draft",
        publish_time=datetime.now(),
        expire_time=datetime.now() + timedelta(days=7),
        target_version=">=0.1.10",
        sys_creator=admin,
    )

    print(f"✓ 公告创建成功")
    print(f"  ID: {announcement.id}")
    print(f"  标题: {announcement.title}")
    print(f"  级别: {announcement.level}")
    print(f"  状态: {announcement.status}")

    return announcement


def test_publish_announcement(announcement):
    """测试发布公告"""
    print("\n=== 测试 2: 发布公告 ===")

    if not announcement:
        print("❌ 公告对象为空，跳过测试")
        return

    # 更新状态为已发布
    announcement.status = "published"
    announcement.publish_time = datetime.now()
    announcement.save()

    print(f"✓ 公告发布成功")
    print(f"  状态: {announcement.status}")
    print(f"  发布时间: {announcement.publish_time}")


def test_query_announcements():
    """测试查询公告"""
    print("\n=== 测试 3: 查询公告列表 ===")

    # 查询所有已发布的公告
    announcements = ClientAnnouncement.objects.filter(
        is_deleted=False,
        status="published"
    ).order_by('-publish_time')

    print(f"✓ 查询成功，共 {announcements.count()} 条已发布公告")

    for ann in announcements[:5]:
        print(f"  - [{ann.level}] {ann.title} (发布于: {ann.publish_time})")


def test_client_api():
    """测试客户端 API（模拟）"""
    print("\n=== 测试 4: 客户端 API ===")

    from datetime import datetime
    from django.utils import timezone

    now = timezone.now()

    # 模拟客户端查询公告
    announcements = ClientAnnouncement.objects.filter(
        is_deleted=False,
        status='published',
    ).filter(
        publish_time__lte=now
    ).order_by('-publish_time')[:10]

    # 过滤未过期的
    valid_announcements = [
        ann for ann in announcements
        if ann.expire_time is None or ann.expire_time > now
    ]

    print(f"✓ 客户端可见公告: {len(valid_announcements)} 条")

    for ann in valid_announcements:
        print(f"  - [{ann.level}] {ann.title}")
        print(f"    内容: {ann.content[:50]}...")
        print(f"    目标版本: {ann.target_version or '所有版本'}")


def cleanup_test_data():
    """清理测试数据"""
    print("\n=== 清理测试数据 ===")

    deleted = ClientAnnouncement.objects.filter(
        title__startswith="测试公告"
    ).delete()

    print(f"✓ 已删除 {deleted[0]} 条测试公告")


def main():
    """主函数"""
    print("=" * 60)
    print("客户端公告模块功能测试")
    print("=" * 60)

    try:
        # 1. 创建公告
        announcement = test_create_announcement()

        # 2. 发布公告
        test_publish_announcement(announcement)

        # 3. 查询公告
        test_query_announcements()

        # 4. 测试客户端 API
        test_client_api()

        print("\n" + "=" * 60)
        print("✓ 所有测试通过")
        print("=" * 60)

        # 询问是否清理测试数据
        cleanup = input("\n是否清理测试数据？(y/n): ").strip().lower()
        if cleanup == 'y':
            cleanup_test_data()

    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()


if __name__ == '__main__':
    main()
