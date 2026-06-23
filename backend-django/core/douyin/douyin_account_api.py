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
    CheckCredentialIn,
    CheckCredentialOut,
    CredentialStatusOut,
    DouyinAccountActionOut,
    DouyinAccountBatchDeleteIn,
    DouyinAccountBatchDeleteOut,
    DouyinAccountFilters,
    DouyinAccountSchemaIn,
    DouyinAccountSchemaOut,
    DouyinAccountSchemaPatch,
    DouyinAccountSimpleOut,
    DouyinCredentialImportIn,
    QuickCreateAccountIn,
)
# 导入消息回复模块需要的 schema
from core.douyin.douyin_session_schema import (
    DouyinConversationListOut,
    DouyinManualReplyIn,
    DouyinMessageItemOut,
    DouyinSessionControlOut,
    DouyinWorkerCommandStatusOut,
)

router = Router()


def _delete_douyin_account_with_storage(account: DouyinAccount) -> None:
    """删除账号并清理加密登录态 / scan cursor，避免 quick-create 失败留孤儿 .bin。"""
    from core.douyin.runtime.storage import delete_account_runtime_state

    account_id = str(account.id)
    account.delete()
    delete_account_runtime_state(account_id)


def _apply_fetched_profile(
    account: DouyinAccount,
    profile: dict,
    *,
    nickname_override: bool = True,
) -> None:
    """把 fetch_self_profile 结果写回账号；sec_uid 冲突时抛 409。"""
    account_id = str(account.id)
    sec_uid = (profile.get("sec_uid") or "").strip()
    if sec_uid:
        conflict = DouyinAccount.objects.filter(sec_uid=sec_uid).exclude(id=account_id).first()
        if conflict:
            raise HttpError(
                409,
                f"该抖音号已被账号「{conflict.nickname}」占用，请勿重复导入同一登录态",
            )
        account.sec_uid = sec_uid

    # 保存 unique_id（抖音号）
    if profile.get("unique_id"):
        account.unique_id = str(profile["unique_id"])

    if nickname_override and profile.get("nickname"):
        account.nickname = str(profile["nickname"])
    if profile.get("avatar"):
        account.avatar = profile["avatar"]


def _fetch_account_profile(account: DouyinAccount, *, timeout_s: float = 25):
    """拉取当前登录账号资料；超时或失败返回 None（不阻断导入）。"""
    import asyncio
    import logging

    from asgiref.sync import async_to_sync
    from core.douyin.runtime.transport.http_protocol import HttpProtocolTransport

    logger = logging.getLogger(__name__)

    async def _run():
        transport = HttpProtocolTransport()
        await transport.start(account)
        try:
            return await asyncio.wait_for(
                transport.fetch_self_profile(account),
                timeout=timeout_s,
            )
        finally:
            await transport.stop(account)

    try:
        return async_to_sync(_run)()
    except asyncio.TimeoutError:
        logger.warning(
            f"[account] 获取账号信息超时 account={account.id} timeout={timeout_s}s"
        )
        return None
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[account] 获取账号信息失败 account={account.id} err={e}")
        return None


def _fetch_account_profile_with_retry(account: DouyinAccount, *, max_retries: int = 3, timeout_s: float = 20):
    """带重试的 profile 拉取，最多尝试 max_retries 次。

    Args:
        account: 账号对象
        max_retries: 最大重试次数（默认 3 次）
        timeout_s: 每次尝试的超时时间（默认 20 秒）

    Returns:
        profile dict 或 None
    """
    import logging
    import time

    logger = logging.getLogger(__name__)

    for attempt in range(max_retries):
        profile = _fetch_account_profile(account, timeout_s=timeout_s)
        if profile:
            if attempt > 0:
                logger.info(
                    f"[account] profile 拉取成功（重试 {attempt} 次后）account={account.id}"
                )
            return profile

        if attempt < max_retries - 1:
            logger.warning(
                f"[account] profile 拉取失败，将重试 account={account.id} "
                f"attempt={attempt + 1}/{max_retries}"
            )
            time.sleep(2)  # 短暂延迟避免立即重试

    logger.warning(
        f"[account] profile 拉取失败（已重试 {max_retries} 次）account={account.id}"
    )
    return None


