#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""跨平台回复规则 API"""
from typing import List

from django.shortcuts import get_object_or_404
from ninja import Query, Router
from ninja.errors import HttpError
from ninja.pagination import paginate

from common.fu_crud import create, delete, retrieve
from common.fu_pagination import MyPagination
from core.social.constants import PLATFORM_VALUES
from core.social.rule_model import ReplyRule
from core.social.rule_schema import (
    ReplyRuleBatchDeleteIn,
    ReplyRuleBatchDeleteOut,
    ReplyRuleFilters,
    ReplyRuleSchemaIn,
    ReplyRuleSchemaOut,
    ReplyRuleSchemaPatch,
)
from core.social.services import account_exists
from core.social.template_model import ReplyTemplate

router = Router()


def _normalize_rule_payload(payload: dict) -> dict:
    normalized = dict(payload)
    template_id = normalized.pop('template_id', None)
    if template_id == '':
        template_id = None
    normalized['template_id'] = template_id
    account_id = normalized.get('account_id')
    if account_id == '':
        account_id = None
    normalized['account_id'] = account_id
    return normalized


def _validate_rule_payload(payload: dict) -> None:
    """根据 match_type 校验必填字段组合"""
    match_type = payload.get('match_type', 'contains')
    if match_type == 'contains':
        if not payload.get('keywords'):
            raise HttpError(400, "包含匹配规则需至少提供一个关键词")
    elif match_type == 'regex':
        if not payload.get('regex_pattern'):
            raise HttpError(400, "正则匹配规则需提供正则表达式")
    elif match_type == 'default':
        pass
    else:
        raise HttpError(400, f"不支持的匹配方式: {match_type}")


def _validate_refs(payload: dict) -> None:
    platform = payload.get('platform')
    if platform not in PLATFORM_VALUES:
        raise HttpError(400, f"不支持的平台: {platform}")
    if payload.get('account_id') and not account_exists(platform, payload['account_id']):
        raise HttpError(400, "所属账号不存在")
    if payload.get('template_id') and not ReplyTemplate.objects.filter(id=payload['template_id']).exists():
        raise HttpError(400, "引用模板不存在")


@router.get("/rule", response=List[ReplyRuleSchemaOut], summary="获取回复规则列表（分页）")
@paginate(MyPagination)
def list_rule(request, filters: ReplyRuleFilters = Query(...)):
    return retrieve(request, ReplyRule, filters)


@router.get("/rule/by/account/{account_id}", response=List[ReplyRuleSchemaOut], summary="获取指定账号的所有规则")
def list_rule_by_account(request, account_id: str):
    """按账号取全部规则（不分页，供前端按账号展示）"""
    return ReplyRule.objects.filter(account_id=account_id).order_by('-priority', '-sys_create_datetime')


@router.get("/rule/{rule_id}", response=ReplyRuleSchemaOut, summary="获取规则详情")
def get_rule(request, rule_id: str):
    return get_object_or_404(ReplyRule, id=rule_id)


@router.post("/rule", response=ReplyRuleSchemaOut, summary="创建回复规则")
def create_rule(request, data: ReplyRuleSchemaIn):
    payload = _normalize_rule_payload(data.dict())
    _validate_refs(payload)
    _validate_rule_payload(payload)
    return create(request, payload, ReplyRule)


@router.put("/rule/{rule_id}", response=ReplyRuleSchemaOut, summary="更新规则（完全替换）")
def update_rule(request, rule_id: str, data: ReplyRuleSchemaIn):
    rule = get_object_or_404(ReplyRule, id=rule_id)
    payload = _normalize_rule_payload(data.dict())
    _validate_refs(payload)
    _validate_rule_payload(payload)
    for attr, value in payload.items():
        setattr(rule, attr, value)
    rule.save()
    return rule


@router.patch("/rule/{rule_id}", response=ReplyRuleSchemaOut, summary="部分更新规则")
def patch_rule(request, rule_id: str, data: ReplyRuleSchemaPatch):
    rule = get_object_or_404(ReplyRule, id=rule_id)
    updates = _normalize_rule_payload(data.dict(exclude_unset=True))
    merged = {
        'match_type': updates.get('match_type', rule.match_type),
        'keywords': updates.get('keywords', rule.keywords),
        'regex_pattern': updates.get('regex_pattern', rule.regex_pattern),
    }
    if updates.get('template_id') and not ReplyTemplate.objects.filter(id=updates['template_id']).exists():
        raise HttpError(400, "引用模板不存在")
    _validate_rule_payload(merged)
    for attr, value in updates.items():
        setattr(rule, attr, value)
    rule.save()
    return rule


@router.delete("/rule/{rule_id}", response=ReplyRuleSchemaOut, summary="删除规则")
def delete_rule(request, rule_id: str):
    return delete(rule_id, ReplyRule)


@router.post("/rule/batch/delete", response=ReplyRuleBatchDeleteOut, summary="批量删除规则")
def batch_delete_rule(request, data: ReplyRuleBatchDeleteIn):
    count = ReplyRule.objects.filter(id__in=data.ids).delete()[0]
    return ReplyRuleBatchDeleteOut(count=count)
