#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
分析 sniffer 落盘的 JSONL，输出抖音 IM 协议地图。

典型用法：
    # 分析最新一份 dump（自动找最近的 session）
    python manage.py douyin_sniff_analyze

    # 指定账号
    python manage.py douyin_sniff_analyze --account-id 7

    # 指定文件
    python manage.py douyin_sniff_analyze --file /var/lib/zq-platform/douyin/sniff/account_7/session_1730000000.jsonl

    # 输出每个 WS URL 的前 N 条 hex 样本
    python manage.py douyin_sniff_analyze --account-id 7 --hex-samples 5

    # 把分析报告也落到文件
    python manage.py douyin_sniff_analyze --account-id 7 --report /tmp/im_protocol_map.md
"""
from __future__ import annotations

import base64
import json
from collections import Counter, defaultdict
from io import StringIO
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


# 与 sniffer.py 一致；这里独立保留一份，避免命令行环境下的 import 复杂度
_DEFAULT_SENSITIVE_HEADERS = (
    "cookie",
    "authorization",
    "x-ms-stub",
    "x-khronos",
    "x-argus",
    "x-gorgon",
    "x-mstoken",
    "x-tt-token",
    "x-bogus",
    "_signature",
    "a_bogus",
    "mstoken",
    "passport-csrf-token",
)

_INTERESTING_HEADERS = (
    "x-ms-stub",
    "x-khronos",
    "x-argus",
    "x-gorgon",
    "x-bogus",
    "a_bogus",
    "_signature",
    "mstoken",
    "x-tt-token",
    "passport-csrf-token",
    "x-secsdk-csrf-token",
    "user-agent",
    "referer",
    "content-type",
)


def _sniffer_root() -> Path:
    explicit = getattr(settings, "DOUYIN_SNIFFER_DIR", "") or ""
    if explicit:
        return Path(explicit)
    return Path(getattr(settings, "DOUYIN_DATA_DIR", "/var/lib/zq-platform/douyin")) / "sniff"


def _find_latest_session(account_id: Optional[str]) -> Optional[Path]:
    root = _sniffer_root()
    if not root.exists():
        return None
    if account_id:
        dirs = [root / f"account_{account_id}"]
    else:
        dirs = sorted([d for d in root.iterdir() if d.is_dir()])
    candidates: list[Path] = []
    for d in dirs:
        if not d.exists():
            continue
        candidates.extend(d.glob("session_*.jsonl"))
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def _b64_to_bytes(b64: Optional[str]) -> bytes:
    if not b64:
        return b""
    try:
        return base64.b64decode(b64)
    except Exception:
        return b""


def _hex_preview(data: bytes, width: int = 64) -> str:
    if not data:
        return "<empty>"
    sample = data[:width]
    hex_part = sample.hex(" ")
    ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in sample)
    return f"{hex_part}\n  ascii: {ascii_part!r}"


def _try_text(data: bytes) -> Optional[str]:
    if not data:
        return None
    try:
        s = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    # 看起来像 protobuf 不可见字符比例高的就跳过
    printable = sum(1 for c in s if c.isprintable() or c in "\r\n\t")
    if printable / max(1, len(s)) < 0.85:
        return None
    return s


def _normalize_url(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


class Command(BaseCommand):
    help = "分析 IM sniffer 抓到的 JSONL，输出协议地图"

    def add_arguments(self, parser):
        parser.add_argument("--account-id", help="指定账号 ID（默认取最新 dump）")
        parser.add_argument("--file", help="直接指定 JSONL 文件路径")
        parser.add_argument(
            "--hex-samples",
            type=int,
            default=3,
            help="每个 WS URL 输出的前 N 条 hex 样本（默认 3）",
        )
        parser.add_argument(
            "--top-headers",
            type=int,
            default=10,
            help="HTTP 请求头按出现频率取前 N 个展示（默认 10）",
        )
        parser.add_argument(
            "--report",
            help="额外把分析报告写到指定文件（Markdown 格式）",
        )
        parser.add_argument(
            "--show-sensitive",
            action="store_true",
            help="展示敏感 header 原值（cookie / token / 签名等）",
        )

    def handle(self, *args, **opts):
        account_id = opts.get("account_id")
        file_arg = opts.get("file")

        if file_arg:
            path = Path(file_arg)
            if not path.exists():
                raise CommandError(f"文件不存在: {path}")
        else:
            path = _find_latest_session(account_id)
            if path is None:
                root = _sniffer_root()
                raise CommandError(
                    f"找不到 dump 文件。检查 DOUYIN_SNIFFER_ENABLED=true 是否生效，"
                    f"以及 {root} 下是否有 account_*/session_*.jsonl"
                )

        out = StringIO()
        self._analyze_file(
            path,
            out,
            hex_samples=int(opts.get("hex_samples") or 3),
            top_headers=int(opts.get("top_headers") or 10),
            show_sensitive=bool(opts.get("show_sensitive")),
        )
        report = out.getvalue()
        self.stdout.write(report)

        report_path = opts.get("report")
        if report_path:
            rp = Path(report_path)
            rp.parent.mkdir(parents=True, exist_ok=True)
            rp.write_text(report, encoding="utf-8")
            self.stdout.write(self.style.SUCCESS(f"\n报告已写入 {rp}"))

    # ---------------- 分析 ----------------
    def _analyze_file(
        self,
        path: Path,
        out: StringIO,
        *,
        hex_samples: int,
        top_headers: int,
        show_sensitive: bool,
    ) -> None:
        events = list(self._iter_events(path))
        if not events:
            out.write(f"# 抖音 IM Sniffer 分析\n\n文件 {path} 为空。\n")
            return

        session_open = next((e for e in events if e.get("type") == "session_open"), None)
        session_close = next((e for e in events if e.get("type") == "session_close"), None)

        out.write("# 抖音 IM Sniffer 分析报告\n\n")
        out.write(f"- 文件: `{path}`\n")
        out.write(f"- 总事件数: **{len(events)}**\n")
        if session_open:
            out.write(f"- 会话开始: {session_open.get('ts')}\n")
            out.write(
                f"- 关键词列表: {session_open.get('url_keywords')}\n"
            )
        if session_close:
            out.write(f"- 会话结束统计: `{session_close.get('stats')}`\n")
        out.write("\n")

        self._section_websockets(events, out, hex_samples=hex_samples)
        self._section_http(
            events,
            out,
            top_headers=top_headers,
            show_sensitive=show_sensitive,
        )
        self._section_cookies(events, out, show_sensitive=show_sensitive)
        self._section_recommendation(events, out)

    def _iter_events(self, path: Path):
        with open(path, "r", encoding="utf-8") as fp:
            for line in fp:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue

    # ---------------- WS ----------------
    def _section_websockets(self, events, out: StringIO, *, hex_samples: int) -> None:
        ws_events = [e for e in events if e.get("type", "").startswith("ws_")]
        if not ws_events:
            out.write("## WebSocket\n\n_未捕获到任何 WebSocket 流量_。\n\n")
            return

        by_url: dict[str, dict] = defaultdict(
            lambda: {
                "open": 0,
                "send": 0,
                "recv": 0,
                "close": 0,
                "send_lens": [],
                "recv_lens": [],
                "send_samples": [],
                "recv_samples": [],
            }
        )
        for e in ws_events:
            url = e.get("url", "")
            stat = by_url[url]
            t = e.get("type")
            if t == "ws_open":
                stat["open"] += 1
            elif t == "ws_send":
                stat["send"] += 1
                stat["send_lens"].append(e.get("len", 0))
                if len(stat["send_samples"]) < hex_samples:
                    stat["send_samples"].append(e)
            elif t == "ws_recv":
                stat["recv"] += 1
                stat["recv_lens"].append(e.get("len", 0))
                if len(stat["recv_samples"]) < hex_samples:
                    stat["recv_samples"].append(e)
            elif t == "ws_close":
                stat["close"] += 1

        out.write("## WebSocket 长连接（最关心的目标）\n\n")
        out.write("| URL | open | send | recv | close | 平均 recv 字节 |\n")
        out.write("|---|---:|---:|---:|---:|---:|\n")
        for url, stat in sorted(by_url.items(), key=lambda kv: -kv[1]["recv"]):
            avg_recv = (
                sum(stat["recv_lens"]) // max(1, len(stat["recv_lens"]))
                if stat["recv_lens"]
                else 0
            )
            out.write(
                f"| `{url}` | {stat['open']} | {stat['send']} | {stat['recv']} | "
                f"{stat['close']} | {avg_recv} |\n"
            )
        out.write("\n")

        for url, stat in by_url.items():
            if not stat["send_samples"] and not stat["recv_samples"]:
                continue
            out.write(f"### WS `{url}`\n\n")
            for label, samples in [
                ("client → server (send)", stat["send_samples"]),
                ("server → client (recv)", stat["recv_samples"]),
            ]:
                if not samples:
                    continue
                out.write(f"**{label}（前 {len(samples)} 帧）**\n\n")
                for idx, evt in enumerate(samples, 1):
                    raw = _b64_to_bytes(evt.get("b64"))
                    enc = evt.get("encoding", "?")
                    n = evt.get("len", 0)
                    truncated = " (truncated)" if evt.get("truncated") else ""
                    text = _try_text(raw) if enc == "text" else None
                    out.write(f"```\n[{idx}] len={n} encoding={enc}{truncated}\n")
                    if text is not None:
                        sample_text = text[:512]
                        out.write(f"text: {sample_text}\n")
                        if len(text) > 512:
                            out.write("...\n")
                    else:
                        out.write(f"hex:   {_hex_preview(raw, width=80)}\n")
                    out.write("```\n\n")

    # ---------------- HTTP ----------------
    def _section_http(
        self,
        events,
        out: StringIO,
        *,
        top_headers: int,
        show_sensitive: bool,
    ) -> None:
        reqs = [e for e in events if e.get("type") == "http_request"]
        resps = [e for e in events if e.get("type") == "http_response"]

        if not reqs and not resps:
            out.write("## HTTP\n\n_没有命中关键词的 HTTP 流量_。\n\n")
            return

        out.write("## HTTP 请求统计\n\n")
        endpoints = Counter(_normalize_url(r.get("url", "")) for r in reqs)
        out.write("| Endpoint | 调用次数 |\n|---|---:|\n")
        for ep, n in endpoints.most_common(40):
            out.write(f"| `{ep}` | {n} |\n")
        out.write("\n")

        # Header 频次（用来发现签名 header）
        header_counter: Counter = Counter()
        header_samples: dict[str, list[str]] = defaultdict(list)
        for r in reqs:
            for k, v in (r.get("headers") or {}).items():
                kl = k.lower()
                header_counter[kl] += 1
                if len(header_samples[kl]) < 3:
                    header_samples[kl].append(v)

        out.write(f"## HTTP 请求 Header 频次（Top {top_headers}）\n\n")
        out.write("| Header | 出现次数 | 样例 |\n|---|---:|---|\n")
        for h, n in header_counter.most_common(top_headers):
            samples = header_samples.get(h) or []
            sample = samples[0] if samples else ""
            if h in _DEFAULT_SENSITIVE_HEADERS and not show_sensitive:
                sample = self._mask(sample)
            out.write(f"| `{h}` | {n} | `{sample[:80]}` |\n")
        out.write("\n")

        # 重点 header（即使频次不高也单独列出）
        out.write("## 重点 Header（签名/凭证候选）\n\n")
        out.write("| Header | 命中次数 | 样例（脱敏） |\n|---|---:|---|\n")
        any_hit = False
        for h in _INTERESTING_HEADERS:
            n = header_counter.get(h, 0)
            if n == 0:
                continue
            any_hit = True
            samples = header_samples.get(h) or []
            sample = samples[0] if samples else ""
            if h in _DEFAULT_SENSITIVE_HEADERS and not show_sensitive:
                sample = self._mask(sample)
            out.write(f"| `{h}` | {n} | `{sample[:120]}` |\n")
        if not any_hit:
            out.write("| _（未命中预设的签名 header）_ | | |\n")
        out.write("\n")

        # POST body 抽样（专门看 send_message 之类）
        # 抖音创作者中心 IM 的发送 endpoint 是 imapi.douyin.com/v1/message/send，
        # 同时兼容老版本 /aweme/v1/im/send_message/ 等写法。
        _SEND_LIKE_KEYWORDS = (
            "/message/send",
            "send_message",
            "send_msg",
            "/im/send",
            "/send/",
            "/v1/send",
            "/v2/send",
        )
        send_like = [
            r
            for r in reqs
            if any(kw in (r.get("url", "")).lower() for kw in _SEND_LIKE_KEYWORDS)
        ]
        # 关键 endpoint 的 request + response 配对样本（让发送消息体、token 响应一次看清）
        self._section_critical_pairs(reqs, resps, out, show_sensitive=show_sensitive)

        if send_like:
            out.write(f"## 发送消息相关请求样本（{len(send_like)} 条）\n\n")
            for idx, r in enumerate(send_like[:5], 1):
                out.write(f"### {idx}. `{r.get('method')} {r.get('url')}`\n\n")
                out.write(
                    "```\n"
                    f"resource_type: {r.get('resource_type')}\n"
                    f"post_len: {r.get('post_len')} encoding={r.get('post_encoding')} "
                    f"truncated={r.get('post_truncated')}\n"
                )
                raw = _b64_to_bytes(r.get("post_b64"))
                txt = _try_text(raw)
                if txt is not None:
                    out.write(f"post_text: {txt[:512]}\n")
                else:
                    out.write(f"post_hex: {_hex_preview(raw, width=80)}\n")
                out.write("```\n\n")
                interesting = {
                    k: v
                    for k, v in (r.get("headers") or {}).items()
                    if k.lower() in _INTERESTING_HEADERS
                }
                if interesting:
                    out.write("interesting headers:\n```\n")
                    for k, v in interesting.items():
                        if k.lower() in _DEFAULT_SENSITIVE_HEADERS and not show_sensitive:
                            v = self._mask(v)
                        out.write(f"{k}: {v}\n")
                    out.write("```\n\n")

    # ---------------- 关键 endpoint 配对 ----------------
    _CRITICAL_ENDPOINTS = (
        # 发送消息
        ("send", ("/message/send", "send_message", "/im/send")),
        # 拿 IM token / csrf token（后续协议层鉴权关键）
        ("im_token", ("creator/im/user_token", "/web/api/v1/im/token")),
        # 增量拉取（看 cursor 字段格式）
        ("get_by_user", ("/message/get_by_user",)),
        # 单会话历史
        ("get_by_conversation", ("/message/get_by_conversation",)),
        # 标记已读
        ("mark_read", ("/conversation/mark_read",)),
        # 会话列表
        ("conversation_list", ("/get_conversation_list", "/conversation/list")),
        # 拿用户资料
        ("user_detail", ("/im/user_detail",)),
    )

    def _section_critical_pairs(
        self, reqs, resps, out: StringIO, *, show_sensitive: bool
    ) -> None:
        """对几类关键 endpoint，打印第一条 request + response 的完整 body。"""
        # 把响应按 url 分桶（同 url 多次响应取第一条匹配该请求的）
        # 简单近似：按 (method,url) 配对，依赖 http_response 出现顺序
        out.write("## 关键 Endpoint 请求/响应样本\n\n")
        out.write(
            "_自动按 endpoint 类型抽取首个请求与对应响应，方便定位字段结构。_\n\n"
        )
        for label, kws in self._CRITICAL_ENDPOINTS:
            req = next(
                (
                    r
                    for r in reqs
                    if any(k in (r.get("url") or "").lower() for k in kws)
                ),
                None,
            )
            if not req:
                continue
            resp = next(
                (
                    rp
                    for rp in resps
                    if any(k in (rp.get("url") or "").lower() for k in kws)
                ),
                None,
            )
            out.write(f"### `{label}`：`{req.get('method')} {self._short_url(req.get('url'))}`\n\n")
            # 请求体
            raw_post = _b64_to_bytes(req.get("post_b64"))
            txt_post = _try_text(raw_post)
            out.write("**request**\n\n```\n")
            out.write(f"url: {req.get('url')}\n")
            out.write(
                f"post_len: {req.get('post_len')} encoding={req.get('post_encoding')} truncated={req.get('post_truncated')}\n"
            )
            if txt_post is not None:
                pretty = self._pretty_json(txt_post) or txt_post
                out.write(f"body:\n{pretty[:2000]}\n")
            elif raw_post:
                out.write(f"hex: {_hex_preview(raw_post, width=80)}\n")
            else:
                out.write("body: <empty>\n")
            out.write("```\n\n")
            # 响应
            if resp:
                raw_resp = _b64_to_bytes(resp.get("body_b64"))
                txt_resp = _try_text(raw_resp)
                out.write(
                    f"**response** status={resp.get('status')} ok={resp.get('ok')}\n\n```\n"
                )
                if txt_resp is not None:
                    pretty = self._pretty_json(txt_resp) or txt_resp
                    out.write(f"body:\n{pretty[:2000]}\n")
                elif raw_resp:
                    out.write(f"hex: {_hex_preview(raw_resp, width=80)}\n")
                else:
                    out.write("body: <empty>\n")
                # 关键响应 header
                rh = resp.get("headers") or {}
                ct = rh.get("content-type") or rh.get("Content-Type")
                csrf = rh.get("x-secsdk-csrf-token") or rh.get("X-Secsdk-Csrf-Token")
                if ct:
                    out.write(f"content-type: {ct}\n")
                if csrf:
                    masked = (
                        csrf if show_sensitive else self._mask(csrf)
                    )
                    out.write(f"x-secsdk-csrf-token: {masked}\n")
                out.write("```\n\n")
            else:
                out.write("**response** _未捕获到对应响应_\n\n")

    @staticmethod
    def _short_url(url: str | None) -> str:
        if not url:
            return ""
        # 去掉 query，避免长签名串挤占视觉
        return url.split("?", 1)[0]

    @staticmethod
    def _pretty_json(text: str) -> str | None:
        try:
            return json.dumps(json.loads(text), ensure_ascii=False, indent=2)
        except Exception:
            return None

    # ---------------- Cookie ----------------
    def _section_cookies(self, events, out: StringIO, *, show_sensitive: bool) -> None:
        cookies_events = [e for e in events if e.get("type") == "cookies"]
        if not cookies_events:
            return
        out.write("## Cookie 快照\n\n")
        for evt in cookies_events:
            phase = evt.get("phase")
            cookies = evt.get("cookies") or []
            names = sorted({c.get("name") for c in cookies if c.get("name")})
            out.write(
                f"- phase=**{phase}** 抖音/字节域 cookie 共 {len(cookies)} 个；"
                f"name 集合: `{names}`\n"
            )
        out.write("\n")
        # 列出最关键的几个 cookie 值（不显示完整值）
        last = cookies_events[-1]
        cookies = last.get("cookies") or []
        watch = (
            "sessionid_ss",
            "sessionid",
            "sid_guard",
            "sid_tt",
            "sid_ucp_v1",
            "passport_csrf_token",
            "passport_csrf_token_default",
            "msToken",
            "ttwid",
            "odin_tt",
            "s_v_web_id",
        )
        rows = []
        for c in cookies:
            name = c.get("name")
            if name in watch:
                value = c.get("value", "")
                if not show_sensitive:
                    value = self._mask(value)
                rows.append((name, value, c.get("domain"), c.get("expires")))
        if rows:
            out.write("| name | value | domain | expires |\n|---|---|---|---:|\n")
            for n, v, d, exp in rows:
                out.write(f"| `{n}` | `{v}` | `{d}` | {exp} |\n")
            out.write("\n")

    # ---------------- 结论建议 ----------------
    def _section_recommendation(self, events, out: StringIO) -> None:
        ws_recv = sum(1 for e in events if e.get("type") == "ws_recv")
        ws_send = sum(1 for e in events if e.get("type") == "ws_send")
        send_keywords = (
            "/message/send",
            "send_message",
            "send_msg",
            "/im/send",
            "/send/",
            "/v1/send",
            "/v2/send",
        )
        send_like = sum(
            1
            for e in events
            if e.get("type") == "http_request"
            and any(kw in (e.get("url", "")).lower() for kw in send_keywords)
        )
        out.write("## 下一步建议\n\n")
        if ws_recv > 0:
            out.write(
                f"- 已捕获 **{ws_recv} 帧** server→client 帧，**{ws_send} 帧** client→server 帧。\n"
                "  这就是接收消息的长连接，可以作为协议化改造接收侧的入口（旁路监听 → 直接解 protobuf）。\n"
            )
        else:
            out.write(
                "- **没抓到 WS 帧**：可能是关键词没匹配到 URL；试试在 `DOUYIN_SNIFFER_URL_KEYWORDS` 里加 `wss` 或具体域名再跑一遍。\n"
            )
        if send_like > 0:
            out.write(
                f"- 共 **{send_like}** 条疑似发送消息的 HTTP 请求。优先看 post body 是 protobuf 还是 form-encoded，"
                "结合上面的『重点 Header』决定走『浏览器签名机』还是复现签名。\n"
            )
        else:
            out.write(
                "- **没抓到发送类请求**：要么没在 sniffer 期间真正发送一条消息，要么 URL 关键词没覆盖到。"
                "建议在抓包窗口内手动发送一条文本回复，再重新分析。\n"
            )
        out.write(
            "- 如果重点 Header 里出现 `x-bogus`、`a_bogus`、`_signature`、`x-argus`、`x-gorgon`、`mstoken`，"
            "说明发送侧需要签名；推荐用 `page.evaluate` 复用浏览器内已加载的签名函数（成本最低）。\n"
        )
        out.write(
            "- 验证完协议后，可按 `transport/` 目录抽出 `AccountTransport` 抽象，先把接收侧切到 WS 旁路解码、"
            "发送侧仍走浏览器，这就是阶段 2/3 的最小落地路径。\n"
        )

    @staticmethod
    def _mask(value: str) -> str:
        if not value:
            return ""
        if len(value) <= 8:
            return "*" * len(value)
        return f"{value[:4]}...{value[-4:]}(len={len(value)})"
