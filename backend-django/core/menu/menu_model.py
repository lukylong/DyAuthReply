#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: menu_model.py
@Desc: Menu Model - 菜单模型 - 用于管理系统菜单和前端路由
"""
"""
Menu Model - 菜单模型
用于管理系统菜单和前端路由
"""
from django.db import models

from common.fu_model import RootModel


class Menu(RootModel):
    """系统菜单表"""

    # 基础信息
    parent = models.ForeignKey(
        to="Menu",
        on_delete=models.CASCADE,
        db_constraint=False,
        blank=True,
        null=True,
        help_text="Parent Menu",
    )
    name = models.CharField(max_length=100, help_text='菜单名称')
    title = models.CharField(max_length=100, blank=True, null=True, help_text='菜单标题')
    authCode = models.CharField(max_length=100, blank=True, null=True, help_text='后端权限标识')
    path = models.CharField(max_length=200, help_text='路由路径')
    type = models.CharField(
        max_length=20,
        default='catalog',
        help_text='菜单类型'
    )

    # 路由配置
    component = models.CharField(max_length=100, blank=True, null=True, help_text='组件')
    redirect = models.CharField(max_length=200, blank=True, null=True, help_text='重定向')
    activePath = models.CharField(max_length=200, blank=True, null=True, help_text='激活路径')
    query = models.JSONField(blank=True, null=True, help_text='额外路由参数')
    noBasicLayout = models.BooleanField(default=False, help_text='无需基础布局')

    # 菜单展示
    icon = models.CharField(max_length=100, blank=True, null=True, help_text='菜单图标')
    activeIcon = models.CharField(max_length=100, blank=True, null=True, help_text='激活图标')
    order = models.IntegerField(default=0, help_text='菜单排序')
    hideInMenu = models.BooleanField(default=False, help_text='在菜单中隐藏')
    hideChildrenInMenu = models.BooleanField(default=False, help_text='在菜单中隐藏下级')
    hideInBreadcrumb = models.BooleanField(default=False, help_text='在面包屑中隐藏')

    # 标签页配置
    hideInTab = models.BooleanField(default=False, help_text='在标签栏中隐藏')
    affixTab = models.BooleanField(default=False, help_text='固定在标签栏')
    affixTabOrder = models.IntegerField(blank=True, null=True, help_text='标签栏固定顺序')
    keepAlive = models.BooleanField(default=False, help_text='缓存页面')
    maxNumOfOpenTab = models.IntegerField(blank=True, null=True, help_text='最大打开标签数')

    # 外部链接配置
    link = models.CharField(max_length=200, blank=True, null=True, help_text='外链URL')
    iframeSrc = models.CharField(max_length=200, blank=True, null=True, help_text='内嵌iframe URL')
    openInNewWindow = models.BooleanField(default=False, help_text='在新窗口打开')

    # 徽标配置
    badge = models.CharField(max_length=20, blank=True, null=True, help_text='徽标内容')
    badgeType = models.CharField(
        max_length=20,
        blank=True,
        null=True,
        help_text='徽标类型'
    )
    badgeVariants = models.CharField(
        max_length=20,
        blank=True,
        null=True,
        help_text='徽标颜色'
    )

    class Meta:
        db_table = "core_menu"
        ordering = ("order",)

    def __str__(self):
        return f"{self.title or self.name} ({self.path})"

    def get_level(self):
        """计算菜单层级"""
        if self.parent:
            return self.parent.get_level() + 1
        return 0

    def get_full_path(self):
        """获取完整路径（包含父菜单）"""
        if self.parent:
            return f"{self.parent.get_full_path()}/{self.path.lstrip('/')}"
        return self.path

    def get_ancestors(self):
        """获取所有祖先菜单"""
        ancestors = []
        current = self.parent
        while current:
            ancestors.append(current)
            current = current.parent
        return ancestors

    def get_descendants(self):
        """获取所有后代菜单"""
        descendants = []

        def collect_children(menu):
            children = Menu.objects.filter(parent=menu)
            for child in children:
                descendants.append(child)
                collect_children(child)

        collect_children(self)
        return descendants

    def get_child_count(self):
        """获取直接子菜单数量"""
        return Menu.objects.filter(parent=self).count()

    def is_leaf(self):
        """判断是否为叶子节点"""
        return self.get_child_count() == 0

    def is_root(self):
        """判断是否为根节点"""
        return self.parent is None

    def can_delete(self):
        """判断是否可以删除（没有子菜单）"""
        return self.is_leaf()