@router.get("/account", response=List[DouyinAccountSchemaOut], summary="获取抖音账号列表（分页）")
@paginate(MyPagination)
def list_douyin_account(request, filters: DouyinAccountFilters = Query(...)):
    """分页查询抖音账号列表"""
    return retrieve(request, DouyinAccount, filters)


@router.get("/account/all", response=List[DouyinAccountSimpleOut], summary="获取所有抖音账号（简化版）")
def list_all_douyin_account(request):
    """返回所有未禁用账号，供前端下拉选择"""
    return DouyinAccount.objects.exclude(status=3).order_by('-sort', '-sys_create_datetime')


@router.get("/account/credential-status", response=CredentialStatusOut, summary="获取所有账号的凭证状态")
def get_credential_status(request):
    """
    获取所有账号的凭证状态，用于 Cookie 管理页面

    返回：
    - accounts: 所有账号的凭证状态列表
    - duplicates: 重复的 sec_uid（同一个抖音号导入到多个账号）
    """
    from collections import defaultdict
    from core.douyin.runtime.storage import load_storage_state
    from core.douyin.runtime.credential import has_send_credential
    from core.douyin.douyin_account_schema import CredentialStatusItem

    accounts = DouyinAccount.objects.all().order_by('-last_login_at', '-sys_create_datetime')

    # 收集账号信息
    account_list = []
    sec_uid_map = defaultdict(list)  # sec_uid -> [account_id, ...]

    for acc in accounts:
        # 检查凭证文件是否存在
        storage_exists = False
        has_send = False
        if acc.storage_state_path:
            state = load_storage_state(str(acc.id))
            storage_exists = state is not None
            if state:
                has_send = has_send_credential(state)

        item = CredentialStatusItem(
            id=str(acc.id),
            nickname=acc.nickname,
            sec_uid=acc.sec_uid,
            avatar=acc.avatar,
            credential_state=acc.credential_state,
            last_login_at=acc.last_login_at.isoformat() if acc.last_login_at else None,
            storage_state_exists=storage_exists,
            has_send_credential=has_send,
            status=acc.status,
        )
        account_list.append(item)

        # 收集 sec_uid 用于重复检测
        if acc.sec_uid:
            sec_uid_map[acc.sec_uid].append(str(acc.id))

    # 找出重复的 sec_uid（一个 sec_uid 对应多个账号）
    duplicates = {
        sec_uid: account_ids
        for sec_uid, account_ids in sec_uid_map.items()
        if len(account_ids) > 1
    }

    return CredentialStatusOut(
        accounts=account_list,
        duplicates=duplicates,
    )


