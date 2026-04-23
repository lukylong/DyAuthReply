#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: douyin_rule_api.py
@Desc: Douyin Rule API - 抖音回复规则管理接口
"""
from typing import List

from django.shortcuts import get_object_or_404
from ninja import Query, Router
from ninja.errors import HttpError
from ninja.pagination import paginate

from common.fu_crud import create, delete, retrieve
from common.fu_pagination import MyPagination
from core.douyin.douyin_account_model import DouyinAccount
from core.douyin.douyin_rule_model import DouyinRule
from core.douyin.douyin_rule_schema import (
    DouyinRuleBatchDeleteIn,
    DouyinRuleBatchDeleteOut,
    DouyinRuleFilters,
    DouyinRuleSchemaIn,
    DouyinRuleSchemaOut,
    DouyinRuleSchemaPatch,
)

router = Router()


@router.get("/rule", response=List[DouyinRuleSchemaOut], summary="获取回复规则列表（分页）")
@paginate(MyPagination)
def list_rule(request, filters: DouyinRuleFilters = Query(...)):
    return retrieve(request, DouyinRule, filters)


@router.get("/rule/by/account/{account_id}", response=List[DouyinRuleSchemaOut], summary="获取指定账号的所有规则")
def list_rule_by_account(request, account_id: str):
    """按账号取全部规则（不分页，供前端按账号展示）"""
    return DouyinRule.objects.filter(account_id=account_id).order_by('-priority', '-sys_create_datetime')


@router.get("/rule/{rule_id}", response=DouyinRuleSchemaOut, summary="获取规则详情")
def get_rule(request, rule_id: str):
    return get_object_or_404(DouyinRule, id=rule_id)


@router.post("/rule", response=DouyinRuleSchemaOut, summary="创建回复规则")
def create_rule(request, data: DouyinRuleSchemaIn):
    payload = data.dict()
    if not DouyinAccount.objects.filter(id=payload['account_id']).exists():
        raise HttpError(400, "所属抖音账号不存在")
    _validate_rule_payload(payload)
    return create(request, payload, DouyinRule)


@router.put("/rule/{rule_id}", response=DouyinRuleSchemaOut, summary="更新规则（完全替换）")
def update_rule(request, rule_id: str, data: DouyinRuleSchemaIn):
    rule = get_object_or_404(DouyinRule, id=rule_id)
    payload = data.dict()
    _validate_rule_payload(payload)
    for attr, value in payload.items():
        setattr(rule, attr, value)
    rule.save()
    return rule


@router.patch("/rule/{rule_id}", response=DouyinRuleSchemaOut, summary="部分更新规则")
def patch_rule(request, rule_id: str, data: DouyinRuleSchemaPatch):
    rule = get_object_or_404(DouyinRule, id=rule_id)
    updates = data.dict(exclude_unset=True)
    merged = {
        'match_type': updates.get('match_type', rule.match_type),
        'keywords': updates.get('keywords', rule.keywords),
        'regex_pattern': updates.get('regex_pattern', rule.regex_pattern),
    }
    _validate_rule_payload(merged)
    for attr, value in updates.items():
        setattr(rule, attr, value)
    rule.save()
    return rule


@router.delete("/rule/{rule_id}", response=DouyinRuleSchemaOut, summary="删除规则")
def delete_rule(request, rule_id: str):
    return delete(rule_id, DouyinRule)


@router.post(
    "/rule/batch/delete",
    response=DouyinRuleBatchDeleteOut,
    summary="批量删除规则",
)
def batch_delete_rule(request, data: DouyinRuleBatchDeleteIn):
    count = DouyinRule.objects.filter(id__in=data.ids).delete()[0]
    return DouyinRuleBatchDeleteOut(count=count)


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
