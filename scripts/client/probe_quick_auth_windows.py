#!/usr/bin/env python3
"""Probe the shipped Windows browser with disposable blank profiles, without login.

Only CI/synthetic data is used. The sandbox remains enabled and no account, token,
credential store, installed browser, or running user browser is touched.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import time
import urllib.request
import uuid
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def production_args() -> list[str]:
    source = (ROOT / "dyauthreply-client/agent/src/quick_auth/mod.rs").read_text(encoding="utf-8")
    block = re.search(r"const BROWSER_ARGS:.*?= &\[(.*?)\];", source, re.S)
    if not block:
        raise RuntimeError("BROWSER_ARGS was not found")
    args = re.findall(r'"([^"\n]+)"', block[1])
    if args[-1] != "about:blank" or "--remote-debugging-address=127.0.0.1" not in args:
        raise RuntimeError("Unexpected browser launch contract")
    if any("no-sandbox" in item for item in args):
        raise RuntimeError("The probe requires the browser sandbox")
    return args


def download_browser(output: Path) -> tuple[Path, dict]:
    pin = json.loads((ROOT / "dyauthreply-client/agent/quick-auth-component.json").read_text(encoding="utf-8"))
    expected = pin["platforms"]["win64"]
    archive = output / "browser.zip"
    attempts = []
    for template in pin["sources"]:
        url = template.format(version=pin["version"], platform="win64")
        try:
            digest = hashlib.sha256()
            size = 0
            with urllib.request.urlopen(url, timeout=90) as response, archive.open("wb") as dest:
                while chunk := response.read(1024 * 1024):
                    size += len(chunk)
                    if size > expected["size"]:
                        raise RuntimeError("Archive exceeds pinned length")
                    digest.update(chunk)
                    dest.write(chunk)
            if size != expected["size"] or digest.hexdigest() != expected["sha256"]:
                raise RuntimeError("Archive does not match shipped pin")
            attempts.append({"source": url, "verified": True})
            break
        except Exception as error:
            attempts.append({"source": url, "verified": False, "error": str(error)})
    else:
        raise RuntimeError(json.dumps(attempts))
    managed = output / "agent-v2/components/chromium/versions" / (
        pin["version"] + "-x86_64-pc-windows-msvc-" + str(uuid.uuid4())
    )
    managed.mkdir(parents=True)
    with zipfile.ZipFile(archive) as bundle:
        for entry in bundle.infolist():
            destination = (managed / entry.filename).resolve()
            if not destination.is_relative_to(managed.resolve()):
                raise RuntimeError("Archive entry escapes extraction directory")
        bundle.extractall(managed)
    browser = managed / "chrome-win64/chrome.exe"
    if not browser.is_file():
        raise RuntimeError("Pinned archive has no browser executable")
    return browser, {"version": pin["version"], "archive_sha256": expected["sha256"], "sources": attempts}


def run_case(browser: Path, output: Path, name: str, profile: Path, verbose: bool = False) -> dict:
    profile.mkdir(parents=True)
    args = [str(browser), f"--user-data-dir={profile}", *production_args()]
    if verbose:
        args[1:1] = ["--enable-logging=stderr", "--log-level=0"]
    result = {"case": name, "ready": False, "args": args, "profile_chars": len(str(profile)),
              "executable_chars": len(str(browser)), "exit_code": None}
    stdout = output / (name + ".stdout.log")
    stderr = output / (name + ".stderr.log")
    child = None
    started = time.monotonic()
    try:
        with stdout.open("wb") as out, stderr.open("wb") as err:
            child = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=out, stderr=err)
            active = profile / "DevToolsActivePort"
            deadline = started + 20
            while time.monotonic() < deadline:
                code = child.poll()
                if code is not None:
                    result["exit_code"] = code
                    break
                if active.is_file():
                    try:
                        lines = active.read_text(encoding="utf-8").splitlines()
                        port = int(lines[0])
                        if not (0 < port < 65536 and lines[1].startswith("/devtools/browser/")):
                            raise RuntimeError("Unexpected DevTools endpoint")
                        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                        with opener.open(f"http://127.0.0.1:{port}/json/version", timeout=3) as r:
                            version = json.load(r)
                        with opener.open(f"http://127.0.0.1:{port}/json/list", timeout=3) as r:
                            pages = json.load(r)
                        result["browser"] = version.get("Browser")
                        result["blank_page"] = any(p.get("type") == "page" and p.get("url") == "about:blank" for p in pages)
                        result["ready"] = bool(result["blank_page"])
                        break
                    except (OSError, ValueError, IndexError) as error:
                        result["last_probe_error"] = str(error)
                time.sleep(0.1)
            result["elapsed_ms"] = round((time.monotonic() - started) * 1000)
    except Exception as error:
        result["error"] = str(error)
    finally:
        # Only this newly created synthetic browser process and its children.
        if child is not None and child.poll() is None:
            subprocess.run(["taskkill", "/PID", str(child.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            child.wait(timeout=10)
        if stderr.exists():
            result["stderr_tail"] = stderr.read_text(encoding="utf-8", errors="replace")[-16000:]
        try:
            shutil.rmtree(profile)
            result["profile_removed"] = True
        except OSError:
            result["profile_removed"] = False
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("This probe must run on Windows")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = {"os": platform.platform(), "version": platform.version(), "machine": platform.machine(),
              "login_attempted": False, "sandbox_disabled": False, "cases": []}
    try:
        browser, component = download_browser(output)
        report["component"] = component
        report["resource_inventory"] = {p.name: p.stat().st_size for p in browser.parent.iterdir() if p.is_file()}
        report["cases"].append(run_case(browser, output, "production", output / "agent-v2/quick-auth/sessions" / str(uuid.uuid4())))
        report["cases"].append(run_case(browser, output, "unicode-spaces", output / "用户 目录/快捷登录/sessions" / str(uuid.uuid4())))
        report["cases"].append(run_case(browser, output, "diagnostic-logging", output / "diagnostic-profile", verbose=True))
        report["passed"] = all(case["ready"] for case in report["cases"])
    except Exception as error:
        report["error"] = str(error)
        report["passed"] = False
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    for case in report["cases"]:
        print(json.dumps({key: case.get(key) for key in ["case", "ready", "exit_code", "browser", "elapsed_ms"]}))
    print("WINDOWS_BROWSER_STARTUP_PASS=" + str(int(report["passed"])))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