@router.post("/account/quick-create", response=DouyinAccountSchemaOut, summary="一步创建账号（导入Cookie+自动获取信息）")
def quick_create_account(request, data: QuickCreateAccountIn):
    """
    一步完成：导入 Cookie + 自动创建账号（昵称从协议获取）

    流程：
    1. 验证至少提供了 bundle 或 cookie
    2. 创建临时账号（nickname 使用临时值）
    3. 调用 import_credential 导入凭证并自动获取真实昵称/头像/sec_uid
    4. sec_uid 冲突时回滚；拉取昵称失败则保留账号（临时昵称，可稍后编辑）
    """
    import logging
    import uuid
    from django.utils import timezone

    from core.douyin.runtime.credential import (
        find_duplicate_session_owner,
        format_duplicate_session_error,
        has_send_credential,
        merge_storage_state,
        parse_credential_bundle,
    )
    from core.douyin.runtime.storage import load_storage_state, save_storage_state

    logger = logging.getLogger(__name__)

    # 验证至少提供了凭证
    if not data.bundle and not data.cookie:
        raise HttpError(400, "必须提供 bundle 或 cookie")

    # 步骤 1: 创建临时账号（使用随机昵称避免冲突）
    temp_nickname = f"临时_{uuid.uuid4().hex[:8]}"
    owner_id = request.auth.id

    account = DouyinAccount.objects.create(
        nickname=temp_nickname,
        owner_id=owner_id,
        status=0,  # 未登录
        auto_reply_enabled=data.auto_reply_enabled,
        daily_reply_quota=data.daily_reply_quota,
        min_interval_seconds=data.min_interval_seconds,
        max_interval_seconds=data.max_interval_seconds,
        silent_start=data.silent_start,
        silent_end=data.silent_end,
        remark=data.remark or '',
    )
    account_id = str(account.id)

    # 步骤 2: 解析和导入凭证（复用 import_credential 的逻辑）
    try:
        cookie = data.cookie or ""
        web_protect = data.web_protect or ""
        keys = data.keys or ""
        user_agent = data.user_agent or ""

        # 解析 bundle（如果提供）
        bundle_sec_uid = ""
        bundle_nickname = ""
        bundle_unique_id = ""
        bundle_avatar = ""
        if data.bundle and data.bundle.strip():
            try:
                unpacked = parse_credential_bundle(data.bundle)
                cookie = cookie or unpacked["cookie"]
                web_protect = web_protect or unpacked["web_protect"]
                keys = keys or unpacked["keys"]
                user_agent = user_agent or unpacked["user_agent"]
                bundle_sec_uid = unpacked.get("sec_uid", "")       # 新增
                bundle_nickname = unpacked.get("nickname", "")     # 新增
                bundle_unique_id = unpacked.get("unique_id", "")   # 新增
                bundle_avatar = unpacked.get("avatar", "")         # 新增
            except ValueError as e:
                _delete_douyin_account_with_storage(account)
                raise HttpError(400, f"一键导入串解析失败：{e}")

        base_state = load_storage_state(account_id)
        state = merge_storage_state(
            base_state,
            cookie,
            web_protect=web_protect,
            keys=keys,
        )

        cookies = {c["name"]: c["value"] for c in state.get("cookies", [])}
        if not ({"sessionid", "sessionid_ss"} & cookies.keys()):
            _delete_douyin_account_with_storage(account)
            raise HttpError(400, "Cookie 缺少 sessionid，请确认已登录抖音后再从浏览器复制完整 Cookie")

        # 检查重复
        sessionid = cookies.get("sessionid") or ""
        uid_tt = cookies.get("uid_tt") or ""
        dup = find_duplicate_session_owner(
            account_id=account_id,
            sessionid=sessionid,
            uid_tt=uid_tt,
        )
        if dup:
            other_id, other_name, reason = dup
            _delete_douyin_account_with_storage(account)
            raise HttpError(
                409,
                format_duplicate_session_error(
                    other_name=other_name,
                    reason=reason,
                    sessionid=sessionid,
                    uid_tt=uid_tt,
                ),
            )

        can_send = has_send_credential(state)
        account.storage_state_path = save_storage_state(account_id, state)
        account.status = 1
        account.last_login_at = timezone.now()
        account.credential_state = 'sendable' if can_send else 'receive_only'
        if user_agent:
            account.user_agent = user_agent

        # 步骤 3: 使用 bundle 中的基础信息，但仍尝试拉取 profile 获取头像
        if bundle_sec_uid:
            account.sec_uid = bundle_sec_uid
            if bundle_nickname:
                account.nickname = bundle_nickname
            if bundle_unique_id:
                account.unique_id = bundle_unique_id
            if bundle_avatar:
                account.avatar = bundle_avatar
            logger.info(
                f"[quick_create] 使用 bundle 中的账号信息 "
                f"account={account_id} sec_uid={bundle_sec_uid[:20]} unique_id={bundle_unique_id}"
            )

        # 尝试拉取 profile 获取头像（bundle 中没有 avatar）
        profile = _fetch_account_profile_with_retry(account, max_retries=3, timeout_s=20)
        if profile:
            try:
                _apply_fetched_profile(account, profile, nickname_override=not bundle_nickname and not data.remark)
            except HttpError:
                _delete_douyin_account_with_storage(account)
                raise
        else:
            # profile 拉取失败，推断检测：查找相同 sessionid 的其他账号
            from core.douyin.runtime.storage import load_storage_state
            from core.douyin.runtime.credential import session_fingerprint_from_state

            same_session_accounts = []
            for other_acc in DouyinAccount.objects.exclude(id=account_id).filter(is_deleted=False):
                other_state = load_storage_state(str(other_acc.id))
                if other_state:
                    other_sid, _ = session_fingerprint_from_state(other_state)
                    if other_sid == sessionid and other_acc.sec_uid:
                        same_session_accounts.append(other_acc)

            if same_session_accounts:
                _delete_douyin_account_with_storage(account)
                raise HttpError(
                    409,
                    f"此 Cookie 可能与账号「{same_session_accounts[0].nickname}」重复"
                    f"（基于 sessionid 推断，无法获取账号信息验证）。"
                    f"请确认浏览器中登录的是目标账号后再试。"
                )

            # 没有找到重复，允许导入但使用临时昵称
            if (data.remark or "").strip():
                account.nickname = str(data.remark).strip()[:64]

        account.save()

        logger.info(
            f"[quick_create] 账号创建成功 id={account_id} "
            f"nickname={account.nickname!r} "
            f"sec_uid={(account.sec_uid or '')[:20] or 'N/A'} "
            f"profile={'Y' if profile else 'N'}"
        )

        return account

    except HttpError:
        raise
    except Exception as e:
        logger.exception(f"[quick_create] 创建账号失败 temp_id={account_id}")
        if DouyinAccount.objects.filter(id=account_id).exists():
            try:
                _delete_douyin_account_with_storage(account)
            except Exception:
                pass
        raise HttpError(500, f"创建账号失败：{str(e)}")


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
    import logging
    logger = logging.getLogger(__name__)
    from django.utils import timezone

    from core.douyin.runtime.credential import (
        find_duplicate_session_owner,
        format_duplicate_session_error,
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
    bundle_sec_uid = ""
    bundle_nickname = ""
    bundle_unique_id = ""
    bundle_avatar = ""
    if data.bundle and data.bundle.strip():
        try:
            unpacked = parse_credential_bundle(data.bundle)
        except ValueError as e:
            raise HttpError(400, f"一键导入串解析失败：{e}")
        cookie = cookie or unpacked["cookie"]
        web_protect = web_protect or unpacked["web_protect"]
        keys = keys or unpacked["keys"]
        user_agent = user_agent or unpacked["user_agent"]
        bundle_sec_uid = unpacked.get("sec_uid", "")       # 新增
        bundle_nickname = unpacked.get("nickname", "")     # 新增
        bundle_unique_id = unpacked.get("unique_id", "")   # 新增
        bundle_avatar = unpacked.get("avatar", "")         # 新增

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

    sessionid = cookies.get("sessionid") or ""
    uid_tt = cookies.get("uid_tt") or ""
    dup = find_duplicate_session_owner(
        account_id=str(account_id), sessionid=sessionid, uid_tt=uid_tt,
    )
    if dup:
        other_id, other_name, reason = dup
        raise HttpError(
            409,
            format_duplicate_session_error(
                other_name=other_name,
                reason=reason,
                sessionid=sessionid,
                uid_tt=uid_tt,
            ),
        )

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

    # 使用 bundle 中的基础信息（sec_uid、nickname、unique_id、avatar）
    if bundle_sec_uid:
        account.sec_uid = bundle_sec_uid
        if bundle_nickname and not data.nickname:
            account.nickname = bundle_nickname
        if bundle_unique_id:
            account.unique_id = bundle_unique_id
        if bundle_avatar:
            account.avatar = bundle_avatar
        logger.info(
            f"[import_credential] 使用 bundle 中的账号信息 "
            f"account={account_id} sec_uid={bundle_sec_uid[:20]} unique_id={bundle_unique_id}"
        )

    # 尝试拉取 profile 获取头像（bundle 中没有 avatar，需要通过 API 获取）
    if True:  # 始终尝试获取完整资料（包括头像）
        profile = _fetch_account_profile_with_retry(account, max_retries=3, timeout_s=20)
        if profile:
            try:
                _apply_fetched_profile(
                    account,
                    profile,
                    nickname_override=not data.nickname,
                )
            except HttpError:
                raise
        else:
            # profile 拉取失败，推断检测：查找相同 sessionid 的其他账号
            from core.douyin.runtime.storage import load_storage_state
            from core.douyin.runtime.credential import session_fingerprint_from_state

            same_session_accounts = []
            for other_acc in DouyinAccount.objects.exclude(id=account_id).filter(is_deleted=False):
                other_state = load_storage_state(str(other_acc.id))
                if other_state:
                    other_sid, _ = session_fingerprint_from_state(other_state)
                    if other_sid == sessionid and other_acc.sec_uid:
                        same_session_accounts.append(other_acc)

            if same_session_accounts:
                raise HttpError(
                    409,
                    f"此 Cookie 可能与账号「{same_session_accounts[0].nickname}」重复"
                    f"（基于 sessionid 推断，无法获取账号信息验证）。"
                    f"请确认浏览器中登录的是目标账号后再试。"
                )

    account.save()

    from core.douyin.runtime.command_publisher import send_session_control

    try:
        send_session_control(str(account_id), "restart")
        logger.info(f"[import_credential] 已通知 worker 重启账号协程 account={account_id}")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[import_credential] worker restart 通知失败 account={account_id}: {e}")

    msg = (
        "登录态已导入：可监控与发送私信。"
        if can_send
        else "登录态已导入：可监控/接收；发送私信还需补 web_protect 与 keys（bd-ticket-guard）。"
    )
    return DouyinAccountActionOut(success=True, message=msg)


@router.post(
    "/account/check-credential",
    response={200: "CheckCredentialOut"},
    summary="预检凭证（不保存，用于前端提示）",
)
def check_credential(request, data: "CheckCredentialIn"):
    """预检凭证是否有效、是否重复，但不实际导入。

    用于前端"检查 Cookie"按钮，提前发现问题。
    """
    import logging
    import uuid
    from django.utils import timezone

    from core.douyin.runtime.credential import (
        find_duplicate_session_owner,
        has_send_credential,
        merge_storage_state,
        parse_credential_bundle,
    )
    from core.douyin.runtime.storage import load_storage_state

    logger = logging.getLogger(__name__)

    # 验证至少提供了凭证
    if not data.bundle and not data.cookie:
        return {
            "valid": False,
            "reason": "invalid",
            "conflict_account": None,
            "sec_uid": None,
            "nickname": None,
            "suggestions": ["必须提供 bundle 或 cookie"]
        }

    try:
        cookie = data.cookie or ""
        web_protect = data.web_protect or ""
        keys = data.keys or ""
        bundle_sec_uid = ""
        bundle_nickname = ""

        # 解析 bundle（如果提供）
        if data.bundle and data.bundle.strip():
            try:
                unpacked = parse_credential_bundle(data.bundle)
                cookie = cookie or unpacked["cookie"]
                web_protect = web_protect or unpacked["web_protect"]
                keys = keys or unpacked["keys"]
                bundle_sec_uid = unpacked.get("sec_uid", "")
                bundle_nickname = unpacked.get("nickname", "")
            except ValueError as e:
                return {
                    "valid": False,
                    "reason": "invalid",
                    "conflict_account": None,
                    "sec_uid": None,
                    "nickname": None,
                    "suggestions": [f"一键导入串解析失败：{e}"]
                }

        # 创建临时 storage_state（不保存）
        temp_id = str(uuid.uuid4())
        state = merge_storage_state({}, cookie, web_protect=web_protect, keys=keys)

        cookies = {c["name"]: c["value"] for c in state.get("cookies", [])}
        if not ({"sessionid", "sessionid_ss"} & cookies.keys()):
            return {
                "valid": False,
                "reason": "invalid",
                "conflict_account": None,
                "sec_uid": None,
                "nickname": None,
                "suggestions": [
                    "Cookie 缺少 sessionid",
                    "请确认已登录抖音后再从浏览器复制完整 Cookie"
                ]
            }

        # 检查重复
        sessionid = cookies.get("sessionid") or ""
        uid_tt = cookies.get("uid_tt") or ""
        dup = find_duplicate_session_owner(
            account_id=temp_id,
            sessionid=sessionid,
            uid_tt=uid_tt,
        )

        if dup:
            other_id, other_name, reason = dup
            is_deleted = "deleted" in reason
            return {
                "valid": False,
                "reason": "duplicate" if not is_deleted else "deleted",
                "conflict_account": {"id": other_id, "nickname": other_name},
                "sec_uid": bundle_sec_uid or None,
                "nickname": bundle_nickname or None,
                "suggestions": [
                    f"此 Cookie 与账号「{other_name}」是同一抖音登录态",
                    "请在浏览器中确认右上角已登录的是目标账号后再导出",
                    "或先删除冲突的账号后再导入" if not is_deleted else "请等待 7 天后再导入"
                ]
            }

        # 检查发送凭证
        can_send = has_send_credential(state)

        return {
            "valid": True,
            "reason": None,
            "conflict_account": None,
            "sec_uid": bundle_sec_uid or None,
            "nickname": bundle_nickname or None,
            "suggestions": [
                "✓ 未检测到重复，可以安全导入",
                f"凭证能力：{'可发送私信' if can_send else '仅接收（需补 web_protect 和 keys 才能发送）'}"
            ]
        }

    except Exception as e:
        logger.exception(f"[check_credential] 预检失败: {e}")
        return {
            "valid": False,
            "reason": "invalid",
            "conflict_account": None,
            "sec_uid": None,
            "nickname": None,
            "suggestions": [f"预检失败：{e}"]
        }


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
    account.sec_uid = None
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
    response=DouyinConversationListOut,
    summary="获取账号的会话列表（消息回复模块）",
)
def list_account_conversations(
    request,
    account_id: str,
    page: int = 1,
    page_size: int = 50,
    keyword: str = '',
):
    """
    获取指定账号的会话列表（用于消息回复界面），支持分页与关键词搜索。
    不依赖 session，直接通过 account_id 查询。
    """
    from core.douyin.douyin_conversation_utils import paginate_account_conversations

    account = get_object_or_404(DouyinAccount, id=account_id)
    items, total, has_more = paginate_account_conversations(
        account.id,
        page=page,
        page_size=page_size,
        keyword=keyword,
    )
    page = max(1, int(page or 1))
    page_size = min(max(1, int(page_size or 50)), 100)
    return {
        'items': items,
        'total': total,
        'page': page,
        'page_size': page_size,
        'has_more': has_more,
    }


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
    from core.douyin.douyin_session_schema import DouyinMessageItemOut

    account = get_object_or_404(DouyinAccount, id=account_id)
    conv = get_object_or_404(
        DouyinConversation,
        id=conversation_id,
        account_id=account.id
    )

    # 获取最新的100条消息，然后按时间升序排列（从早到晚）
    messages = list(
        DouyinMessage.objects
        .filter(conversation_id=conv.id)
        .order_by('-received_at', '-sys_create_datetime')[:100]
    )
    # 反转列表，让前端从早到晚显示
    messages.reverse()

    # 增强消息数据：添加发送者信息
    result = []
    for msg in messages:
        # 根据消息方向填充发送者信息
        if msg.direction == 'in':
            # 对方发来的消息
            sender_name = conv.peer_nickname or conv.peer_sec_uid
            sender_avatar = conv.peer_avatar
        else:
            # 我方发出的消息
            sender_name = account.nickname
            sender_avatar = account.avatar

        # 创建 Schema 实例（不是 dict）
        item = DouyinMessageItemOut(
            id=str(msg.id),
            direction=msg.direction,
            content_type=msg.content_type,
            content=msg.content,
            received_at=msg.received_at,
            processed=msg.processed,
            sender_name=sender_name,
            sender_avatar=sender_avatar,
        )
        result.append(item)

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

    ok, command_id = command_publisher.send_manual_reply(str(account.id), str(conv.id), text)
    if not ok:
        return DouyinSessionControlOut(
            success=False,
            message="未能下发手动回复指令（Worker 命令队列不可用）"
        )

    return DouyinSessionControlOut(
        success=True,
        message="手动回复指令已下发",
        command_id=command_id,
    )


