"""为现有会话补充用户昵称和头像"""
import asyncio
import os

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'application.settings')
import django
django.setup()

from core.douyin.douyin_account_model import DouyinAccount
from core.douyin.douyin_conversation_model import DouyinConversation


async def main():
    account = await asyncio.to_thread(
        lambda: DouyinAccount.objects.first()
    )
    if not account:
        print("账号不存在")
        return

    print(f"账号: {account.nickname}, self sec_uid: {account.sec_uid}")

    convs = await asyncio.to_thread(
        lambda: list(DouyinConversation.objects.filter(account_id=account.id))
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

    print("\n测试 fetch_self_profile...")
    print("\n测试 _resolve_user_details_by_sec_uids...")
    sec_uids = [
        'MS4wLjABAAAA5K_cxi4sxveAPjgv39yoemYj7c83E8ObMD7A-BCbv9E',
        'MS4wLjABAAAA5BGFXmgsSzRclbTggRnJbsrFEW8JXoFLvY3hfNLTF6U',
        'MS4wLjABAAAAxcNt16wSrYOj_HrHFUP78RGZKlFeBDt02--ZwiIo-7QtYhxwWKnjD-IfhvDLvP3Q',
        'MS4wLjABAAAA1C91B1sscrrM-wNdf9h_MjcyIgYnrh2GMN7ukRvgkl0nPNUtXuJhy7a-IK6Js3yp'
    ]
    try:
        details = await transport._resolve_user_details_by_sec_uids(account, sec_uids)
        print(f"批量获取结果:")
        for sec, info in details.items():
            print(f"  sec={sec[:20]}...: nickname={info.get('nickname')!r}")
    except Exception as e:
        print(f"Error: {e}")

    await transport.stop(account)
    print("\n完成")


asyncio.run(main())
