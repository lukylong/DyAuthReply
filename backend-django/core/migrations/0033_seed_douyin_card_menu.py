#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
数据迁移：注入「卡片管理」菜单到抖音托管模块。

幂等：如果菜单已存在（按 path 判断），跳过。
"""
from django.db import migrations

DOUYIN_PARENT_ID = 'd0000000-0000-0000-0000-000000090000'

CARD_MENU = dict(
    id='d0000000-0000-0000-0000-000000090011',
    parent_id=DOUYIN_PARENT_ID,
    name='DouyinCard',
    title='卡片管理',
    path='/douyin/card',
    type='menu',
    component='/douyin/card/index',
    icon='lucide:credit-card',
    order=11,
)


def seed_card_menu(apps, schema_editor):
    Menu = apps.get_model('core', 'Menu')
    if Menu.objects.filter(path=CARD_MENU['path']).exists():
        return
    Menu.objects.create(
        id=CARD_MENU['id'],
        parent_id=CARD_MENU['parent_id'],
        name=CARD_MENU['name'],
        title=CARD_MENU['title'],
        path=CARD_MENU['path'],
        type=CARD_MENU['type'],
        component=CARD_MENU['component'],
        icon=CARD_MENU['icon'],
        order=CARD_MENU['order'],
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


def remove_card_menu(apps, schema_editor):
    Menu = apps.get_model('core', 'Menu')
    Menu.objects.filter(id=CARD_MENU['id']).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0032_douyinrule_card_ids_douyincard'),
    ]

    operations = [
        migrations.RunPython(seed_card_menu, reverse_code=remove_card_menu),
    ]
