#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: douyin_rule_schema.py
@Desc: Douyin Rule Schema - 抖音回复规则数据验证模式
"""
import re
from typing import List, Optional

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


class DouyinRuleSchemaIn(ModelSchema):
    """规则输入模式"""
    account_id: str = Field(..., description="所属抖音账号ID")

    class Config:
        model = DouyinRule
        model_exclude = (*exclude_fields, 'account', 'hit_count')

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
    links: Optional[List[str]] = None
    send_mode: Optional[str] = None
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
        return obj.account.nickname if obj.account_id else None


class DouyinRuleBatchDeleteIn(Schema):
    ids: List[str] = Field(..., description="规则ID列表")


class DouyinRuleBatchDeleteOut(Schema):
    count: int
