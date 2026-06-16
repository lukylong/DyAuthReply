#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""添加抖音 Worker 进程监控菜单"""
import uuid

from django.db import migrations


def add_worker_monitor_menu(apps, schema_editor):
    Menu = apps.get_model('core', 'Menu')
    Permission = apps.get_model('core', 'Permission')

    douyin_parent = Menu.objects.filter(path='/douyin', is_deleted=False).first()
    if not douyin_parent:
        print('警告: 未找到抖音父菜单，跳过 Worker 监控菜单创建')
        return

    existing = Menu.objects.filter(path='/douyin/worker-monitor', is_deleted=False).first()
    if existing:
        menu = existing
        print(f'✓ 菜单已存在: Worker 进程监控 (ID: {menu.id})')
    else:
        menu_id = str(uuid.uuid4())
        menu = Menu.objects.create(
            id=menu_id,
            name='DouyinWorkerMonitor',
            title='Worker 进程监控',
            path='/douyin/worker-monitor',
            component='/douyin/worker-monitor/index',
            parent=douyin_parent,
            type='menu',
            icon='lucide:cpu',
            order=6,
            hideInMenu=False,
            is_deleted=False,
        )
        print(f'✓ 已创建菜单: Worker 进程监控 (ID: {menu_id})')

    if not Permission.objects.filter(code='douyin:worker-monitor:view', is_deleted=False).exists():
        Permission.objects.create(
            id=str(uuid.uuid4()),
            name='抖音 Worker 进程监控',
            code='douyin:worker-monitor:view',
            permission_type=1,
            menu=menu,
            is_active=True,
            is_deleted=False,
        )
        print('✓ 已创建权限: douyin:worker-monitor:view')


def remove_worker_monitor_menu(apps, schema_editor):
    Menu = apps.get_model('core', 'Menu')
    Permission = apps.get_model('core', 'Permission')
    Menu.objects.filter(path='/douyin/worker-monitor', is_deleted=False).update(is_deleted=True)
    Permission.objects.filter(code='douyin:worker-monitor:view', is_deleted=False).update(is_deleted=True)


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0019_add_unique_id_to_douyin_account'),
    ]

    operations = [
        migrations.RunPython(add_worker_monitor_menu, remove_worker_monitor_menu),
    ]
