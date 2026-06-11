#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
数据迁移：自动注入快手托管模块菜单树

- 一级目录：快手托管（/kuaishou）
- 二级菜单：看板、账号管理、账号分组、会话监控、运行事件、回复日志

幂等：如果菜单已存在（按 path 判断），跳过。
回复规则 / 模板 / 黑名单 / 快捷回复属于跨平台共用层，后续单独注入"内容策略"目录。
"""
from django.db import migrations

KUAISHOU_PARENT_ID = 'c0000000-0000-0000-0000-00000000a000'

KUAISHOU_MENUS = [
    # 一级目录
    dict(
        id=KUAISHOU_PARENT_ID,
        parent_id=None,
        name='KuaishouCenter',
        title='快手托管',
        path='/kuaishou',
        type='catalog',
        component=None,
        icon='lucide:tv',
        order=6,
    ),
    # 二级菜单
    dict(
        id='c0000000-0000-0000-0000-00000000a001',
        parent_id=KUAISHOU_PARENT_ID,
        name='KuaishouDashboard',
        title='托管看板',
        path='/kuaishou/dashboard',
        type='menu',
        component='/kuaishou/dashboard/index',
        icon='lucide:layout-dashboard',
        order=1,
    ),
    dict(
        id='c0000000-0000-0000-0000-00000000a002',
        parent_id=KUAISHOU_PARENT_ID,
        name='KuaishouAccount',
        title='账号管理',
        path='/kuaishou/account',
        type='menu',
        component='/kuaishou/account/index',
        icon='lucide:user-round',
        order=2,
    ),
    dict(
        id='c0000000-0000-0000-0000-00000000a003',
        parent_id=KUAISHOU_PARENT_ID,
        name='KuaishouAccountGroup',
        title='账号分组',
        path='/kuaishou/account-group',
        type='menu',
        component='/kuaishou/account-group/index',
        icon='lucide:users-round',
        order=3,
    ),
    dict(
        id='c0000000-0000-0000-0000-00000000a004',
        parent_id=KUAISHOU_PARENT_ID,
        name='KuaishouSession',
        title='会话监控',
        path='/kuaishou/session',
        type='menu',
        component='/kuaishou/session/index',
        icon='lucide:activity',
        order=4,
    ),
    dict(
        id='c0000000-0000-0000-0000-00000000a005',
        parent_id=KUAISHOU_PARENT_ID,
        name='KuaishouEvent',
        title='运行事件',
        path='/kuaishou/event',
        type='menu',
        component='/kuaishou/event/index',
        icon='lucide:bell',
        order=5,
    ),
    dict(
        id='c0000000-0000-0000-0000-00000000a006',
        parent_id=KUAISHOU_PARENT_ID,
        name='KuaishouReplyLog',
        title='回复日志',
        path='/kuaishou/reply-log',
        type='menu',
        component='/kuaishou/reply-log/index',
        icon='lucide:scroll-text',
        order=6,
    ),
]


def seed_kuaishou_menus(apps, schema_editor):
    Menu = apps.get_model('core', 'Menu')
    for item in KUAISHOU_MENUS:
        if Menu.objects.filter(path=item['path']).exists():
            continue
        Menu.objects.create(
            id=item['id'],
            parent_id=item['parent_id'],
            name=item['name'],
            title=item['title'],
            path=item['path'],
            type=item['type'],
            component=item['component'],
            icon=item['icon'],
            order=item['order'],
            sort=0,
            hideInMenu=False,
            hideChildrenInMenu=False,
            hideInBreadcrumb=False,
            hideInTab=False,
            affixTab=False,
            keepAlive=False,
            noBasicLayout=False,
            openInNewWindow=False,
        )


def remove_kuaishou_menus(apps, schema_editor):
    Menu = apps.get_model('core', 'Menu')
    ids = [m['id'] for m in KUAISHOU_MENUS]
    Menu.objects.filter(id__in=ids).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0011_kuaishouaccountgroup_kuaishouaccount_and_more'),
    ]

    operations = [
        migrations.RunPython(seed_kuaishou_menus, reverse_code=remove_kuaishou_menus),
    ]
