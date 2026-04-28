import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from django.test import SimpleTestCase, override_settings

from core.douyin.runtime.worker import (
    DouyinWorker,
    _can_process_reply,
    _should_enforce_daily_peer_limit,
)


class DouyinWorkerRuntimeTests(SimpleTestCase):
    def test_can_process_reply_allows_live_session_on_non_disabled_account(self):
        self.assertTrue(_can_process_reply(2, True, True))
        self.assertTrue(_can_process_reply(1, True, False))

    def test_can_process_reply_blocks_disabled_or_disabled_auto_reply(self):
        self.assertFalse(_can_process_reply(3, True, True))
        self.assertFalse(_can_process_reply(1, False, True))
        self.assertFalse(_can_process_reply(2, True, False))

    def test_daily_peer_limit_disabled_by_default(self):
        self.assertFalse(_should_enforce_daily_peer_limit())

    @override_settings(DOUYIN_ENFORCE_DAILY_PEER_REPLY_LIMIT=True)
    def test_daily_peer_limit_can_be_enabled_by_setting(self):
        self.assertTrue(_should_enforce_daily_peer_limit())


class _FakeAccount:
    def __init__(self, account_id: str = "acc-uuid-xxxxxxxx"):
        self.id = account_id
        self.nickname = "tester"


class DouyinWorkerLoginDispatchTests(unittest.IsolatedAsyncioTestCase):
    """
    Phase 2 修复：login 命令必须在 _dispatch_command 同步路径上立即占位 _login_tasks，
    避免 close_context 让出事件循环时 race 到的 focus 命令把扫码 tab 顶掉。
    """

    async def test_login_dispatch_immediately_registers_login_task(self):
        worker = DouyinWorker()
        account_id = "acc-uuid-xxxxxxxx"

        long_running_close = asyncio.Event()

        async def _slow_close(_aid):
            # 模拟旧逻辑：dispatcher 在 close_context 时会让出事件循环
            await long_running_close.wait()

        async def _slow_scan(_account):
            await asyncio.sleep(60)
            return True

        with patch(
            "core.douyin.runtime.worker._fetch_account_orm",
            new=AsyncMock(return_value=_FakeAccount(account_id)),
        ), patch(
            "core.douyin.runtime.worker.BrowserManager.close_context",
            new=AsyncMock(side_effect=_slow_close),
        ), patch(
            "core.douyin.runtime.worker.delete_account_runtime_state",
            create=True,
        ), patch(
            "core.douyin.runtime.worker.scan_qrcode_login",
            new=AsyncMock(side_effect=_slow_scan),
        ):
            await worker._dispatch_command(
                f"douyin:cmd:login:{account_id}", {}
            )
            # 关键断言：dispatcher 同步返回后 _login_tasks 必须已有键，
            # 哪怕 close_context 还没结束 —— 这样并发到的 focus 守卫能立刻命中。
            self.assertIn(account_id, worker._login_tasks)
            task = worker._login_tasks[account_id]
            self.assertFalse(task.done())

            # 释放 close → 让 task 进入 scan_qrcode_login 等待，再清理
            long_running_close.set()
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass


