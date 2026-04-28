"""
Phase 3 SignProvider 单测

mock 掉 BrowserManager / Page / Context；只验证 SignProvider 自己的逻辑：
  - is_ready 状态机：未 start / start 失败 / start 成功 / page closed
  - signed_fetch 成功路径
  - signed_fetch 浏览器侧返回 ok=false → SignerUnavailable + 失败计数 +1
  - signed_fetch page.evaluate 抛异常 → SignerUnavailable + 失败计数 +1
  - get_cookies 过滤 domain
  - ensure_ready 自动恢复
"""
from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from core.douyin.runtime.transport.sign_provider import (
    SignedResponse,
    SignerUnavailable,
    SignProvider,
)


class _FakeAccount:
    id = "acc-uuid-sign"


class _FakePage:
    def __init__(self, *, evaluate_returns=None, evaluate_raises=None, closed=False):
        self._closed = closed
        self.evaluate = AsyncMock(
            return_value=evaluate_returns,
            side_effect=evaluate_raises,
        )
        self.close = AsyncMock()
        self.goto = AsyncMock()
        self.context = MagicMock()
        self.context.cookies = AsyncMock(return_value=[])

    def is_closed(self):
        return self._closed


class _FakeContext:
    def __init__(self, page):
        self._page = page
        self.cookies = AsyncMock(return_value=[])

    async def new_page(self):
        return self._page


class SignProviderLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_success_marks_ready(self):
        page = _FakePage()
        ctx = _FakeContext(page)
        page.context = ctx
        sp = SignProvider()

        with patch(
            "core.douyin.runtime.transport.sign_provider.BrowserManager.get_or_create_context",
            new=AsyncMock(return_value=ctx),
        ):
            await sp.start(_FakeAccount())

        self.assertTrue(sp.is_ready)
        page.goto.assert_awaited_once()

    async def test_start_browser_failure_keeps_unready(self):
        sp = SignProvider()
        with patch(
            "core.douyin.runtime.transport.sign_provider.BrowserManager.get_or_create_context",
            new=AsyncMock(side_effect=RuntimeError("ctx down")),
        ):
            await sp.start(_FakeAccount())
        self.assertFalse(sp.is_ready)

    async def test_start_goto_failure_closes_page(self):
        page = _FakePage()
        page.goto = AsyncMock(side_effect=RuntimeError("nav timeout"))
        ctx = _FakeContext(page)
        page.context = ctx
        sp = SignProvider()
        with patch(
            "core.douyin.runtime.transport.sign_provider.BrowserManager.get_or_create_context",
            new=AsyncMock(return_value=ctx),
        ):
            await sp.start(_FakeAccount())
        self.assertFalse(sp.is_ready)
        page.close.assert_awaited()

    async def test_stop_closes_page_and_clears(self):
        page = _FakePage()
        ctx = _FakeContext(page)
        page.context = ctx
        sp = SignProvider()
        with patch(
            "core.douyin.runtime.transport.sign_provider.BrowserManager.get_or_create_context",
            new=AsyncMock(return_value=ctx),
        ):
            await sp.start(_FakeAccount())
        await sp.stop(_FakeAccount())
        self.assertFalse(sp.is_ready)
        page.close.assert_awaited()


