import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from core.douyin.runtime import selectors as S
from core.douyin.runtime.inbox import (
    AccountIdentityMismatch,
    _classify_bubble_direction,
    _collect_own_sec_uid,
    _ensure_account_sec_uid,
    _is_aggregate_conversation,
    _is_time_like_label,
    _norm_for_compare,
    _parse_conversation_lines,
    _probe_own_sec_uid_lightweight,
    _sanitize_conversation_snapshot,
    _scroll_conversation_list,
)


def _make_bubble(*, cls: str = "", links: list[dict] | None = None,
                 data_direction: str | None = None):
    """构造一个能被 _classify_bubble_direction 解析的 bubble locator mock。"""
    bubble = MagicMock(name="bubble")
    attrs = {"class": cls, "data-direction": data_direction}

    async def _get_attribute(name: str):
        return attrs.get(name)
    bubble.get_attribute = _get_attribute

    by_sel: dict[str, list[dict]] = {}
    for link in links or []:
        by_sel.setdefault(link["selector"], []).append(link)

    def _locator(selector: str):
        items = by_sel.get(selector, [])
        loc = MagicMock(name=f"loc[{selector}]")

        async def _count():
            return len(items)
        loc.count = _count

        def _nth(i: int):
            inner = MagicMock(name=f"loc[{selector}][{i}]")
            href = items[i].get("href", "")

            async def _attr(name: str):
                return href if name == "href" else None
            inner.get_attribute = _attr
            return inner
        loc.nth = _nth
        return loc

    bubble.locator = _locator
    return bubble


class DouyinInboxTests(unittest.IsolatedAsyncioTestCase):
    def test_parse_conversation_lines_strips_trailing_time(self):
        nickname, preview = _parse_conversation_lines("陌生人A\n你好，在吗\n16:10")

        self.assertEqual(nickname, "陌生人A")
        self.assertEqual(preview, "你好，在吗")

    def test_aggregate_conversation_only_skipped_on_default_tabs(self):
        self.assertTrue(_is_aggregate_conversation("陌生人消息", None))
        self.assertTrue(_is_aggregate_conversation("朋友私信", "全部"))
        self.assertFalse(_is_aggregate_conversation("陌生人消息", "陌生人"))
        self.assertFalse(_is_aggregate_conversation("测试用户", None))

    async def test_scroll_conversation_list_uses_page_evaluate(self):
        page = AsyncMock()
        page.evaluate = AsyncMock(return_value=True)

        moved = await _scroll_conversation_list(page)

        self.assertTrue(moved)
        page.evaluate.assert_awaited()

    def test_norm_for_compare_strips_whitespace_and_truncates(self):
        self.assertEqual(_norm_for_compare("  你 好\t世界\n "), "你好世界")
        long_text = "a" * 200
        self.assertEqual(len(_norm_for_compare(long_text)), 120)
        self.assertEqual(_norm_for_compare(""), "")

    async def test_classify_bubble_self_via_author_link(self):
        """作者链接命中本账号 sec_uid → self（最高优先级，不受 css 干扰）"""
        bubble = _make_bubble(
            cls="message-item received from-other",  # 故意带 other 的 css
            links=[{
                "selector": "a[href*='user/']",
                "href": "/user/MS4wOABC_self_uid",
            }],
        )
        result = await _classify_bubble_direction(bubble, own_sec_uid="MS4wOABC_self_uid")
        self.assertEqual(result, "self")

    async def test_classify_bubble_peer_via_other_author_link(self):
        bubble = _make_bubble(
            cls="message-item is-master",  # 故意带 self 的 css
            links=[{
                "selector": "a[href*='user/']",
                "href": "/user/MS4wOXYZ_peer_uid",
            }],
        )
        result = await _classify_bubble_direction(bubble, own_sec_uid="MS4wOABC_self_uid")
        self.assertEqual(result, "peer")

    async def test_classify_bubble_other_hint_wins_over_self_hint(self):
        """旧版关键词同时命中时，必须以 peer 优先（避免对方误判为自己漏回）"""
        bubble = _make_bubble(cls="message-item is-other ownership-master")
        result = await _classify_bubble_direction(bubble, own_sec_uid=None)
        self.assertEqual(result, "peer")

    async def test_classify_bubble_self_hint_only(self):
        bubble = _make_bubble(cls="message-item is-master placement-right")
        result = await _classify_bubble_direction(bubble, own_sec_uid=None)
        self.assertEqual(result, "self")

    async def test_classify_bubble_unknown_when_ambiguous(self):
        """没有明确 self/other css 关键词时返回 unknown，由上层做回声黑名单兜底"""
        bubble = _make_bubble(cls="message-item bubble")
        result = await _classify_bubble_direction(bubble, own_sec_uid=None)
        self.assertEqual(result, "unknown")

    async def test_classify_bubble_does_not_match_sender_word_anymore(self):
        """回归测试：旧实现里 'send' in 'sender-...' 会把对方消息误判为 self。"""
        bubble = _make_bubble(cls="message-item from-sender chat-bubble received")
        result = await _classify_bubble_direction(bubble, own_sec_uid=None)
        # 不再误判为 self；要么 unknown，要么 peer，绝不能 self
        self.assertNotEqual(result, "self")

    def test_self_hint_constants_exclude_overbroad_keywords(self):
        """选择器层守约：'send' / 'right' / 'me' 等过宽关键词必须从 self hint 中下线。"""
        forbidden = {"send", "right", "me"}
        self.assertFalse(
            forbidden & set(S.IM_MESSAGE_SELF_HINT),
            f"IM_MESSAGE_SELF_HINT 含过宽关键词: {forbidden & set(S.IM_MESSAGE_SELF_HINT)}",
        )


