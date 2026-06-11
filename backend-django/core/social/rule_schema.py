#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""跨平台回复规则 Schema"""
import re
from datetime import time as dtime
from typing import List, Optional

from ninja import Field, ModelSchema, Schema
from pydantic import field_validator

from common.fu_model import exclude_fields
from common.fu_schema import FuFilters
from core.social.rule_model import ReplyRule


class ReplyRuleFilters(FuFilters):
    platform: Optional[str] = Field(None, q="platform", alias="platform")
    account_id: Optional[str] = Field(None, q="account_id", alias="account_id")
    name: Optional[str] = Field(None, q="name__icontains", alias="name")
    match_type: Optional[str] = Field(None, q="match_type", alias="match_type")
    status: Optional[bool] = Field(None, q="status", alias="status")


class ReplyRuleSchemaIn(ModelSchema):
    """规则输入模式

    account_id 可为空：表示该平台全局规则（对该平台所有账号生效）。
    """
    account_id: Optional[str] = Field(None, description="所属平台账号ID；为空表示该平台全局规则")
    template_id: Optional[str] = Field(None, description="引用模板ID")

    class Config:
        model = ReplyRule
        model_exclude = (*exclude_fields, 'hit_count', 'template')

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


class ReplyRuleSchemaPatch(Schema):
    """规则部分更新模式"""
    name: Optional[str] = None
    account_id: Optional[str] = None
    match_type: Optional[str] = None
    keywords: Optional[List[str]] = None
    regex_pattern: Optional[str] = None
    reply_text: Optional[str] = None
    links: Optional[List[str]] = None
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


class ReplyRuleSchemaOut(ModelSchema):
    """规则输出模式"""
    platform_display: Optional[str] = None
    match_type_display: Optional[str] = None
    send_mode_display: Optional[str] = None
    account_id: Optional[str] = None
    template_id: Optional[str] = None
    template_name: Optional[str] = None

    class Config:
        model = ReplyRule
        model_fields = "__all__"

    @staticmethod
    def resolve_platform_display(obj):
        return obj.get_platform_display()

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
    def resolve_template_id(obj):
        return str(obj.template_id) if obj.template_id else None

    @staticmethod
    def resolve_template_name(obj):
        try:
            return obj.template.name if obj.template_id else None
        except Exception:
            return None


class ReplyRuleBatchDeleteIn(Schema):
    ids: List[str] = Field(..., description="规则ID列表")


class ReplyRuleBatchDeleteOut(Schema):
    count: int
