#!/usr/bin/env python3
"""Smoke test the packaged launcher by booting it and polling the health API."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


def _health_ready(port: int) -> bool:
    health_url = f"http://127.0.0.1:{port}/api/client/v1/health"
    try:
        with urllib.request.urlopen(health_url, timeout=2) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            payload = json.loads(body)
            return resp.status == 200 and payload.get("ok") is True
    except Exception:
        return False


def _terminate_tree(proc: subprocess.Popen[bytes | str], *, is_windows: bool) -> None:
    if proc.poll() is not None:
        return
    if is_windows:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
            check=False,
            timeout=15,
        )
        return
    proc.terminate()
    try:
        proc.wait(timeout=10)
        return
    except subprocess.TimeoutExpired:
        proc.kill()


def _request_shutdown(port: int) -> None:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/client/v1/lifecycle/shutdown",
        method="POST",
        data=b"",
    )
    try:
        urllib.request.urlopen(req, timeout=3).read()
    except Exception:
        pass


def _print_log_excerpt(data_dir: Path) -> None:
    log_path = data_dir / "logs" / "launcher.log"
    if not log_path.is_file():
        print(f"[smoke] launcher log missing: {log_path}", file=sys.stderr)
        return
    print(f"[smoke] launcher log: {log_path}", file=sys.stderr)
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        print(f"[smoke] failed to read launcher log: {exc}", file=sys.stderr)
        return
    tail = lines[-120:]
    for line in tail:
        print(line, file=sys.stderr)


def _print_stdout_excerpt(stdout_path: Path) -> None:
    if not stdout_path.is_file():
        return
    print(f"[smoke] launcher stdout log: {stdout_path}", file=sys.stderr)
    try:
        lines = stdout_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        print(f"[smoke] failed to read launcher stdout log: {exc}", file=sys.stderr)
        return
    for line in lines[-120:]:
        print(line, file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke test packaged launcher")
    parser.add_argument("launcher_path", help="Path to packaged launcher executable")
    parser.add_argument("--port", type=int, default=8765, help="Health-check port")
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=120,
        help="Seconds to wait for the launcher health endpoint",
    )
    parser.add_argument(
        "--data-dir",
        default="",
        help="Optional client data dir. Defaults to a disposable tmp path.",
    )
    args = parser.parse_args()

    launcher_path = Path(args.launcher_path).resolve()
    if not launcher_path.is_file():
        raise SystemExit(f"ERROR: launcher not found: {launcher_path}")

    if args.data_dir:
        data_dir = Path(args.data_dir).resolve()
    else:
        data_dir = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / f"dyauthreply-smoke-{int(time.time())}"

    if data_dir.exists():
        shutil.rmtree(data_dir, ignore_errors=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = data_dir / "smoke-launcher.stdout.log"

    cmd = [
        str(launcher_path),
        "--no-worker",
        "--host",
        "127.0.0.1",
        "--port",
        str(args.port),
        "--data-dir",
        str(data_dir),
    ]
    print(f"[smoke] starting launcher: {' '.join(cmd)}")

    creationflags = 0
    if sys.platform == "win32":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

    with stdout_path.open("w", encoding="utf-8", errors="replace") as stdout_fp:
        proc = subprocess.Popen(
            cmd,
            stdout=stdout_fp,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags,
        )

        try:
            deadline = time.time() + max(1, args.timeout_seconds)
            while time.time() < deadline:
                if _health_ready(args.port):
                    print("[smoke] launcher health check passed")
                    _request_shutdown(args.port)
                    return
                if proc.poll() is not None:
                    break
                time.sleep(1)

            _print_stdout_excerpt(stdout_path)
            _print_log_excerpt(data_dir)
            exit_code = proc.poll()
            if exit_code is None:
                raise SystemExit("ERROR: launcher health check timed out")
            raise SystemExit(f"ERROR: launcher exited before health check succeeded (code={exit_code})")
        finally:
            _terminate_tree(proc, is_windows=sys.platform == "win32")
            shutil.rmtree(data_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
