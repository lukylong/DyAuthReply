# -*- coding: utf-8 -*-
"""
添加客户端公告菜单
"""
from django.db import migrations


def add_client_announcement_menu(apps, schema_editor):
    """添加客户端公告菜单"""
    Menu = apps.get_model('core', 'Menu')

    # 获取消息管理父菜单
    try:
        parent_menu = Menu.objects.get(path='/message')
    except Menu.DoesNotExist:
        print("Warning: 消息管理父菜单不存在，跳过创建客户端公告菜单")
        return

    # 检查菜单是否已存在
    if Menu.objects.filter(path='/message/client-announcement').exists():
        print("客户端公告菜单已存在，跳过创建")
        return

    # 创建客户端公告菜单
    Menu.objects.create(
        name='ClientAnnouncement',
        title='客户端公告',
        path='/message/client-announcement',
        component='client-announcement/index',
        parent=parent_menu,
        type=1,  # 菜单类型
        icon='Bell',
        order=3,
        hideInMenu=False,
        keepAlive=True,
    )
    print("✓ 客户端公告菜单创建成功")


def remove_client_announcement_menu(apps, schema_editor):
    """删除客户端公告菜单"""
    Menu = apps.get_model('core', 'Menu')
    Menu.objects.filter(path='/message/client-announcement').delete()
    print("✓ 客户端公告菜单已删除")


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0028_add_client_announcement'),
    ]

    operations = [
        migrations.RunPython(add_client_announcement_menu, remove_client_announcement_menu),
    ]
