#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
在宿主机本地浏览器中完成 Douyin 创作者中心登录，并导出 Playwright storage_state.json。
"""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Any


LOGIN_GATE_HINTS = ("扫码登录", "验证码登录", "密码登录", "登录/注册", "我是创作者")
BUSINESS_HINTS = (
    "创作者中心",
    "创作者服务中心",
    "数据中心",
    "内容管理",
    "作品管理",
    "私信管理",
    "直播管理",
    "电商带货",
    "数据看板",
)
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


async def _page_text(page, limit: int = 2500) -> str:
    try:
        text = await page.evaluate(f"() => (document.body?.innerText || '').slice(0, {limit})")
    except Exception:
        return ""
    return (text or "").replace("\n", "")


async def looks_like_login_gate(page) -> bool:
    text = await _page_text(page)
    return any(hint in text for hint in LOGIN_GATE_HINTS)


async def looks_like_business_shell(page) -> bool:
    text = await _page_text(page)
    return any(hint in text for hint in BUSINESS_HINTS)


async def wait_for_login_ready(page, *, timeout: int) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if not await looks_like_login_gate(page) and await looks_like_business_shell(page):
            return True
        await asyncio.sleep(2)
    return False


async def run_local_auth(
    *,
    account_id: str,
    output: str | Path,
    user_data_dir: str | Path | None = None,
    channel: str = "chrome",
    user_agent: str = DEFAULT_USER_AGENT,
    timeout: int = 300,
    interactive: bool = True,
) -> dict[str, Any]:
    from playwright.async_api import async_playwright

    output_path = Path(output).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    profile_dir = (
        Path(user_data_dir).expanduser().resolve()
        if user_data_dir
        else (Path.home() / ".dyauthreply" / "douyin-auth" / account_id)
    )
    profile_dir.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        launch_kwargs = {
            "headless": False,
            "user_agent": user_agent,
            "viewport": {"width": 1440, "height": 900},
            "locale": "zh-CN",
            "timezone_id": "Asia/Shanghai",
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
            ],
        }
        if channel:
            launch_kwargs["channel"] = channel

        context = await p.chromium.launch_persistent_context(
            str(profile_dir),
            **launch_kwargs,
        )
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            window.chrome = window.chrome || { runtime: {} };
            Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
        """)

        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto("https://creator.douyin.com/", wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(2)

        if interactive:
            print("")
            print("=== Douyin Local Auth ===")
            print(f"account_id   : {account_id}")
            print(f"user_data_dir: {profile_dir}")
            print(f"output       : {output_path}")
            print("")
            print("请在打开的浏览器中完成扫码/人工验证。")
            print("确认你已经进入可用的创作者中心业务页后，回到终端按 Enter 导出 storage_state。")
            input()
            ready = not await looks_like_login_gate(page)
        else:
            print(f"[auth-agent] 等待宿主机浏览器完成登录 account={account_id} timeout={timeout}s")
            ready = await wait_for_login_ready(page, timeout=timeout)

        gate = await looks_like_login_gate(page)
        state = await context.storage_state(path=str(output_path))
        await context.close()

        return {
            "ready": ready and not gate,
            "gate": gate,
            "output": str(output_path),
            "user_data_dir": str(profile_dir),
            "cookies": len(state.get("cookies") or []),
            "origins": len(state.get("origins") or []),
        }


async def amain(args) -> int:
    result = await run_local_auth(
        account_id=args.account_id,
        output=args.output,
        user_data_dir=args.user_data_dir,
        channel=args.channel,
        user_agent=args.user_agent,
        timeout=args.timeout,
        interactive=True,
    )

    print("")
    print(f"已导出 storage_state: {result['output']}")
    print(f"cookies={result['cookies']} origins={result['origins']}")
    if not result["ready"]:
        print("警告：导出时页面仍像登录门面，后续导入后可能仍需重新验证。")
    else:
        print("页面看起来已离开登录门面，可继续导入到 Docker 后端。")
    print("")
    cwd = Path.cwd().resolve()
    output_path = Path(result["output"])
    print("下一步（在项目根目录执行）:")
    try:
        relative_output = output_path.relative_to(cwd)
        print(
            "docker compose exec backend "
            f"python manage.py import_douyin_storage_state {args.account_id} "
            f"/app/{relative_output}"
        )
    except ValueError:
        print(
            "请先把导出的 storage_state.json 放到仓库挂载目录内（例如 backend-django/tmp/douyin/），"
            "再在 Docker backend 容器里执行 import_douyin_storage_state。"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="本机 Douyin 扫码认证并导出 storage_state")
    parser.add_argument("--account-id", required=True, help="DouyinAccount ID")
    parser.add_argument("--output", required=True, help="导出的 storage_state.json 路径")
    parser.add_argument(
        "--user-data-dir",
        help="本地持久化浏览器目录；默认 ~/.dyauthreply/douyin-auth/<account_id>",
    )
    parser.add_argument(
        "--channel",
        default="chrome",
        help="Playwright browser channel，默认 chrome；可改为 msedge / chrome-beta",
    )
    parser.add_argument(
        "--user-agent",
        default=DEFAULT_USER_AGENT,
        help="浏览器 UA",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="自动等待登录完成的最长秒数（仅 auth-agent 使用）",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return asyncio.run(amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
