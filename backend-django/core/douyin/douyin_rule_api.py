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
from core.douyin.douyin_template_model import DouyinTemplate
from core.douyin.douyin_rule_schema import (
    DouyinRuleBatchDeleteIn,
    DouyinRuleBatchDeleteOut,
    DouyinRuleFilters,
    DouyinRuleSchemaIn,
    DouyinRuleSchemaOut,
    DouyinRuleSchemaPatch,
    DouyinRuleQuickEnableIn,
    DouyinRuleQuickEnableOut,
)

router = Router()
_QUICK_RULE_REMARK = "__quick_stranger_auto_reply__"


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
    if 'links' in normalized:
        normalized['links'] = _normalize_links(normalized.get('links'))
    links = normalized.get('links') or []
    if links and normalized.get('send_mode') in (None, '', 'merged'):
        normalized['send_mode'] = 'multi_message'
    if normalized.get('cooldown_seconds') is None:
        normalized['cooldown_seconds'] = 300
    elif int(normalized.get('cooldown_seconds') or 0) < 0:
        normalized['cooldown_seconds'] = 0
    return normalized


def _normalize_links(links) -> list[dict[str, str]]:
    """统一 links 为 [{title, url}, ...]（兼容纯 URL 字符串）。"""
    if not links:
        return []
    out: list[dict[str, str]] = []
    for item in links:
        if isinstance(item, str):
            url = item.strip()
            if url:
                out.append({'title': url, 'url': url})
            continue
        if isinstance(item, dict):
            url = str(item.get('url') or '').strip()
            if not url:
                continue
            title = str(item.get('title') or url).strip()
            out.append({'title': title, 'url': url})
            continue
        # Pydantic Schema 对象
        url = str(getattr(item, 'url', '') or '').strip()
        if url:
            title = str(getattr(item, 'title', '') or url).strip()
            out.append({'title': title, 'url': url})
    return out


def _normalize_keywords(keywords: list[str] | None) -> list[str]:
    return [
        item.strip()
        for item in (keywords or [])
        if isinstance(item, str) and item.strip()
    ]


def _build_quick_enable_rule_payload(data: DouyinRuleQuickEnableIn) -> dict:
    keywords = _normalize_keywords(data.keywords)
    match_type = 'contains' if keywords else 'default'
    return {
        'account_id': data.account_id,
        'name': "陌生人自动回复（快捷）" if not keywords else "关键词自动回复（快捷）",
        'match_type': match_type,
        'keywords': keywords,
        'regex_pattern': None,
        'reply_text': (data.reply_text or '').strip(),
        'links': [],
        'send_mode': data.send_mode,
        'priority': 0,
        'status': True,
        'cooldown_seconds': max(0, int(data.cooldown_seconds or 0)),
        'remark': _QUICK_RULE_REMARK,
        'template_id': None,
        'channel': 'dm',
        'weekday_mask': '1111111',
    }


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
    payload = _normalize_rule_payload(data.dict())
    if payload.get('account_id') and not DouyinAccount.objects.filter(id=payload['account_id']).exists():
        raise HttpError(400, "所属抖音账号不存在")
    if payload.get('template_id') and not DouyinTemplate.objects.filter(id=payload['template_id']).exists():
        raise HttpError(400, "引用模板不存在")
    _validate_rule_payload(payload)
    return create(request, payload, DouyinRule)


@router.put("/rule/{rule_id}", response=DouyinRuleSchemaOut, summary="更新规则（完全替换）")
def update_rule(request, rule_id: str, data: DouyinRuleSchemaIn):
    rule = get_object_or_404(DouyinRule, id=rule_id)
    payload = _normalize_rule_payload(data.dict())
    if payload.get('account_id') and not DouyinAccount.objects.filter(id=payload['account_id']).exists():
        raise HttpError(400, "所属抖音账号不存在")
    if payload.get('template_id') and not DouyinTemplate.objects.filter(id=payload['template_id']).exists():
        raise HttpError(400, "引用模板不存在")
    _validate_rule_payload(payload)
    for attr, value in payload.items():
        setattr(rule, attr, value)
    rule.save()
    return rule


@router.patch("/rule/{rule_id}", response=DouyinRuleSchemaOut, summary="部分更新规则")
def patch_rule(request, rule_id: str, data: DouyinRuleSchemaPatch):
    rule = get_object_or_404(DouyinRule, id=rule_id)
    updates = _normalize_rule_payload(data.dict(exclude_unset=True))
    merged = {
        'match_type': updates.get('match_type', rule.match_type),
        'keywords': updates.get('keywords', rule.keywords),
        'regex_pattern': updates.get('regex_pattern', rule.regex_pattern),
    }
    if updates.get('template_id') and not DouyinTemplate.objects.filter(id=updates['template_id']).exists():
        raise HttpError(400, "引用模板不存在")
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


@router.post(
    "/rule/quick-enable",
    response=DouyinRuleQuickEnableOut,
    summary="一键开启陌生人消息自动回复",
)
def quick_enable_rule(request, data: DouyinRuleQuickEnableIn):
    account = DouyinAccount.objects.filter(id=data.account_id).first()
    if not account:
        raise HttpError(400, "所属抖音账号不存在")

    reply_text = (data.reply_text or "").strip()
    if not reply_text:
        raise HttpError(400, "回复文案不能为空")

    if data.send_mode not in ("merged", "multi_message", "card_fallback"):
        raise HttpError(400, "send_mode 仅支持 merged/multi_message/card_fallback")

    payload = _build_quick_enable_rule_payload(data)

    rule = DouyinRule.objects.filter(
        account_id=data.account_id,
        remark=_QUICK_RULE_REMARK,
        is_deleted=False,
    ).first()

    created = False
    if not rule:
        created = True
        rule = DouyinRule(**payload)
    else:
        for attr, value in payload.items():
            setattr(rule, attr, value)
    rule.save()

    return DouyinRuleQuickEnableOut(
        created=created,
        message="已开启自动回复" if not payload['keywords'] else "已开启关键词自动回复",
        rule_id=str(rule.id),
    )


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
