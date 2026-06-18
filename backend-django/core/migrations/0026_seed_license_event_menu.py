#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Seed license event menu for existing environments."""
import uuid

from django.db import migrations

MENU_ID = "e0000000-0000-0000-0000-00000000b005"
PARENT_ID = "e0000000-0000-0000-0000-00000000b000"
MENU_PATH = "/license/event"
PERMISSION_CODE = "license:event:view"


def seed_event_menu(apps, schema_editor):
    Menu = apps.get_model("core", "Menu")
    Permission = apps.get_model("core", "Permission")

    menu = Menu.objects.filter(path=MENU_PATH, is_deleted=False).first()
    if not menu:
        menu = Menu.objects.create(
            id=MENU_ID,
            parent_id=PARENT_ID,
            name="LicenseEvents",
            title="授权事件",
            path=MENU_PATH,
            type="menu",
            component="/license/event/index",
            icon="lucide:scroll-text",
            order=5,
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

    if not Permission.objects.filter(code=PERMISSION_CODE, is_deleted=False).exists():
        Permission.objects.create(
            id=str(uuid.uuid4()),
            menu=menu,
            name="授权事件查看",
            code=PERMISSION_CODE,
            permission_type=1,
            is_active=True,
            is_deleted=False,
        )


def remove_event_menu(apps, schema_editor):
    Menu = apps.get_model("core", "Menu")
    Permission = apps.get_model("core", "Permission")

    Menu.objects.filter(path=MENU_PATH, is_deleted=False).update(is_deleted=True)
    Permission.objects.filter(code=PERMISSION_CODE, is_deleted=False).update(is_deleted=True)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0025_seed_license_menus"),
    ]

    operations = [
        migrations.RunPython(seed_event_menu, remove_event_menu),
    ]