class DouyinWorkerCommandSerializationTests(unittest.IsolatedAsyncioTestCase):
    """
    bugfix：登出和监管页命令几乎同时下发时必须串行执行。
    旧实现用 asyncio.create_task 并发派发 → focus 跑在 logout 关 ctx 之前 →
    抢到旧 BrowserContext → 用户看到的是"已登录"页面。
    """

    async def test_focus_waits_for_logout_to_finish(self):
        worker = DouyinWorker()
        account_id = "acc-serialize-test"

        order: list[str] = []
        logout_release = asyncio.Event()

        async def _slow_stop_account(aid: str):
            order.append(f"stop_account:start:{aid}")
            # 模拟 close_context 实际耗时（几十 ms 就够暴露 race）
            await logout_release.wait()
            order.append(f"stop_account:done:{aid}")

        async def _focus_inner(aid: str):
            order.append(f"focus:run:{aid}")

        # 一个未登录账号 — focus 守卫会拒绝；为了能验证锁本身的串行，
        # 这里返回 status=1，让 focus 真的会跑到 _focus_account_page。
        class _OnlineAccount:
            def __init__(self):
                self.id = account_id
                self.status = 1
                self.nickname = "tester"

        with patch.object(worker, "_stop_account", new=AsyncMock(side_effect=_slow_stop_account)), \
             patch.object(worker, "_cancel_login_task", new=AsyncMock()), \
             patch.object(worker, "_focus_account_page", new=AsyncMock(side_effect=_focus_inner)), \
             patch(
                "core.douyin.runtime.worker._fetch_account_orm",
                new=AsyncMock(return_value=_OnlineAccount()),
             ), patch(
                "core.douyin.runtime.worker.delete_account_runtime_state",
                create=True,
             ):
            # 模拟 redis pubsub 的 create_task 派发：两条命令几乎同时进入
            t_logout = asyncio.create_task(
                worker._dispatch_command(f"douyin:cmd:logout:{account_id}", {})
            )
            await asyncio.sleep(0)  # 让 logout 先抢到锁
            t_focus = asyncio.create_task(
                worker._dispatch_command(f"douyin:cmd:focus:{account_id}", {})
            )

            # focus 必须卡在锁上等 logout 完成
            await asyncio.sleep(0.05)
            self.assertEqual(
                order,
                [f"stop_account:start:{account_id}"],
                f"focus 不能在 logout 关 ctx 之前执行，实际顺序: {order}",
            )

            logout_release.set()
            await asyncio.gather(t_logout, t_focus)

            self.assertEqual(
                order,
                [
                    f"stop_account:start:{account_id}",
                    f"stop_account:done:{account_id}",
                    f"focus:run:{account_id}",
                ],
                f"命令串行顺序错乱: {order}",
            )

    async def test_focus_skipped_when_account_not_logged_in(self):
        """守卫：status != 1 时 focus 命令直接返回，不打开 chromium。"""
        worker = DouyinWorker()
        account_id = "acc-offline"

        class _OfflineAccount:
            def __init__(self):
                self.id = account_id
                self.status = 0  # 未登录
                self.nickname = "offline-tester"

        focus_inner = AsyncMock()
        with patch.object(worker, "_focus_account_page", new=focus_inner), \
             patch(
                "core.douyin.runtime.worker._fetch_account_orm",
                new=AsyncMock(return_value=_OfflineAccount()),
             ):
            await worker._dispatch_command(f"douyin:cmd:focus:{account_id}", {})

        focus_inner.assert_not_called()


class DouyinAccountFocusApiGuardTests(SimpleTestCase):
    """API 层守卫：focus 接口对未登录账号直接返回 success=False，
    避免 worker 异步处理后用户毫无感知地点了好几次。"""

    def test_focus_api_rejects_offline_account(self):
        from unittest.mock import MagicMock

        from core.douyin import douyin_account_api

        offline = MagicMock()
        offline.id = "acc-offline"
        offline.nickname = "未登录号"
        offline.status = 0

        with patch.object(douyin_account_api, "get_object_or_404", return_value=offline), \
             patch.object(douyin_account_api.command_publisher, "send_focus_account") as send:
            result = douyin_account_api.focus_account(MagicMock(), "acc-offline")

        self.assertFalse(result.success)
        self.assertIn("未登录", result.message)
        send.assert_not_called()

    def test_focus_api_passes_online_account_to_publisher(self):
        from unittest.mock import MagicMock

        from core.douyin import douyin_account_api

        online = MagicMock()
        online.id = "acc-online"
        online.nickname = "在线号"
        online.status = 1

        with patch.object(douyin_account_api, "get_object_or_404", return_value=online), \
             patch.object(
                douyin_account_api.command_publisher,
                "send_focus_account",
                return_value=True,
             ) as send:
            result = douyin_account_api.focus_account(MagicMock(), "acc-online")

        self.assertTrue(result.success)
        send.assert_called_once_with("acc-online")
