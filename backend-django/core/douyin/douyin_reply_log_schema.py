#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: douyin_reply_log_schema.py
@Desc: Douyin Reply Log Schema - 抖音回复日志数据验证模式
"""
from typing import Optional

from ninja import Field, ModelSchema, Schema

from common.fu_schema import FuFilters
from core.douyin.douyin_reply_log_model import DouyinReplyLog


class DouyinReplyLogFilters(FuFilters):
    """回复日志过滤器"""
    account_id: Optional[str] = Field(None, q="account_id", alias="account_id")
    conversation_id: Optional[str] = Field(None, q="conversation_id", alias="conversation_id")
    matched_rule_id: Optional[str] = Field(None, q="matched_rule_id", alias="matched_rule_id")
    result: Optional[str] = Field(None, q="result", alias="result")


class DouyinReplyLogSchemaOut(ModelSchema):
    """回复日志输出"""
    result_display: Optional[str] = None
    account_id: Optional[str] = None
    account_nickname: Optional[str] = None
    conversation_id: Optional[str] = None
    matched_rule_id: Optional[str] = None
    rule_name: Optional[str] = None
    peer_nickname: Optional[str] = None
    trigger_message_content: Optional[str] = None

    class Config:
        model = DouyinReplyLog
        model_fields = "__all__"

    @staticmethod
    def resolve_result_display(obj):
        return obj.get_result_display()

    @staticmethod
    def resolve_account_id(obj):
        return str(obj.account_id) if obj.account_id else None

    @staticmethod
    def resolve_conversation_id(obj):
        return str(obj.conversation_id) if obj.conversation_id else None

    @staticmethod
    def resolve_matched_rule_id(obj):
        return str(obj.matched_rule_id) if obj.matched_rule_id else None

    @staticmethod
    def resolve_rule_name(obj):
        try:
            return obj.matched_rule.name if obj.matched_rule_id else None
        except Exception:
            return None

    @staticmethod
    def resolve_peer_nickname(obj):
        try:
            return obj.conversation.peer_nickname if obj.conversation_id else None
        except Exception:
            return None

    @staticmethod
    def resolve_account_nickname(obj):
        try:
            return obj.account.nickname if obj.account_id else None
        except Exception:
            return None

    @staticmethod
    def resolve_trigger_message_content(obj):
        try:
            return obj.trigger_message.content if obj.trigger_message_id else None
        except Exception:
            return None


class DouyinReplyLogStatOut(Schema):
    """回复日志统计输出"""
    total: int = 0
    success: int = 0
    failed: int = 0
    skipped: int = 0
    cooldown: int = 0
    quota_exceeded: int = 0
    silent: int = 0
    avg_duration_ms: float = 0.0
