#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Seed the client runtime diagnostics menu (opt-in client reporting)."""
import uuid

from django.db import migrations

MENU_ID = "e0000000-0000-0000-0000-00000000b006"
PARENT_ID = "e0000000-0000-0000-0000-00000000b000"
MENU_PATH = "/license/diagnostics"
PERMISSION_CODE = "license:diagnostics:view"


def seed_diagnostics_menu(apps, schema_editor):
    Menu = apps.get_model("core", "Menu")
    Permission = apps.get_model("core", "Permission")

    menu = Menu.objects.filter(path=MENU_PATH, is_deleted=False).first()
    if not menu:
        menu = Menu.objects.create(
            id=MENU_ID,
            parent_id=PARENT_ID,
            name="LicenseDiagnostics",
            title="运行诊断",
            path=MENU_PATH,
            type="menu",
            component="/license/diagnostics/index",
            icon="lucide:activity",
            order=6,
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
            name="运行诊断查看",
            code=PERMISSION_CODE,
            permission_type=1,
            is_active=True,
            is_deleted=False,
        )


def remove_diagnostics_menu(apps, schema_editor):
    Menu = apps.get_model("core", "Menu")
    Permission = apps.get_model("core", "Permission")

    Menu.objects.filter(path=MENU_PATH, is_deleted=False).update(is_deleted=True)
    Permission.objects.filter(code=PERMISSION_CODE, is_deleted=False).update(is_deleted=True)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0040_client_diagnostics"),
    ]

    operations = [
        migrations.RunPython(seed_diagnostics_menu, remove_diagnostics_menu),
    ]
