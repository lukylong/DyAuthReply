#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: douyin_rule_schema.py
@Desc: Douyin Rule Schema - 抖音回复规则数据验证模式
"""
import re
from datetime import time as dtime
from typing import Any, List, Optional, Union

from ninja import Field, ModelSchema, Schema
from pydantic import field_validator

from common.fu_model import exclude_fields
from common.fu_schema import FuFilters
from core.douyin.douyin_rule_model import DouyinRule


class DouyinRuleFilters(FuFilters):
    """规则过滤器"""
    account_id: Optional[str] = Field(None, q="account_id", alias="account_id")
    name: Optional[str] = Field(None, q="name__icontains", alias="name")
    match_type: Optional[str] = Field(None, q="match_type", alias="match_type")
    status: Optional[bool] = Field(None, q="status", alias="status")


class DouyinRuleLinkIn(Schema):
    """规则附带链接（与模板 links 结构一致）"""
    title: Optional[str] = ''
    url: str


class DouyinRuleSchemaIn(ModelSchema):
    """规则输入模式

    account_id 可为空：表示全局规则（对所有账号生效）。
    """
    account_id: Optional[str] = Field(None, description="所属抖音账号ID；为空表示全局规则，对所有账号生效")
    template_id: Optional[str] = Field(None, description="引用模板ID")
    links: Optional[List[Union[DouyinRuleLinkIn, str, dict[str, Any]]]] = None

    class Config:
        model = DouyinRule
        model_exclude = (*exclude_fields, 'account', 'hit_count', 'template')

    @field_validator('regex_pattern', check_fields=False)
    @classmethod
    def validate_regex(cls, v):
        if not v:
            return v
        try:
            re.compile(v)
        except re.error as exc:
            raise ValueError(f'正则表达式非法: {exc}')
        return v


class DouyinRuleSchemaPatch(Schema):
    """规则部分更新模式"""
    name: Optional[str] = None
    match_type: Optional[str] = None
    keywords: Optional[List[str]] = None
    regex_pattern: Optional[str] = None
    reply_text: Optional[str] = None
    links: Optional[List[Union[DouyinRuleLinkIn, str, dict[str, Any]]]] = None
    template_id: Optional[str] = None
    send_mode: Optional[str] = None
    channel: Optional[str] = None
    time_window_start: Optional[dtime] = None
    time_window_end: Optional[dtime] = None
    weekday_mask: Optional[str] = None
    priority: Optional[int] = None
    status: Optional[bool] = None
    cooldown_seconds: Optional[int] = None
    remark: Optional[str] = None

    @field_validator('regex_pattern')
    @classmethod
    def validate_regex(cls, v):
        if not v:
            return v
        try:
            re.compile(v)
        except re.error as exc:
            raise ValueError(f'正则表达式非法: {exc}')
        return v


class DouyinRuleSchemaOut(ModelSchema):
    """规则输出模式"""
    match_type_display: Optional[str] = None
    send_mode_display: Optional[str] = None
    account_id: Optional[str] = None
    account_nickname: Optional[str] = None
    template_id: Optional[str] = None
    template_name: Optional[str] = None

    class Config:
        model = DouyinRule
        model_fields = "__all__"

    @staticmethod
    def resolve_match_type_display(obj):
        return obj.get_match_type_display()

    @staticmethod
    def resolve_send_mode_display(obj):
        return obj.get_send_mode_display()

    @staticmethod
    def resolve_account_id(obj):
        return str(obj.account_id) if obj.account_id else None

    @staticmethod
    def resolve_account_nickname(obj):
        if not obj.account_id:
            return "全部账号（默认规则）"
        try:
            return obj.account.nickname
        except Exception:
            return None

    @staticmethod
    def resolve_template_id(obj):
        return str(obj.template_id) if obj.template_id else None

    @staticmethod
    def resolve_template_name(obj):
        try:
            return obj.template.name if obj.template_id else None
        except Exception:
            return None


class DouyinRuleBatchDeleteIn(Schema):
    ids: List[str] = Field(..., description="规则ID列表")


class DouyinRuleBatchDeleteOut(Schema):
    count: int


class DouyinRuleQuickEnableIn(Schema):
    account_id: str = Field(..., description="所属抖音账号ID")
    reply_text: str = Field(..., description="自动回复文案")
    keywords: Optional[List[str]] = Field(default=None, description="可选关键词列表；填写后创建关键词规则")
    cooldown_seconds: int = Field(300, description="同会话冷却秒数")
    send_mode: str = Field("merged", description="发送模式：merged/multi_message/card_fallback")


class DouyinRuleQuickEnableOut(Schema):
    created: bool
    message: str
    rule_id: str
