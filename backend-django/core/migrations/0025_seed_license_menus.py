#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Seed license authorization menus and permissions."""
import uuid

from django.db import migrations

LICENSE_PARENT_PATH = "/license"
LICENSE_PARENT_ID = "e0000000-0000-0000-0000-00000000b000"

LICENSE_MENUS = [
    dict(
        id=LICENSE_PARENT_ID,
        parent_id=None,
        name="LicenseCenter",
        title="授权中心",
        path=LICENSE_PARENT_PATH,
        type="catalog",
        component=None,
        icon="lucide:key-round",
        order=7,
    ),
    dict(
        id="e0000000-0000-0000-0000-00000000b001",
        parent_id=LICENSE_PARENT_ID,
        name="LicensePlans",
        title="套餐管理",
        path="/license/plan",
        type="menu",
        component="/license/plan/index",
        icon="lucide:package-2",
        order=1,
    ),
    dict(
        id="e0000000-0000-0000-0000-00000000b002",
        parent_id=LICENSE_PARENT_ID,
        name="LicenseKeys",
        title="卡密管理",
        path="/license/key",
        type="menu",
        component="/license/key/index",
        icon="lucide:ticket",
        order=2,
    ),
    dict(
        id="e0000000-0000-0000-0000-00000000b003",
        parent_id=LICENSE_PARENT_ID,
        name="LicenseDevices",
        title="设备绑定",
        path="/license/device",
        type="menu",
        component="/license/device/index",
        icon="lucide:laptop-minimal",
        order=3,
    ),
    dict(
        id="e0000000-0000-0000-0000-00000000b004",
        parent_id=LICENSE_PARENT_ID,
        name="LicenseActivations",
        title="激活记录",
        path="/license/activation",
        type="menu",
        component="/license/activation/index",
        icon="lucide:history",
        order=4,
    ),
    dict(
        id="e0000000-0000-0000-0000-00000000b005",
        parent_id=LICENSE_PARENT_ID,
        name="LicenseEvents",
        title="授权事件",
        path="/license/event",
        type="menu",
        component="/license/event/index",
        icon="lucide:scroll-text",
        order=5,
    ),
]

LICENSE_PERMISSIONS = [
    dict(menu_path="/license/plan", name="授权套餐查看", code="license:plan:view"),
    dict(menu_path="/license/plan", name="授权套餐编辑", code="license:plan:edit"),
    dict(menu_path="/license/key", name="卡密查看", code="license:key:view"),
    dict(menu_path="/license/key", name="卡密生成", code="license:key:generate"),
    dict(menu_path="/license/key", name="卡密撤销", code="license:key:revoke"),
    dict(menu_path="/license/device", name="设备绑定查看", code="license:device:view"),
    dict(menu_path="/license/device", name="设备解绑", code="license:device:unbind"),
    dict(menu_path="/license/activation", name="激活记录查看", code="license:activation:view"),
    dict(menu_path="/license/event", name="授权事件查看", code="license:event:view"),
]


def seed_license_menus(apps, schema_editor):
    Menu = apps.get_model("core", "Menu")
    Permission = apps.get_model("core", "Permission")

    for item in LICENSE_MENUS:
        menu = Menu.objects.filter(path=item["path"], is_deleted=False).first()
        if menu:
            continue
        Menu.objects.create(
            id=item["id"],
            parent_id=item["parent_id"],
            name=item["name"],
            title=item["title"],
            path=item["path"],
            type=item["type"],
            component=item["component"],
            icon=item["icon"],
            order=item["order"],
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

    for item in LICENSE_PERMISSIONS:
        if Permission.objects.filter(code=item["code"], is_deleted=False).exists():
            continue
        menu = Menu.objects.filter(path=item["menu_path"], is_deleted=False).first()
        if not menu:
            continue
        Permission.objects.create(
            id=str(uuid.uuid4()),
            menu=menu,
            name=item["name"],
            code=item["code"],
            permission_type=1,
            is_active=True,
            is_deleted=False,
        )


def remove_license_menus(apps, schema_editor):
    Menu = apps.get_model("core", "Menu")
    Permission = apps.get_model("core", "Permission")

    Menu.objects.filter(path__in=[item["path"] for item in LICENSE_MENUS], is_deleted=False).update(is_deleted=True)
    Permission.objects.filter(code__in=[item["code"] for item in LICENSE_PERMISSIONS], is_deleted=False).update(is_deleted=True)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0024_add_license_authorization"),
    ]

    operations = [
        migrations.RunPython(seed_license_menus, remove_license_menus),
    ]
