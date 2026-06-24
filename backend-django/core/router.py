#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Core Router - 核心模块路由配置
统一管理 core 模块的所有路由
"""
from ninja import Router

from core.auth.auth_api import router as auth_router
from core.database_manager.database_manager_api import router as database_manager_router
from core.database_monitor.database_monitor_api import router as database_monitor_router
from core.dept.dept_api import router as dept_router
from core.dict.dict_api import router as dict_router
from core.dict_item.dict_item_api import router as dict_item_router
from core.file_manager.file_manager_api import router as file_manager_router
from core.login_log.login_log_api import router as login_log_router
from core.license.license_api import router as license_router
from core.menu.menu_api import router as menu_router
from core.message.announcement_api import router as announcement_router
from core.message.message_api import router as message_router
from core.oauth.oauth_api import router as oauth_router
from core.page_manager.page_api import router as page_manager_router
from core.permission.permission_api import router as permission_router
from core.post.post_api import router as post_router
from core.redis_manager.redis_manager_api import router as redis_manager_router
from core.redis_monitor.redis_monitor_api import router as redis_monitor_router
from core.role.role_api import router as role_router
from core.server_monitor.server_monitor_api import router as server_monitor_router
from core.user.user_api import router as user_router
from core.data_source.data_source_api import router as data_source_router
from core.douyin.douyin_account_api import router as douyin_account_router
from core.douyin.douyin_account_group_api import router as douyin_account_group_router
from core.douyin.douyin_blacklist_api import router as douyin_blacklist_router
from core.douyin.douyin_event_api import router as douyin_event_router
from core.douyin.douyin_quick_reply_api import router as douyin_quick_reply_router
from core.douyin.douyin_reply_log_api import router as douyin_reply_log_router
from core.douyin.douyin_rule_api import router as douyin_rule_router
from core.douyin.douyin_session_api import router as douyin_session_router
from core.douyin.douyin_stat_api import router as douyin_stat_router
from core.douyin.douyin_template_api import router as douyin_template_router
from core.douyin.douyin_worker_monitor_api import router as douyin_worker_monitor_router
from core.social.template_api import router as social_template_router
from core.social.quick_reply_api import router as social_quick_reply_router
from core.social.rule_api import router as social_rule_router
from core.social.blacklist_api import router as social_blacklist_router
from core.kuaishou.kuaishou_account_api import router as kuaishou_account_router
from core.kuaishou.kuaishou_account_group_api import router as kuaishou_account_group_router
from core.kuaishou.kuaishou_session_api import router as kuaishou_session_router
from core.kuaishou.kuaishou_event_api import router as kuaishou_event_router
from core.kuaishou.kuaishou_reply_log_api import router as kuaishou_reply_log_router
from core.client_announcement.client_announcement_api import router as client_announcement_router


# 创建核心模块的总路由
core_router = Router()

# 注册子路由
core_router.add_router("", auth_router, tags=["Core-Auth"])
core_router.add_router("", user_router, tags=["Core-User"])
core_router.add_router("", role_router, tags=["Core-Role"])
core_router.add_router("", permission_router, tags=["Core-Permission"])
core_router.add_router("", dept_router, tags=["Core-Dept"])
core_router.add_router("", post_router, tags=["Core-Post"])
core_router.add_router("", menu_router, tags=["Core-Menu"])
core_router.add_router("", dict_router, tags=["Core-Dict"])
core_router.add_router("", dict_item_router, tags=["Core-DictItem"])
core_router.add_router("", login_log_router, tags=["Core-LoginLog"])
core_router.add_router("", license_router, tags=["Core-License"])
core_router.add_router("", server_monitor_router, tags=["Core-ServerMonitor"])
core_router.add_router("", redis_monitor_router, tags=["Core-RedisMonitor"])
core_router.add_router("", redis_manager_router, tags=["Core-RedisManager"])
core_router.add_router("", database_monitor_router, tags=["Core-DatabaseMonitor"])
core_router.add_router("", database_manager_router, tags=["Core-DatabaseManager"])
core_router.add_router("", file_manager_router, tags=["Core-FileManager"])
core_router.add_router("/oauth", oauth_router, tags=["Core-OAuth"])
core_router.add_router("", data_source_router, tags=["Core-DataSource"])
core_router.add_router("/message", message_router, tags=["Core-Message"])
core_router.add_router("/announcement", announcement_router, tags=["Core-Announcement"])
core_router.add_router("/page", page_manager_router, tags=["Core-PageManager"])
core_router.add_router("/douyin", douyin_account_router, tags=["Core-Douyin-Account"])
core_router.add_router("/douyin", douyin_account_group_router, tags=["Core-Douyin-AccountGroup"])
core_router.add_router("/douyin", douyin_rule_router, tags=["Core-Douyin-Rule"])
core_router.add_router("/douyin", douyin_template_router, tags=["Core-Douyin-Template"])
core_router.add_router("/douyin", douyin_session_router, tags=["Core-Douyin-Session"])
core_router.add_router("/douyin", douyin_blacklist_router, tags=["Core-Douyin-Blacklist"])
core_router.add_router("/douyin", douyin_quick_reply_router, tags=["Core-Douyin-QuickReply"])
core_router.add_router("/douyin", douyin_event_router, tags=["Core-Douyin-Event"])
core_router.add_router("/douyin", douyin_stat_router, tags=["Core-Douyin-Stat"])
core_router.add_router("/douyin", douyin_reply_log_router, tags=["Core-Douyin-ReplyLog"])
core_router.add_router("/douyin", douyin_worker_monitor_router, tags=["Core-Douyin-WorkerMonitor"])

# 跨平台共用层（抖音/快手共享：模板、快捷回复、规则、黑名单）
core_router.add_router("/social", social_template_router, tags=["Core-Social-Template"])
core_router.add_router("/social", social_quick_reply_router, tags=["Core-Social-QuickReply"])
core_router.add_router("/social", social_rule_router, tags=["Core-Social-Rule"])
core_router.add_router("/social", social_blacklist_router, tags=["Core-Social-Blacklist"])

# 快手账号托管（平台运行时层；规则/模板/黑名单复用 /social）
core_router.add_router("/kuaishou", kuaishou_account_router, tags=["Core-Kuaishou-Account"])
core_router.add_router("/kuaishou", kuaishou_account_group_router, tags=["Core-Kuaishou-AccountGroup"])
core_router.add_router("/kuaishou", kuaishou_session_router, tags=["Core-Kuaishou-Session"])
core_router.add_router("/kuaishou", kuaishou_event_router, tags=["Core-Kuaishou-Event"])
core_router.add_router("/kuaishou", kuaishou_reply_log_router, tags=["Core-Kuaishou-ReplyLog"])

# 客户端公告（管理后台）
core_router.add_router("/client-announcement", client_announcement_router, tags=["Core-ClientAnnouncement"])
