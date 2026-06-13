"""为现有会话补充用户昵称和头像"""
import asyncio
import os

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'application.settings')
import django
django.setup()

from core.douyin.douyin_account_model import DouyinAccount
from core.douyin.douyin_conversation_model import DouyinConversation


async def main():
    account_id = 'd2e80ae6-5be7-4d38-9854-81380b712973'

    account = await asyncio.to_thread(
        lambda: DouyinAccount.objects.filter(id=account_id).first()
    )
    if not account:
        print("账号不存在")
        return

    print(f"账号: {account.nickname}, self sec_uid: {account.sec_uid}")

    convs = await asyncio.to_thread(
        lambda: list(DouyinConversation.objects.filter(account_id=account_id))
    )

    # 收集 peer_uid（从 platform_conversation_id 解析）
    uid_to_conv = {}
    self_uid = None
    for conv in convs:
        parts = (conv.platform_conversation_id or '').split(':')
        if len(parts) >= 4:
            try:
                uid1, uid2 = int(parts[2]), int(parts[3])
                # parts[2] 通常是 self_uid（较小），parts[3] 是 peer
                if self_uid is None:
                    self_uid = uid1
                uid_to_conv[uid1] = conv
                uid_to_conv[uid2] = conv
            except ValueError:
                pass

    print(f"self_uid 推测: {self_uid}")
    user_ids = list(uid_to_conv.keys())
    print(f"收集到 uids: {user_ids}")

    if not user_ids:
        print("没有可用的 uid")
        return

    from core.douyin.runtime.transport.http_protocol import HttpProtocolTransport

    transport = HttpProtocolTransport()
    await transport.start(account)

    print(f"\n调用 user_detail...")
    details = await transport._resolve_user_details(account, user_ids)
    print(f"获取到 {len(details)} 个用户详情:")

    for uid, info in details.items():
        nick = info.get('nickname', '')
        sec = info.get('sec_uid', '')
        print(f"  uid={uid}: nickname={nick!r}, sec_uid={sec[:20]}...")

        conv = uid_to_conv.get(uid)
        # 用 sec_uid 匹配确认是对方而非自己
        if conv and nick and sec == conv.peer_sec_uid:
            conv.peer_nickname = nick
            if info.get('avatar'):
                conv.peer_avatar = info['avatar']
            await asyncio.to_thread(conv.save)
            print(f"    ✓ 已更新会话（sec_uid 匹配）")
        elif conv and nick:
            # sec_uid 不匹配，可能是 self，跳过
            print(f"    - sec_uid 不匹配会话的 peer，跳过（可能是 self）")

    await transport.stop(account)
    print("\n完成")


asyncio.run(main())
