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

    from core.douyin.runtime.credential import has_send_credential, merge_storage_state
    from core.douyin.runtime.storage import load_storage_state, save_storage_state

    account = get_object_or_404(DouyinAccount, id=account_id)
    base_state = load_storage_state(str(account_id))
    try:
        state = merge_storage_state(
            base_state,
            data.cookie or "",
            web_protect=data.web_protect or "",
            keys=data.keys or "",
        )
    except ValueError as e:
        raise HttpError(400, f"凭证解析失败：{e}")

    cookies = {c["name"]: c["value"] for c in state.get("cookies", [])}
    if not ({"sessionid", "sessionid_ss"} & cookies.keys()):
        raise HttpError(400, "Cookie 缺少 sessionid，请确认已登录抖音后再从浏览器复制完整 Cookie")

    account.storage_state_path = save_storage_state(str(account_id), state)
    account.status = 1  # 在线
    account.last_login_at = timezone.now()
    if data.nickname:
        account.nickname = data.nickname
    if data.user_agent:
        account.user_agent = data.user_agent
    account.save()

    can_send = has_send_credential(state)
    msg = (
        "登录态已导入：可监控与发送私信。"
        if can_send
        else "登录态已导入：可监控/接收；发送私信还需补 web_protect 与 keys（bd-ticket-guard）。"
    )
    return DouyinAccountActionOut(success=True, message=msg)


@router.post(
    "/account/{account_id}/login",
    response=DouyinAccountActionOut,
    summary="触发扫码登录",
)
def trigger_login(request, account_id: str):
    """
    触发扫码登录：
      1. 把账号置为"未登录"态
      2. 通过 Redis pubsub 向 worker 进程发送登录命令
      3. 前端需连接 WebSocket `/ws/douyin/` 以接收 qr_image / login_success 事件
    若 Redis 不可用则只改 DB 状态（worker 启动时会自动轮询到本账号也能登录）。
    """
    account = get_object_or_404(DouyinAccount, id=account_id)
    if account.status == 3:
        raise HttpError(400, "该账号已禁用，无法登录")
    account.status = 0
    account.storage_state_path = ''
    account.pending_verification_type = None
    account.pending_verification_at = None
    account.pending_verification_until = None
    account.save(update_fields=[
        'status',
        'storage_state_path',
        'pending_verification_type',
        'pending_verification_at',
        'pending_verification_until',
        'sys_update_datetime',
    ])

    ok = command_publisher.send_login(str(account_id))
    if ok:
        msg = "已下发扫码登录指令，请在前端扫码弹窗中用抖音 APP 扫描二维码"
    else:
        msg = "已标记账号待登录。Redis 不可用，worker 会在下次轮询到后执行登录"
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


@router.post(
    "/account/{account_id}/login/cancel",
    response=DouyinAccountActionOut,
    summary="取消扫码登录",
)
def cancel_login(request, account_id: str):
    """取消当前账号的扫码登录流程（若存在）"""
    account = get_object_or_404(DouyinAccount, id=account_id)
    ok = command_publisher.send_cancel_login(str(account_id))
    if ok:
        msg = f"已请求取消账号 {account.nickname} 的扫码登录流程"
    else:
        msg = f"Redis 不可用，未能取消账号 {account.nickname} 的扫码登录流程"
    return DouyinAccountActionOut(success=ok, message=msg)


@router.post(
    "/account/{account_id}/focus",
    response=DouyinAccountActionOut,
    summary="聚焦抖音账号监管页",
)
def focus_account(request, account_id: str):
    account = get_object_or_404(DouyinAccount, id=account_id)
    # 状态守卫：未登录账号不允许聚焦监管页。
    # 否则会让 worker 用残留 user_data_dir + cookies 拉起一个看似登录的 chromium 窗口，
    # 表现为"刚登出，点监管页又看到已登录页"。前端看到的是 success=False 提示。
    if int(account.status or 0) != 1:
        return DouyinAccountActionOut(
            success=False,
            message=f"账号 {account.nickname} 当前未登录，请先扫码登录后再聚焦监管页",
        )
    ok = command_publisher.send_focus_account(str(account_id))
    if ok:
        msg = f"已请求聚焦账号 {account.nickname} 的监管页"
    else:
        msg = f"Redis 不可用，无法立即聚焦账号 {account.nickname} 的监管页"
    return DouyinAccountActionOut(success=ok, message=msg)