class CollectOwnSecUidTests(unittest.IsolatedAsyncioTestCase):
    """own sec_uid 多源采集——这是自动回复触发的关键依赖。"""

    @staticmethod
    def _make_page(*, cookies=None, content="", url="https://creator.douyin.com/creator-micro/data/following/chat"):
        page = MagicMock(name="page")
        page.url = url
        page.context = MagicMock()
        page.context.cookies = AsyncMock(return_value=cookies or [])
        page.content = AsyncMock(return_value=content)
        return page

    async def test_collect_from_cookie_named_sec_user_id(self):
        page = self._make_page(cookies=[
            {"name": "sessionid_ss", "value": "irrelevant"},
            {"name": "sec_user_id", "value": "MS4wLjABAAAA_self_uid_at_least_20chars"},
        ])
        result = await _collect_own_sec_uid(page)
        self.assertEqual(result, "MS4wLjABAAAA_self_uid_at_least_20chars")

    async def test_collect_from_page_body_ssr_json(self):
        page = self._make_page(
            cookies=[{"name": "sessionid_ss", "value": "x"}],
            content='<script>window.__INITIAL_STATE__={"user":{"sec_uid":"MS4wLjABAAAA_from_body_ssr_uid"}}</script>',
        )
        result = await _collect_own_sec_uid(page)
        self.assertEqual(result, "MS4wLjABAAAA_from_body_ssr_uid")

    async def test_collect_returns_none_when_nothing_matches(self):
        page = self._make_page(
            cookies=[{"name": "irrelevant", "value": "x"}],
            content="<html><body>no sec uid here</body></html>",
        )
        result = await _collect_own_sec_uid(page)
        self.assertIsNone(result)

    async def test_collect_ignores_too_short_cookie_value(self):
        """长度 <20 几乎肯定是脏数据，跳过避免污染 DB。"""
        page = self._make_page(cookies=[{"name": "sec_user_id", "value": "abc"}])
        result = await _collect_own_sec_uid(page)
        self.assertIsNone(result)


class TimeLikeLabelTests(unittest.TestCase):
    """会话列表里出现在 nickname 位置的"伪 nickname"——必须能识别出来并被剔除。"""

    def test_recognizes_relative_time_phrases(self):
        for s in [
            "刚刚",
            "5秒前",
            "30 秒前",
            "3分钟前",
            "12 分钟前",
            "1小时前",
            "8 小时前",
            "2天前",
            "1周前",
            "3 月前",
        ]:
            self.assertTrue(_is_time_like_label(s), f"应识别为时间标签: {s!r}")

    def test_recognizes_absolute_time_phrases(self):
        for s in ["昨天", "前天", "星期一", "星期日", "12:34", "9:05", "04-28", "12-31 23:59"]:
            self.assertTrue(_is_time_like_label(s), f"应识别为时间标签: {s!r}")

    def test_rejects_real_nicknames(self):
        for s in ["小明", "用户7139479680080", "猴哥DIY配置交流群8", "abc123", "1234567890", ""]:
            self.assertFalse(_is_time_like_label(s), f"不应误判为时间标签: {s!r}")


