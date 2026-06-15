#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
管理命令：补全会话用户信息

用法：
    python manage.py backfill_conversation_user_info
    python manage.py backfill_conversation_user_info --account-id=xxx
    python manage.py backfill_conversation_user_info --limit=10 --dry-run
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from core.douyin.douyin_conversation_model import DouyinConversation
from core.douyin.douyin_account_model import DouyinAccount
import asyncio


class Command(BaseCommand):
    help = '补全会话用户信息（昵称、头像）'

    def add_arguments(self, parser):
        parser.add_argument(
            '--account-id',
            type=str,
            help='只处理指定账号的会话',
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=100,
            help='每次处理的会话数量（默认 100）',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='模拟运行，不实际保存',
        )

    def handle(self, *args, **options):
        account_id = options.get('account_id')
        limit = options.get('limit', 100)
        dry_run = options.get('dry_run', False)

        if dry_run:
            self.stdout.write(self.style.WARNING('🔍 模拟运行模式（不会实际保存）'))

        # 查找需要补全的会话（peer_nickname 为空）
        qs = DouyinConversation.objects.filter(
            peer_nickname__isnull=True,
            is_deleted=False,
        ).select_related('account')

        if account_id:
            qs = qs.filter(account_id=account_id)

        total = qs.count()
        self.stdout.write(f'📊 找到 {total} 个需要补全的会话')

        if total == 0:
            self.stdout.write(self.style.SUCCESS('✅ 没有需要补全的会话'))
            return

        # 分批处理
        offset = 0
        updated_count = 0
        failed_count = 0

        while offset < total:
            batch = list(qs[offset:offset + limit])
            self.stdout.write(f'\n🔄 处理第 {offset + 1}-{offset + len(batch)} 个会话...')

            # 按账号分组
            by_account: dict[str, list] = {}
            for conv in batch:
                aid = str(conv.account_id)
                if aid not in by_account:
                    by_account[aid] = []
                by_account[aid].append(conv)

            # 每个账号调用一次 API
            for aid, convs in by_account.items():
                try:
                    account = convs[0].account
                    updated = asyncio.run(self._backfill_account_conversations(
                        account, convs, dry_run
                    ))
                    updated_count += updated
                except Exception as e:
                    self.stdout.write(
                        self.style.ERROR(f'  ✗ 账号 {account.nickname} 处理失败: {e}')
                    )
                    failed_count += len(convs)

            offset += len(batch)

        self.stdout.write('\n' + '=' * 60)
        if dry_run:
            self.stdout.write(self.style.WARNING(f'🔍 模拟运行完成'))
        else:
            self.stdout.write(self.style.SUCCESS(f'✅ 补全完成'))
        self.stdout.write(f'  - 成功: {updated_count}')
        self.stdout.write(f'  - 失败: {failed_count}')
        self.stdout.write(f'  - 总计: {total}')

    async def _backfill_account_conversations(
        self,
        account: DouyinAccount,
        conversations: list[DouyinConversation],
        dry_run: bool,
    ) -> int:
        """为一个账号的会话补全用户信息"""
        from core.douyin.runtime.transport.http_protocol import HttpProtocolTransport

        transport = HttpProtocolTransport()
        await transport.start(account)

        try:
            # 提取 sender_uid（从 sec_uid 推断或使用其他方法）
            # 注意：这里需要先获取 sender_uid
            # 如果数据库没有存 sender_uid，可能需要其他方法

            # 方案：直接用 sec_uid 查询用户信息
            updated = 0
            for conv in conversations:
                try:
                    # 调用抖音 API 获取用户信息
                    user_info = await self._fetch_user_by_sec_uid(
                        transport, account, conv.peer_sec_uid
                    )

                    if user_info:
                        if not dry_run:
                            conv.peer_nickname = user_info.get('nickname') or conv.peer_nickname
                            conv.peer_avatar = user_info.get('avatar') or conv.peer_avatar
                            conv.save(update_fields=['peer_nickname', 'peer_avatar'])

                        self.stdout.write(
                            self.style.SUCCESS(
                                f'  ✓ {conv.peer_sec_uid[:20]}... → {user_info.get("nickname", "")}'
                            )
                        )
                        updated += 1
                    else:
                        self.stdout.write(
                            self.style.WARNING(
                                f'  ⚠ {conv.peer_sec_uid[:20]}... 未找到用户信息'
                            )
                        )
                except Exception as e:
                    self.stdout.write(
                        self.style.ERROR(
                            f'  ✗ {conv.peer_sec_uid[:20]}... 失败: {e}'
                        )
                    )

            return updated

        finally:
            await transport.stop(account)

    async def _fetch_user_by_sec_uid(
        self,
        transport: 'HttpProtocolTransport',
        account: 'DouyinAccount',
        sec_uid: str,
    ) -> dict | None:
        """通过 sec_uid 获取用户信息

        注意：这个方法需要调用抖音 API
        由于我们没有 sender_uid，可能需要先调用其他接口获取
        """
        # TODO: 实现通过 sec_uid 获取用户信息的逻辑
        # 可能需要调用 douyin.com/user/ 接口
        return None
