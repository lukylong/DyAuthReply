"""humanize.py 的单测：
- human_click 的 JS 兜底分支必须显式传 timeout，避免 Playwright 默认 30s 卡顿。
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from django.test import SimpleTestCase

from core.douyin.runtime.humanize import human_click


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _make_locator(*, click_raises: bool, evaluate_raises: bool) -> MagicMock:
    """伪造一个 locator，模拟 click 失败、evaluate 也失败的场景。"""
    loc = MagicMock()
    loc.scroll_into_view_if_needed = AsyncMock()
    loc.wait_for = AsyncMock()
    loc.hover = AsyncMock()

    async def _click(**_kwargs):
        if click_raises:
            raise RuntimeError("click failed (DOM detached)")

    async def _evaluate(*_args, **kwargs):
        # 关键断言：evaluate 必须显式传 timeout，否则会用默认 30s 卡死。
        assert 'timeout' in kwargs, (
            "human_click 内部 evaluate 兜底必须显式指定 timeout "
            "（默认 30s 在 DOM detach 时会拖死扫描）"
        )
        assert kwargs['timeout'] <= 10000, (
            f"timeout 过大({kwargs['timeout']}ms)，应当 ≤10s"
        )
        if evaluate_raises:
            raise RuntimeError(f"evaluate failed (timeout={kwargs['timeout']}ms)")

    loc.click = AsyncMock(side_effect=_click)
    loc.evaluate = AsyncMock(side_effect=_evaluate)
    return loc


class HumanClickEvaluateTimeoutTests(SimpleTestCase):
    """关键回归：JS 兜底 evaluate 必须带 timeout。"""

    def test_evaluate_fallback_must_pass_explicit_timeout(self):
        loc = _make_locator(click_raises=True, evaluate_raises=False)
        _run(human_click(loc))
        loc.evaluate.assert_awaited_once()
        _, kwargs = loc.evaluate.call_args
        self.assertIn('timeout', kwargs)
        self.assertLessEqual(kwargs['timeout'], 10000)

    def test_evaluate_failure_raises_last_click_error(self):
        loc = _make_locator(click_raises=True, evaluate_raises=True)
        with self.assertRaises(RuntimeError) as cm:
            _run(human_click(loc))
        # 既可能是最后一次 click 的错，也可能是 evaluate 的错；
        # 关键是必须 raise 让上层快速跳过会话，而不是吞掉。
        self.assertTrue(
            'click failed' in str(cm.exception)
            or 'evaluate failed' in str(cm.exception)
        )

    def test_normal_click_skips_evaluate_fallback(self):
        loc = _make_locator(click_raises=False, evaluate_raises=False)
        _run(human_click(loc))
        loc.click.assert_awaited()
        loc.evaluate.assert_not_called()