class SanitizeConversationSnapshotTests(unittest.TestCase):
    """会话快照清洗：避免时间/消息文本被当 nickname 入库形成幽灵会话。"""

    def test_drops_time_like_nickname(self):
        nick, prev = _sanitize_conversation_snapshot("刚刚", "1")
        self.assertEqual(nick, "")
        self.assertEqual(prev, "1")

    def test_drops_nickname_equal_to_short_preview(self):
        # 截图里出现的脏行：nickname == preview，肯定是 selector 误命中
        nick, prev = _sanitize_conversation_snapshot("22啊啊啊", "22啊啊啊")
        self.assertEqual(nick, "")
        self.assertEqual(prev, "22啊啊啊")

    def test_keeps_legit_nickname(self):
        nick, prev = _sanitize_conversation_snapshot(
            "猴哥DIY配置交流群8", "你收到一条新类型消息..."
        )
        self.assertEqual(nick, "猴哥DIY配置交流群8")
        self.assertEqual(prev, "你收到一条新类型消息...")

    def test_keeps_long_nickname_even_if_equal_to_preview(self):
        # 长文本相等不太可能是 selector 误命中（极少有这种长群名 == 长 preview）
        long_text = "这是一个非常长的群名称用来测试不要被简单的相等规则误杀掉"
        nick, prev = _sanitize_conversation_snapshot(long_text, long_text)
        self.assertEqual(nick, long_text)


class ParseConversationLinesTests(unittest.TestCase):
    """fallback 文本解析：抖音 PC 把时间渲染在昵称上方时也要正确取昵称。"""

    def test_skips_leading_time_line(self):
        nick, prev = _parse_conversation_lines("刚刚\n小明\n你好啊")
        self.assertEqual(nick, "小明")
        self.assertEqual(prev, "你好啊")

    def test_skips_multiple_leading_time_lines(self):
        nick, prev = _parse_conversation_lines("12:34\n3 分钟前\n用户A\n您好")
        self.assertEqual(nick, "用户A")
        self.assertEqual(prev, "您好")

    def test_returns_empty_when_only_time_lines(self):
        nick, prev = _parse_conversation_lines("刚刚\n12:34")
        self.assertEqual(nick, "")
        self.assertEqual(prev, "")


class ProbeOwnSecUidLightweightTests(unittest.IsolatedAsyncioTestCase):
    """轻量级 sec_uid 探测：只走 cookies / URL / window globals，不能打 /user/self。"""

    @staticmethod
    def _make_page(*, cookies=None, url="https://creator.douyin.com/creator-micro/data/following/chat",
                   evaluate_return=None):
        page = MagicMock(name="page")
        page.url = url
        page.context = MagicMock()
        page.context.cookies = AsyncMock(return_value=cookies or [])
        page.evaluate = AsyncMock(return_value=evaluate_return)
        return page

    async def test_probe_returns_cookie_value_when_present(self):
        page = self._make_page(cookies=[
            {"name": "sec_user_id", "value": "MS4wLjABAAAA_self_uid_at_least_20chars"},
        ])
        result = await _probe_own_sec_uid_lightweight(page)
        self.assertEqual(result, "MS4wLjABAAAA_self_uid_at_least_20chars")

    async def test_probe_returns_window_global_value_when_no_cookie(self):
        page = self._make_page(
            cookies=[{"name": "sessionid_ss", "value": "x"}],
            evaluate_return="MS4wLjABAAAA_from_window_global_uid",
        )
        result = await _probe_own_sec_uid_lightweight(page)
        self.assertEqual(result, "MS4wLjABAAAA_from_window_global_uid")

    async def test_probe_returns_none_when_all_strategies_fail(self):
        page = self._make_page(
            cookies=[{"name": "irrelevant", "value": "x"}],
            evaluate_return=None,
        )
        result = await _probe_own_sec_uid_lightweight(page)
        self.assertIsNone(result)

    async def test_probe_does_not_open_new_page_for_user_self_redirect(self):
        """关键不变量：身份核对每轮都跑，不能打 /user/self（重 + 慢 + 易触发风控）。"""
        page = self._make_page(cookies=[{"name": "irrelevant", "value": "x"}])
        page.context.new_page = AsyncMock()
        await _probe_own_sec_uid_lightweight(page)
        page.context.new_page.assert_not_called()


