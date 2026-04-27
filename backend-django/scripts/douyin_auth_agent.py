#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
宿主机常驻 Douyin auth-agent：

- 轮询 backend-django/tmp/douyin-auth-jobs/*.json
- 发现 pending 任务后，在宿主机真实浏览器中完成认证
- 导出 storage_state.json
- 自动调用 Docker backend 导入登录态
"""
from __future__ import annotations

import argparse
import asyncio
import os
import socket
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from core.douyin.runtime.local_auth_jobs import list_jobs, update_job  # noqa: E402
from scripts.douyin_local_auth import DEFAULT_USER_AGENT, run_local_auth  # noqa: E402


async def import_into_docker(account_id: str, output_rel_path: str) -> subprocess.CompletedProcess[str]:
    cmd = [
        "docker",
        "compose",
        "exec",
        "backend",
        "python",
        "manage.py",
        "import_douyin_storage_state",
        account_id,
        f"/app/{output_rel_path}",
    ]
    return subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        check=False,
        capture_output=True,
        text=True,
    )


async def process_job(job: dict, args) -> None:
    account_id = str(job["account_id"])
    update_job(
        account_id,
        status="running",
        agent_host=socket.gethostname(),
        agent_pid=str(os.getpid()),
        result_message="auth-agent 正在执行宿主机认证",
    )

    output_rel_path = job.get("output_rel_path") or f"backend-django/tmp/douyin/{account_id}.json"
    output_path = REPO_ROOT / output_rel_path
    user_data_dir = Path.home() / ".dyauthreply" / "douyin-auth" / str(job.get("user_data_dir") or account_id)

    try:
        result = await run_local_auth(
            account_id=account_id,
            output=output_path,
            user_data_dir=user_data_dir,
            channel=args.channel,
            user_agent=args.user_agent,
            timeout=args.timeout,
            interactive=False,
        )
        if not result["ready"]:
            update_job(
                account_id,
                status="failed",
                output_path=str(output_path),
                result_message="登录态导出完成，但页面仍像登录门面，未确认进入业务页",
            )
            return

        imported = await import_into_docker(account_id, output_rel_path)
        if imported.returncode != 0:
            update_job(
                account_id,
                status="failed",
                output_path=str(output_path),
                result_message=(
                    "导入 Docker backend 失败: "
                    f"{(imported.stderr or imported.stdout).strip()[:500]}"
                ),
            )
            return

        update_job(
            account_id,
            status="succeeded",
            output_path=str(output_path),
            result_message=(imported.stdout or "已导入 Docker backend").strip(),
        )
    except Exception as exc:  # noqa: BLE001
        update_job(
            account_id,
            status="failed",
            output_path=str(output_path),
            result_message=f"{type(exc).__name__}: {exc}",
        )


async def amain(args) -> int:
    print(f"[auth-agent] watching local auth jobs in {BACKEND_ROOT / 'tmp' / 'douyin-auth-jobs'}")
    while True:
        pending_jobs = list_jobs(statuses={"pending"})
        if pending_jobs:
            for job in pending_jobs:
                if job.get("status") != "pending":
                    continue
                print(f"[auth-agent] processing account={job['account_id']} nickname={job.get('nickname')}")
                await process_job(job, args)
        await asyncio.sleep(args.poll_interval)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Douyin host-side local auth agent")
    parser.add_argument("--poll-interval", type=int, default=3, help="轮询 job 目录的间隔秒数")
    parser.add_argument("--timeout", type=int, default=300, help="单个认证任务最长等待秒数")
    parser.add_argument("--channel", default="chrome", help="Playwright browser channel")
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT, help="浏览器 UA")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return asyncio.run(amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