class SignProviderFetchTests(unittest.IsolatedAsyncioTestCase):
    async def _started(self, page):
        ctx = _FakeContext(page)
        page.context = ctx
        sp = SignProvider()
        with patch(
            "core.douyin.runtime.transport.sign_provider.BrowserManager.get_or_create_context",
            new=AsyncMock(return_value=ctx),
        ):
            await sp.start(_FakeAccount())
        return sp

    async def test_signed_fetch_success_text_body(self):
        import base64 as b64

        # response content_b64 必须是合法 base64
        content = b'{"a":1}'
        page = _FakePage(evaluate_returns={
            "ok": True,
            "status": 200,
            "url": "https://imapi.snssdk.com/foo",
            "headers": {"Content-Type": "application/json"},
            "text": '{"a":1}',
            "content_b64": b64.b64encode(content).decode("ascii"),
        })
        sp = await self._started(page)

        resp = await sp.signed_fetch(
            "POST",
            "https://imapi.snssdk.com/foo",
            body='{"x":1}',
            headers={"X-Test": "1"},
        )
        self.assertIsInstance(resp, SignedResponse)
        self.assertEqual(resp.status, 200)
        self.assertTrue(resp.ok)
        self.assertEqual(resp.json(), {"a": 1})
        self.assertEqual(resp.content, content)
        self.assertEqual(resp.headers.get("content-type"), "application/json")

        # evaluate 收到 (js_source, payload)；检查 payload 已被规范化
        page.evaluate.assert_awaited_once()
        js_source = page.evaluate.await_args.args[0]
        payload = page.evaluate.await_args.args[1]
        self.assertIn("await fetch", js_source)
        self.assertEqual(payload["method"], "POST")  # method 应被 .upper() 处理
        self.assertEqual(payload["url"], "https://imapi.snssdk.com/foo")
        self.assertEqual(payload["body"], '{"x":1}')
        self.assertIsNone(payload["body_b64"])
        self.assertEqual(payload["headers"], {"X-Test": "1"})

    async def test_signed_fetch_with_bytes_body_passes_base64(self):
        """bytes body 应该被 base64 编码后通过 body_b64 字段传给 JS"""
        import base64 as b64

        page = _FakePage(evaluate_returns={
            "ok": True,
            "status": 200,
            "url": "https://imapi.douyin.com/v1/message/send",
            "headers": {"content-type": "application/x-protobuf"},
            "text": "",  # protobuf 解 utf-8 一般是空 / 乱码
            "content_b64": b64.b64encode(b"\x08\x64\x10\x9d").decode("ascii"),
        })
        sp = await self._started(page)

        proto_body = b"\x08\x64\x10\x9d\x4e\x18\x00"
        resp = await sp.signed_fetch(
            "POST",
            "https://imapi.douyin.com/v1/message/send",
            body=proto_body,
            headers={"content-type": "application/x-protobuf"},
        )
        self.assertEqual(resp.content, b"\x08\x64\x10\x9d")
        # 关键：bytes body 走 body_b64，body 留空
        payload = page.evaluate.await_args.args[1]
        self.assertIsNone(payload["body"])
        self.assertEqual(b64.b64decode(payload["body_b64"]), proto_body)

    async def test_signed_fetch_browser_returns_ok_false(self):
        """fetch 在浏览器内 reject（网络/超时）→ ok=false → SignerUnavailable + 计数+1"""
        page = _FakePage(evaluate_returns={"ok": False, "error": "TypeError: failed"})
        sp = await self._started(page)

        with self.assertRaises(SignerUnavailable):
            await sp.signed_fetch("GET", "https://imapi.snssdk.com/foo")
        self.assertEqual(sp._fail_streak, 1)

    async def test_signed_fetch_evaluate_raises(self):
        """page.evaluate 自身抛 → SignerUnavailable"""
        page = _FakePage(evaluate_raises=RuntimeError("evaluate timeout"))
        sp = await self._started(page)

        with self.assertRaises(SignerUnavailable):
            await sp.signed_fetch("GET", "https://imapi.snssdk.com/foo")
        self.assertEqual(sp._fail_streak, 1)

    async def test_signed_fetch_unready_raises(self):
        sp = SignProvider()
        # 没 start
        with self.assertRaises(SignerUnavailable):
            await sp.signed_fetch("GET", "https://x")

    async def test_signed_fetch_streak_resets_on_success(self):
        page = _FakePage(evaluate_returns={
            "ok": True, "status": 200, "url": "u", "headers": {}, "text": "",
        })
        sp = await self._started(page)
        sp._fail_streak = 3
        await sp.signed_fetch("GET", "https://x")
        self.assertEqual(sp._fail_streak, 0)


class SignProviderCookieTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_cookies_filters_domain(self):
        page = _FakePage()
        ctx = _FakeContext(page)
        ctx.cookies = AsyncMock(return_value=[
            {"name": "msToken", "value": "tok-123", "domain": ".douyin.com"},
            {"name": "sessionid", "value": "sess", "domain": ".douyin.com"},
            {"name": "OTHER", "value": "irrelevant", "domain": ".other.com"},
        ])
        page.context = ctx
        sp = SignProvider()
        with patch(
            "core.douyin.runtime.transport.sign_provider.BrowserManager.get_or_create_context",
            new=AsyncMock(return_value=ctx),
        ):
            await sp.start(_FakeAccount())

        cookies = await sp.get_cookies()
        self.assertIn("mstoken", cookies)
        self.assertEqual(cookies["mstoken"], "tok-123")
        self.assertIn("sessionid", cookies)
        self.assertNotIn("other", cookies)

    async def test_get_cookies_when_not_started_returns_empty(self):
        sp = SignProvider()
        self.assertEqual(await sp.get_cookies(), {})


class SignProviderEnsureReadyTests(unittest.IsolatedAsyncioTestCase):
    async def test_ensure_ready_recovers_dead_page(self):
        """page.is_closed=True 时，ensure_ready 应该重新跑 start"""
        dead_page = _FakePage(closed=True)
        live_page = _FakePage()
        ctx = _FakeContext(live_page)  # 第二次 new_page 拿活的
        live_page.context = ctx
        dead_page.context = ctx
        sp = SignProvider()
        sp._page = dead_page  # 模拟之前 start 过但 page 死了

        with patch(
            "core.douyin.runtime.transport.sign_provider.BrowserManager.get_or_create_context",
            new=AsyncMock(return_value=ctx),
        ):
            ok = await sp.ensure_ready(_FakeAccount())

        self.assertTrue(ok)
        self.assertIs(sp._page, live_page)


if __name__ == "__main__":
    unittest.main()
