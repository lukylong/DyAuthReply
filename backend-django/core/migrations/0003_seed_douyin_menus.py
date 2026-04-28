#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
数据迁移：自动注入抖音托管模块菜单树

- 一级目录：抖音托管（/douyin）
- 二级菜单：看板、账号管理、分组、会话监控、模板、快捷回复、规则、黑名单、事件、回复日志

幂等：如果菜单已存在（按 path 判断），跳过。
"""
from django.db import migrations

DOUYIN_PARENT_ID = 'd0000000-0000-0000-0000-000000090000'

DOUYIN_MENUS = [
    # 一级目录
    dict(
        id=DOUYIN_PARENT_ID,
        parent_id=None,
        name='DouyinCenter',
        title='抖音托管',
        path='/douyin',
        type='catalog',
        component=None,
        icon='lucide:video',
        order=5,
    ),
    # 二级菜单
    dict(
        id='d0000000-0000-0000-0000-000000090001',
        parent_id=DOUYIN_PARENT_ID,
        name='DouyinDashboard',
        title='托管看板',
        path='/douyin/dashboard',
        type='menu',
        component='/douyin/dashboard/index',
        icon='lucide:layout-dashboard',
        order=1,
    ),
    dict(
        id='d0000000-0000-0000-0000-000000090002',
        parent_id=DOUYIN_PARENT_ID,
        name='DouyinAccount',
        title='账号管理',
        path='/douyin/account',
        type='menu',
        component='/douyin/account/index',
        icon='lucide:user-round',
        order=2,
    ),
    dict(
        id='d0000000-0000-0000-0000-000000090003',
        parent_id=DOUYIN_PARENT_ID,
        name='DouyinAccountGroup',
        title='账号分组',
        path='/douyin/account-group',
        type='menu',
        component='/douyin/account-group/index',
        icon='lucide:users-round',
        order=3,
    ),
    dict(
        id='d0000000-0000-0000-0000-000000090004',
        parent_id=DOUYIN_PARENT_ID,
        name='DouyinSession',
        title='会话监控',
        path='/douyin/session',
        type='menu',
        component='/douyin/session/index',
        icon='lucide:activity',
        order=4,
    ),
    dict(
        id='d0000000-0000-0000-0000-000000090005',
        parent_id=DOUYIN_PARENT_ID,
        name='DouyinTemplate',
        title='回复模板',
        path='/douyin/template',
        type='menu',
        component='/douyin/template/index',
        icon='lucide:file-text',
        order=5,
    ),
    dict(
        id='d0000000-0000-0000-0000-000000090006',
        parent_id=DOUYIN_PARENT_ID,
        name='DouyinQuickReply',
        title='快捷回复',
        path='/douyin/quick-reply',
        type='menu',
        component='/douyin/quick-reply/index',
        icon='lucide:zap',
        order=6,
    ),
    dict(
        id='d0000000-0000-0000-0000-000000090007',
        parent_id=DOUYIN_PARENT_ID,
        name='DouyinRule',
        title='回复规则',
        path='/douyin/rule',
        type='menu',
        component='/douyin/rule/index',
        icon='lucide:git-branch',
        order=7,
    ),
    dict(
        id='d0000000-0000-0000-0000-000000090008',
        parent_id=DOUYIN_PARENT_ID,
        name='DouyinBlacklist',
        title='黑名单',
        path='/douyin/blacklist',
        type='menu',
        component='/douyin/blacklist/index',
        icon='lucide:shield-off',
        order=8,
    ),
    dict(
        id='d0000000-0000-0000-0000-000000090009',
        parent_id=DOUYIN_PARENT_ID,
        name='DouyinEvent',
        title='运行事件',
        path='/douyin/event',
        type='menu',
        component='/douyin/event/index',
        icon='lucide:bell',
        order=9,
    ),
    dict(
        id='d0000000-0000-0000-0000-000000090010',
        parent_id=DOUYIN_PARENT_ID,
        name='DouyinReplyLog',
        title='回复日志',
        path='/douyin/reply-log',
        type='menu',
        component='/douyin/reply-log/index',
        icon='lucide:scroll-text',
        order=10,
    ),
]


def seed_douyin_menus(apps, schema_editor):
    Menu = apps.get_model('core', 'Menu')
    for item in DOUYIN_MENUS:
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


def remove_douyin_menus(apps, schema_editor):
    Menu = apps.get_model('core', 'Menu')
    ids = [m['id'] for m in DOUYIN_MENUS]
    Menu.objects.filter(id__in=ids).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0002_douyin_multi_account_enhance'),
    ]

    operations = [
        migrations.RunPython(seed_douyin_menus, reverse_code=remove_douyin_menus),
    ]
