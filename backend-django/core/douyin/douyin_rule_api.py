#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: douyin_rule_api.py
@Desc: Douyin Rule API - 抖音回复规则管理接口
"""
import json
from typing import List

from django.db import transaction
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
    DryRunMatchIn,
    DryRunMatchOut,
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
    if links:
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
                out.append({'title': '', 'url': url})
            continue
        if isinstance(item, dict):
            url = str(item.get('url') or '').strip()
            if not url:
                continue
            title = str(item.get('title') or '').strip()
            if title == url:
                title = ''
            out.append({'title': title, 'url': url})
            continue
        # Pydantic Schema 对象
        url = str(getattr(item, 'url', '') or '').strip()
        if url:
            title = str(getattr(item, 'title', '') or '').strip()
            if title == url:
                title = ''
            out.append({'title': title, 'url': url})
    return out


def _normalize_keywords(keywords: list[str] | None) -> list[str]:
    return [
        item.strip()
        for item in (keywords or [])
        if isinstance(item, str) and item.strip()
    ]


def _normalize_account_ids(account_ids, legacy_account_id=None) -> list[str]:
    """规整账号 ID 列表：去空、去重、保序；兼容旧单账号字段。"""
    raw = account_ids if account_ids is not None else None
    if raw is None:
        # 未传 account_ids，回退到旧单账号字段
        raw = [legacy_account_id] if legacy_account_id else []
    seen: set[str] = set()
    out: list[str] = []
    for item in raw:
        s = str(item or '').strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _validate_accounts_exist(account_ids: list[str]) -> None:
    if not account_ids:
        return
    existing = set(
        str(x) for x in DouyinAccount.objects.filter(id__in=account_ids).values_list('id', flat=True)
    )
    missing = [a for a in account_ids if a not in existing]
    if missing:
        raise HttpError(400, f"以下账号不存在：{', '.join(missing)}")


def _find_account_conflicts(account_ids: list[str], exclude_id: str | None):
    """返回 {account_id: (rule_id, rule_name)}，表示这些账号已被其他规则绑定。

    SQLite 不支持 JSONField__contains，故在 Python 侧扫描。
    """
    if not account_ids:
        return {}
    target = set(account_ids)
    conflicts: dict[str, tuple[str, str]] = {}
    qs = DouyinRule.objects.all()
    if exclude_id:
        qs = qs.exclude(id=exclude_id)
    for rule in qs.only('id', 'name', 'account_ids').iterator():
        for aid in (rule.account_ids or []):
            aid = str(aid)
            if aid in target and aid not in conflicts:
                conflicts[aid] = (str(rule.id), rule.name)
    return conflicts


def _resolve_account_conflicts(account_ids: list[str], exclude_id: str | None, force_move: bool) -> None:
    """校验账号唯一性。冲突且 force_move=False → 409；force_move=True → 从旧规则移除。"""
    conflicts = _find_account_conflicts(account_ids, exclude_id)
    if not conflicts:
        return
    if not force_move:
        nick_map = {
            str(a.id): a.nickname
            for a in DouyinAccount.objects.filter(id__in=list(conflicts.keys()))
        }
        detail = [
            {
                'account_id': aid,
                'account_nickname': nick_map.get(aid, aid),
                'rule_id': rid,
                'rule_name': rname,
            }
            for aid, (rid, rname) in conflicts.items()
        ]
        raise HttpError(409, json.dumps({'code': 'account_conflict', 'conflicts': detail}, ensure_ascii=False))
    # force_move：从冲突规则里移除这些账号
    move_set = set(account_ids)
    rule_ids = {rid for (rid, _name) in conflicts.values()}
    for rule in DouyinRule.objects.filter(id__in=rule_ids):
        rule.account_ids = [str(a) for a in (rule.account_ids or []) if str(a) not in move_set]
        rule.save(update_fields=['account_ids'])


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
    """按账号取规则（不分页，供前端按账号展示）：含显式绑定该账号的规则 + 全局规则。

    SQLite 不支持 JSONField__contains，故 Python 侧过滤。
    """
    aid = str(account_id)
    all_rules = list(
        DouyinRule.objects.all().order_by('-priority', '-sys_create_datetime')
    )
    bound = [r for r in all_rules if aid in [str(x) for x in (r.account_ids or [])]]
    glob = [r for r in all_rules if not (r.account_ids or [])]
    return bound + glob


@router.post(
    "/rule/dry-run-match",
    response=DryRunMatchOut,
    summary="规则匹配测试（Dry-run，不产生实际回复）",
)
def dry_run_match(request, data: DryRunMatchIn):
    from datetime import datetime
    from core.douyin.runtime.matcher import match as rule_match, _rule_effective

    text = (data.text or "").strip()
    if not text:
        raise HttpError(400, "测试文本不能为空")

    all_enabled = list(
        DouyinRule.objects.filter(status=True)
        .order_by('-priority', '-sys_create_datetime')
        .select_related('template')
    )
    if data.account_id:
        aid = str(data.account_id)
        bound = [r for r in all_enabled if aid in [str(x) for x in (r.account_ids or [])]]
        glob = [r for r in all_enabled if not (r.account_ids or [])]
        # 账号专属规则在前（最高优先级），全局规则兜底
        rules = bound + glob
    else:
        rules = [r for r in all_enabled if not (r.account_ids or [])]

    at = datetime.now()
    miss_reasons: list[str] = []

    if not rules:
        miss_reasons.append("当前账号（或全局）无已启用规则")
        return DryRunMatchOut(matched=False, miss_reasons=miss_reasons)

    inactive_rules = [r for r in rules if not _rule_effective(r, data.channel, at)]
    for r in inactive_rules:
        miss_reasons.append(f"规则「{r.name}」因时段/星期限制暂未生效")

    hit = rule_match(text, rules, incoming_channel=data.channel, at=at)
    if hit is None:
        miss_reasons.append("消息文本未命中任何关键词规则，且无兜底规则")
        return DryRunMatchOut(matched=False, miss_reasons=miss_reasons)

    reply_preview = hit.reply_text or ""
    if hit.template_id and hit.template:
        reply_preview = hit.template.content or hit.reply_text or ""

    return DryRunMatchOut(
        matched=True,
        rule_id=str(hit.id),
        rule_name=hit.name,
        match_type=hit.match_type,
        reply_preview=reply_preview,
        miss_reasons=miss_reasons,
    )


@router.get("/rule/{rule_id}", response=DouyinRuleSchemaOut, summary="获取规则详情")
def get_rule(request, rule_id: str):
    return get_object_or_404(DouyinRule, id=rule_id)


@router.post("/rule", response=DouyinRuleSchemaOut, summary="创建回复规则")
@transaction.atomic
def create_rule(request, data: DouyinRuleSchemaIn):
    payload = _normalize_rule_payload(data.dict())
    force_move = bool(payload.pop('force_move', False))
    account_ids = _normalize_account_ids(payload.pop('account_ids', None), payload.get('account_id'))
    if payload.get('template_id') and not DouyinTemplate.objects.filter(id=payload['template_id']).exists():
        raise HttpError(400, "引用模板不存在")
    _validate_rule_payload(payload)
    _validate_accounts_exist(account_ids)
    _resolve_account_conflicts(account_ids, exclude_id=None, force_move=force_move)
    payload['account_ids'] = account_ids
    payload['account_id'] = account_ids[0] if account_ids else None
    return create(request, payload, DouyinRule)


@router.put("/rule/{rule_id}", response=DouyinRuleSchemaOut, summary="更新规则（完全替换）")
@transaction.atomic
def update_rule(request, rule_id: str, data: DouyinRuleSchemaIn):
    rule = get_object_or_404(DouyinRule, id=rule_id)
    payload = _normalize_rule_payload(data.dict())
    force_move = bool(payload.pop('force_move', False))
    account_ids = _normalize_account_ids(payload.pop('account_ids', None), payload.get('account_id'))
    if payload.get('template_id') and not DouyinTemplate.objects.filter(id=payload['template_id']).exists():
        raise HttpError(400, "引用模板不存在")
    _validate_rule_payload(payload)
    _validate_accounts_exist(account_ids)
    _resolve_account_conflicts(account_ids, exclude_id=rule_id, force_move=force_move)
    payload['account_ids'] = account_ids
    payload['account_id'] = account_ids[0] if account_ids else None
    for attr, value in payload.items():
        setattr(rule, attr, value)
    rule.save()
    return rule


@router.patch("/rule/{rule_id}", response=DouyinRuleSchemaOut, summary="部分更新规则")
@transaction.atomic
def patch_rule(request, rule_id: str, data: DouyinRuleSchemaPatch):
    rule = get_object_or_404(DouyinRule, id=rule_id)
    raw = data.dict(exclude_unset=True)
    force_move = bool(raw.pop('force_move', False))
    has_account_ids = 'account_ids' in raw
    account_ids = _normalize_account_ids(raw.pop('account_ids', None)) if has_account_ids else None
    updates = _normalize_rule_payload(raw)
    # _normalize_rule_payload 会注入 account_id/template_id 默认值，patch 下需剔除未显式传入的键
    if 'account_id' not in raw:
        updates.pop('account_id', None)
    if 'template_id' not in raw:
        updates.pop('template_id', None)
    merged = {
        'match_type': updates.get('match_type', rule.match_type),
        'keywords': updates.get('keywords', rule.keywords),
        'regex_pattern': updates.get('regex_pattern', rule.regex_pattern),
    }
    if updates.get('template_id') and not DouyinTemplate.objects.filter(id=updates['template_id']).exists():
        raise HttpError(400, "引用模板不存在")
    _validate_rule_payload(merged)
    if has_account_ids:
        _validate_accounts_exist(account_ids)
        _resolve_account_conflicts(account_ids, exclude_id=rule_id, force_move=force_move)
        rule.account_ids = account_ids
        rule.account_id = account_ids[0] if account_ids else None
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


@router.post(
    "/rule/{rule_id}/clone",
    response=DouyinRuleSchemaOut,
    summary="克隆规则（创建副本）",
)
def clone_rule(request, rule_id: str):
    src = get_object_or_404(DouyinRule, id=rule_id)
    src.pk = None
    src.id = None
    src.name = f"{src.name}（副本）"
    src.status = False
    src.save()
    return src


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