@router.get(
    "/worker-command/{command_id}",
    response=DouyinWorkerCommandStatusOut,
    summary="查询 Worker 命令执行结果（客户端手动发送轮询）",
)
def get_worker_command_status(request, command_id: str):
    from core.douyin.douyin_worker_command_model import DouyinWorkerCommand

    cmd = get_object_or_404(DouyinWorkerCommand, id=command_id)
    result = (cmd.payload or {}).get('_result') if isinstance(cmd.payload, dict) else None
    if cmd.consumed_at is None:
        status = 'pending'
    elif isinstance(result, dict) and result.get('status') in ('success', 'failed'):
        status = str(result['status'])
    else:
        status = 'unknown'
    return DouyinWorkerCommandStatusOut(
        command_id=str(cmd.id),
        consumed=cmd.consumed_at is not None,
        status=status,
        error=(result or {}).get('error') if isinstance(result, dict) else None,
        message_id=(result or {}).get('message_id') if isinstance(result, dict) else None,
    )


@router.post(
    "/account/{account_id}/conversation/{conversation_id}/refresh-user",
    response=DouyinSessionControlOut,
    summary="刷新会话对方的用户资料（头像/昵称）",
)
def refresh_account_conversation_user(request, account_id: str, conversation_id: str):
    """
    手动拉取并更新指定会话的对方真实昵称和头像
    """
    from core.douyin.douyin_conversation_model import DouyinConversation
    from core.douyin.runtime.transport.http_protocol import HttpProtocolTransport
    import asyncio
    from asgiref.sync import async_to_sync
    import json
    from urllib.parse import quote

    account = get_object_or_404(DouyinAccount, id=account_id)
    conv = get_object_or_404(
        DouyinConversation,
        id=conversation_id,
        account_id=account.id
    )

    # 如果 peer_sec_uid 为空，或者以 fallback_ 开头，可能不是合法的 sec_uid
    # 在此情况下我们可以看看有没有最近的 inbound message
    sec_uid = conv.peer_sec_uid
    if not sec_uid or sec_uid.startswith('fallback_'):
        from core.douyin.douyin_message_model import DouyinMessage
        first_msg = DouyinMessage.objects.filter(conversation_id=conv.id, direction='in').first()
        if first_msg and first_msg.raw_payload:
            try:
                payload = json.loads(first_msg.raw_payload) if isinstance(first_msg.raw_payload, str) else first_msg.raw_payload
                # Check for sender_sec_uid or user_sec_uid
                sender_sec_uid = payload.get('sender_sec_uid') or payload.get('sec_uid')
                if sender_sec_uid and not sender_sec_uid.startswith('fallback_'):
                    sec_uid = sender_sec_uid
            except Exception:
                pass

    if not sec_uid or sec_uid.startswith('fallback_'):
        return DouyinSessionControlOut(success=False, message="未找到可解析的对方用户 sec_uid，无法刷新资料")

    # 调用 HttpProtocolTransport 拉取用户资料
    async def _resolve():
        transport = HttpProtocolTransport()
        await transport.start(account)
        try:
            url = "https://www.douyin.com/aweme/v1/web/user/profile/other/"
            extra_params = {
                "source": "channel_pc_web",
                "sec_user_id": sec_uid,
                "personal_center_strategy": "1",
                "update_version_code": "170400",
            }
            headers = {
                "accept": "application/json, text/plain, */*",
                "referer": f"https://www.douyin.com/user/{quote(sec_uid, safe='')}",
            }
            resp = await transport._sign.signed_fetch(
                method="GET",
                url=url,
                headers=headers,
                extra_params=extra_params,
                timeout_ms=10_000,
            )
            if not resp.ok:
                return None
            payload = json.loads(resp.text or resp.content.decode("utf-8") or "{}")
            if payload.get("status_code", 0) != 0:
                return None
            return payload.get("user") or {}
        finally:
            await transport.stop(account)

    try:
        user_info = async_to_sync(_resolve)()
    except Exception as e:
        return DouyinSessionControlOut(success=False, message=f"拉取用户资料异常: {str(e)}")

    if not user_info:
        return DouyinSessionControlOut(success=False, message="接口未返回该用户的最新资料或拉取失败")

    nick = user_info.get('nickname')
    if nick:
        conv.peer_nickname = str(nick).strip()
        avatar = ""
        for key in ("avatar_thumb", "avatar_larger", "avatar_medium"):
            block = user_info.get(key) or {}
            urls = block.get("url_list") or []
            if urls:
                avatar = str(urls[0])
                break
        if avatar:
            conv.peer_avatar = avatar
        unique_id = str(user_info.get('unique_id') or user_info.get('short_id') or '').strip()
        if unique_id:
            conv.peer_unique_id = unique_id
        # 如果原本的 peer_sec_uid 是 fallback 的，在这里更新成真实的
        if conv.peer_sec_uid != sec_uid or conv.peer_sec_uid.startswith('fallback_'):
            conv.peer_sec_uid = sec_uid
        # 修复：仅保存需要更新的字段，避免覆盖 last_message_at
        conv.save(update_fields=[
            'peer_nickname',
            'peer_avatar',
            'peer_unique_id',
            'peer_sec_uid',
            'sys_update_datetime',
        ])
        return DouyinSessionControlOut(success=True, message="用户资料已成功更新")
    else:
        return DouyinSessionControlOut(success=False, message="已拉取资料，但未获取到对方的有效昵称")
