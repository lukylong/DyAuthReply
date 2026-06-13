#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: douyin_account_api.py
@Desc: Douyin Account API - 抖音账号管理接口
        提供账号 CRUD、批量删除、登录/登出触发等操作。登录/登出触发只是写入指令，
        真正的浏览器动作由独立的 douyin worker 进程消费执行（M2 里程碑）。
"""
from typing import List

from django.shortcuts import get_object_or_404
from ninja import Query, Router
from ninja.errors import HttpError
from ninja.pagination import paginate

from common.fu_crud import create, delete, retrieve
from common.fu_pagination import MyPagination
from core.douyin.douyin_account_model import DouyinAccount
from core.douyin.runtime import command_publisher
from core.douyin.douyin_account_schema import (
    DouyinAccountActionOut,
    DouyinAccountBatchDeleteIn,
    DouyinAccountBatchDeleteOut,
    DouyinAccountFilters,
    DouyinAccountSchemaIn,
    DouyinAccountSchemaOut,
    DouyinAccountSchemaPatch,
    DouyinAccountSimpleOut,
    DouyinCredentialImportIn,
)
# 导入消息回复模块需要的 schema
from core.douyin.douyin_session_schema import (
    DouyinConversationItemOut,
    DouyinManualReplyIn,
    DouyinMessageItemOut,
    DouyinSessionControlOut,
)

router = Router()


@router.get("/account", response=List[DouyinAccountSchemaOut], summary="获取抖音账号列表（分页）")
@paginate(MyPagination)
def list_douyin_account(request, filters: DouyinAccountFilters = Query(...)):
    """分页查询抖音账号列表"""
    return retrieve(request, DouyinAccount, filters)


@router.get("/account/all", response=List[DouyinAccountSimpleOut], summary="获取所有抖音账号（简化版）")
def list_all_douyin_account(request):
    """返回所有未禁用账号，供前端下拉选择"""
    return DouyinAccount.objects.exclude(status=3).order_by('-sort', '-sys_create_datetime')


@router.get("/account/{account_id}", response=DouyinAccountSchemaOut, summary="获取抖音账号详情")
def get_douyin_account(request, account_id: str):
    return get_object_or_404(DouyinAccount, id=account_id)


@router.post("/account", response=DouyinAccountSchemaOut, summary="创建抖音账号")
def create_douyin_account(request, data: DouyinAccountSchemaIn):
    """创建抖音账号。若 owner_id 未传则默认为当前登录用户。"""
    payload = data.dict()
    if not payload.get('owner_id'):
        payload['owner_id'] = request.auth.id
    if DouyinAccount.objects.filter(nickname=payload['nickname'], owner_id=payload['owner_id']).exists():
        raise HttpError(400, f"同一用户下已存在昵称为 {payload['nickname']} 的账号")
    return create(request, payload, DouyinAccount)


@router.put("/account/{account_id}", response=DouyinAccountSchemaOut, summary="更新抖音账号（完全替换）")
def update_douyin_account(request, account_id: str, data: DouyinAccountSchemaIn):
    account = get_object_or_404(DouyinAccount, id=account_id)
    payload = data.dict()
    for attr, value in payload.items():
        if value is None and attr == 'owner_id':
            continue
        setattr(account, attr, value)
    account.save()
    return account


@router.patch("/account/{account_id}", response=DouyinAccountSchemaOut, summary="部分更新抖音账号")
def patch_douyin_account(request, account_id: str, data: DouyinAccountSchemaPatch):
    account = get_object_or_404(DouyinAccount, id=account_id)
    for attr, value in data.dict(exclude_unset=True).items():
        setattr(account, attr, value)
    account.save()
    return account


@router.delete("/account/{account_id}", response=DouyinAccountSchemaOut, summary="删除抖音账号")
def delete_douyin_account(request, account_id: str):
    """删除账号：在线账号需先登出/禁用"""
    account = get_object_or_404(DouyinAccount, id=account_id)
    if account.is_online():
        raise HttpError(400, "在线账号无法直接删除，请先登出或禁用")
    return delete(account_id, DouyinAccount)


@router.post(
    "/account/batch/delete",
    response=DouyinAccountBatchDeleteOut,
    summary="批量删除抖音账号",
)
def batch_delete_douyin_account(request, data: DouyinAccountBatchDeleteIn):
    failed_ids: List[str] = []
    success = 0
    for aid in data.ids:
        try:
            acc = DouyinAccount.objects.get(id=aid)
            if acc.is_online():
                failed_ids.append(aid)
                continue
            acc.delete()
            success += 1
        except DouyinAccount.DoesNotExist:
            failed_ids.append(aid)
    return DouyinAccountBatchDeleteOut(count=success, failed_ids=failed_ids)


@router.post(
    "/account/{account_id}/import-credential",
    response=DouyinAccountActionOut,
    summary="导入登录态（粘贴 Cookie，替代扫码登录）",
)
def import_credential(request, account_id: str, data: DouyinCredentialImportIn):
    """粘贴 Cookie 录入登录态（去浏览器扫码），支持分步增量补充。

    - cookie：首次导入必填（监控/接收 + 发送都需要）；之后补凭据时可留空，
      留空则复用已导入的 Cookie。
    - web_protect / keys 选填：bd-ticket-guard 凭证，仅「发送私信」需要；
      只做消息监控可不填。本次未提供的字段会保留上次导入的旧值（增量合并）。
    """
    from django.utils import timezone

    from core.douyin.runtime.credential import (
        has_send_credential,
        merge_storage_state,
        parse_credential_bundle,
    )
    from core.douyin.runtime.storage import load_storage_state, save_storage_state

    account = get_object_or_404(DouyinAccount, id=account_id)

    # 一键导入串：先展开为 cookie/web_protect/keys/ua，单项字段若显式提供则覆盖（单项优先）。
    cookie = data.cookie or ""
    web_protect = data.web_protect or ""
    keys = data.keys or ""
    user_agent = data.user_agent or ""
    if data.bundle:
        try:
            unpacked = parse_credential_bundle(data.bundle)
        except ValueError as e:
            raise HttpError(400, f"一键导入串解析失败：{e}")
        cookie = cookie or unpacked["cookie"]
        web_protect = web_protect or unpacked["web_protect"]
        keys = keys or unpacked["keys"]
        user_agent = user_agent or unpacked["user_agent"]

    base_state = load_storage_state(str(account_id))
    try:
        state = merge_storage_state(
            base_state,
            cookie,
            web_protect=web_protect,
            keys=keys,
        )
    except ValueError as e:
        raise HttpError(400, f"凭证解析失败：{e}")

    cookies = {c["name"]: c["value"] for c in state.get("cookies", [])}
    if not ({"sessionid", "sessionid_ss"} & cookies.keys()):
        raise HttpError(400, "Cookie 缺少 sessionid，请确认已登录抖音后再从浏览器复制完整 Cookie")

    can_send = has_send_credential(state)
    account.storage_state_path = save_storage_state(str(account_id), state)
    account.status = 1  # 在线
    account.last_login_at = timezone.now()
    # 录入即刷新凭证三态：有 bd-ticket 三要素 → 可发送，否则仅接收；清掉历史失效原因
    account.credential_state = 'sendable' if can_send else 'receive_only'
    account.last_probe_error = None
    if data.nickname:
        account.nickname = data.nickname
    if user_agent:
        account.user_agent = user_agent
    account.save()

    msg = (
        "登录态已导入：可监控与发送私信。"
        if can_send
        else "登录态已导入：可监控/接收；发送私信还需补 web_protect 与 keys（bd-ticket-guard）。"
    )
    return DouyinAccountActionOut(success=True, message=msg)


@router.post(
    "/account/{account_id}/logout",
    response=DouyinAccountActionOut,
    summary="登出抖音账号",
)
def trigger_logout(request, account_id: str):
    """登出：
    1. 清理 storage_state / sec_uid 并置为未登录
    2. 通知 worker 关闭浏览器上下文并删除加密登录态文件

    注意：必须同时清掉 sec_uid，否则下次重新登录时（即便扫的是别的号），
    inbox 的"账号身份核对"会因为 DB 里残留的旧 sec_uid 而错误地把新登录视作"漂移"
    并立即又把账号打下线，形成下线-登录死循环。
    """
    account = get_object_or_404(DouyinAccount, id=account_id)
    account.status = 0
    account.storage_state_path = ''
    account.sec_uid = ''
    account.pending_verification_type = None
    account.pending_verification_at = None
    account.pending_verification_until = None
    account.save(update_fields=[
        'status',
        'storage_state_path',
        'sec_uid',
        'pending_verification_type',
        'pending_verification_at',
        'pending_verification_until',
        'sys_update_datetime',
    ])
    command_publisher.send_logout(str(account_id))
    return DouyinAccountActionOut(success=True, message="登出指令已下发")


# ============ 消息回复模块专用 API（账号级别，不依赖 session）============


@router.get(
    "/account/{account_id}/conversations",
    response=List[DouyinConversationItemOut],
    summary="获取账号的会话列表（消息回复模块）",
)
def list_account_conversations(request, account_id: str):
    """
    获取指定账号的所有会话列表（用于消息回复界面）
    不依赖 session，直接通过 account_id 查询
    """
    from core.douyin.douyin_conversation_model import DouyinConversation

    account = get_object_or_404(DouyinAccount, id=account_id)
    rows = list(
        DouyinConversation.objects
        .filter(account_id=account.id)
        .order_by('-last_message_at', '-sys_create_datetime')[:100]
    )

    # 去重逻辑（与 session API 保持一致）
    deduped: list = []
    seen: set[str] = set()
    for row in rows:
        if row.peer_sec_uid.startswith('fallback_') and row.peer_nickname:
            key = f"nick:{row.peer_nickname}"
        else:
            key = f"uid:{row.peer_sec_uid}"
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)

    return deduped[:50]


@router.get(
    "/account/{account_id}/conversation/{conversation_id}/messages",
    response=List[DouyinMessageItemOut],
    summary="获取会话消息（消息回复模块）",
)
def list_account_messages(request, account_id: str, conversation_id: str):
    """
    获取指定会话的消息列表（用于消息回复界面）
    不依赖 session，直接通过 account_id 和 conversation_id 查询
    """
    from core.douyin.douyin_conversation_model import DouyinConversation
    from core.douyin.douyin_message_model import DouyinMessage

    account = get_object_or_404(DouyinAccount, id=account_id)
    conv = get_object_or_404(
        DouyinConversation,
        id=conversation_id,
        account_id=account.id
    )

    messages = list(
        DouyinMessage.objects
        .filter(conversation_id=conv.id)
        .order_by('received_at', 'sys_create_datetime')[:100]
    )

    # 增强消息数据：添加发送者信息
    result = []
    for msg in messages:
        msg_dict = {
            'id': str(msg.id),
            'direction': msg.direction,
            'content_type': msg.content_type,
            'content': msg.content,
            'received_at': msg.received_at,
            'processed': msg.processed,
        }

        # 根据消息方向填充发送者信息
        if msg.direction == 'in':
            # 对方发来的消息
            msg_dict['sender_name'] = conv.peer_nickname or conv.peer_sec_uid
            msg_dict['sender_avatar'] = conv.peer_avatar
        else:
            # 我方发出的消息
            msg_dict['sender_name'] = account.nickname
            msg_dict['sender_avatar'] = account.avatar

        result.append(msg_dict)

    return result


@router.post(
    "/account/{account_id}/manual-reply",
    response=DouyinSessionControlOut,
    summary="发送手动回复（消息回复模块）",
)
def send_account_manual_reply(request, account_id: str, data: DouyinManualReplyIn):
    """
    通过账号发送手动回复（用于消息回复界面）
    不依赖 session，直接通过 account_id 操作
    """
    from core.douyin.douyin_conversation_model import DouyinConversation

    account = get_object_or_404(DouyinAccount, id=account_id)
    conv = get_object_or_404(
        DouyinConversation,
        id=data.conversation_id,
        account_id=account.id
    )

    text = (data.text or '').strip()
    if not text:
        return DouyinSessionControlOut(success=False, message="回复内容不能为空")

    ok = command_publisher.send_manual_reply(str(account.id), str(conv.id), text)
    if not ok:
        return DouyinSessionControlOut(
            success=False,
            message="Redis 不可用，未能下发手动回复指令"
        )

    return DouyinSessionControlOut(success=True, message="手动回复指令已下发")