class EnsureAccountSecUidTests(unittest.IsolatedAsyncioTestCase):
    """账号身份核对：DB 与浏览器 sec_uid 不一致时必须抛 AccountIdentityMismatch。"""

    @staticmethod
    def _make_account(account_id: str = "acc-1", sec_uid: str = ""):
        account = MagicMock()
        account.id = account_id
        account.sec_uid = sec_uid
        return account

    @staticmethod
    def _make_page(url: str = "https://creator.douyin.com/creator-micro/im"):
        page = MagicMock()
        page.url = url
        return page

    async def test_returns_db_value_when_probe_matches(self):
        account = self._make_account(sec_uid="MS4wLjABAAAA_db_value_xxxxxxxxxx")
        page = self._make_page()
        with patch(
            "core.douyin.runtime.inbox._probe_own_sec_uid_lightweight",
            new=AsyncMock(return_value="MS4wLjABAAAA_db_value_xxxxxxxxxx"),
        ):
            result = await _ensure_account_sec_uid(account, page)
        self.assertEqual(result, "MS4wLjABAAAA_db_value_xxxxxxxxxx")

    async def test_returns_db_value_when_probe_fails(self):
        """探测每轮都可能 miss（cookies 还没就绪），不能据此判失败。"""
        account = self._make_account(sec_uid="MS4wLjABAAAA_db_value_xxxxxxxxxx")
        page = self._make_page()
        with patch(
            "core.douyin.runtime.inbox._probe_own_sec_uid_lightweight",
            new=AsyncMock(return_value=None),
        ):
            result = await _ensure_account_sec_uid(account, page)
        self.assertEqual(result, "MS4wLjABAAAA_db_value_xxxxxxxxxx")

    async def test_raises_identity_mismatch_when_probe_differs(self):
        """关键守卫：DB=A 浏览器=B → 必须抛错让 worker 强制下线 + 清登录态。"""
        account = self._make_account(sec_uid="MS4wLjABAAAA_account_A_xxxxxxxxxx")
        page = self._make_page()
        with patch(
            "core.douyin.runtime.inbox._probe_own_sec_uid_lightweight",
            new=AsyncMock(return_value="MS4wLjABAAAA_account_B_xxxxxxxxxx"),
        ):
            with self.assertRaises(AccountIdentityMismatch) as ctx:
                await _ensure_account_sec_uid(account, page)
        self.assertEqual(ctx.exception.expected, "MS4wLjABAAAA_account_A_xxxxxxxxxx")
        self.assertEqual(ctx.exception.actual, "MS4wLjABAAAA_account_B_xxxxxxxxxx")
        self.assertEqual(ctx.exception.account_id, "acc-1")

    async def test_first_time_backfill_when_db_empty_and_probe_hits(self):
        account = self._make_account(sec_uid="")
        page = self._make_page()
        with patch(
            "core.douyin.runtime.inbox._probe_own_sec_uid_lightweight",
            new=AsyncMock(return_value="MS4wLjABAAAA_first_login_uid_xxxxxx"),
        ), patch(
            "core.douyin.runtime.inbox._persist_own_sec_uid",
            new=AsyncMock(return_value=True),
        ):
            result = await _ensure_account_sec_uid(account, page)
        self.assertEqual(result, "MS4wLjABAAAA_first_login_uid_xxxxxx")
        # 内存对象也立刻同步上，本轮扫描不用等下次重启
        self.assertEqual(account.sec_uid, "MS4wLjABAAAA_first_login_uid_xxxxxx")

    async def test_returns_none_when_both_db_and_probe_empty(self):
        account = self._make_account(sec_uid="")
        page = self._make_page()
        page.context = MagicMock()
        page.context.cookies = AsyncMock(return_value=[])
        with patch(
            "core.douyin.runtime.inbox._probe_own_sec_uid_lightweight",
            new=AsyncMock(return_value=None),
        ), patch(
            "core.douyin.runtime.inbox._collect_own_sec_uid",
            new=AsyncMock(return_value=None),
        ):
            result = await _ensure_account_sec_uid(account, page)
        self.assertIsNone(result)


class AccountIdentityMismatchTests(unittest.TestCase):
    """异常类本身：必须能被现有 except LoginGateDetected 捕到。"""

    def test_inherits_login_gate_detected(self):
        from core.douyin.runtime.inbox import LoginGateDetected
        err = AccountIdentityMismatch(
            expected="db_xxxxxxxxxxxxxxxxxxxxxxxxxxx",
            actual="page_xxxxxxxxxxxxxxxxxxxxxxxxxx",
            account_id="acc-1",
        )
        self.assertIsInstance(err, LoginGateDetected)
        self.assertIn("身份核对失败", str(err))
        self.assertIn("acc-1", str(err))
