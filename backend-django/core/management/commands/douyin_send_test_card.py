#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""一次性诊断命令：向指定会话发送一条"伪装卡片"原生消息，逆向验证发送协议。

复刻抓包到的卡片 content_json（title/desc/cover_url(base64)/link_url），通过现有
HttpProtocolTransport 的签名机 + encode_send_message_request_pb2(content_override=...)
发送。用于确认抖音对自发卡片消息的接受度（message_type / aweType / 字段校验）。

用法：
  python manage.py douyin_send_test_card \
      --account <account_id> \
      --conversation "0:1:80549827440:3061476426516824" \
      [--card-json path/to/native-card-content.json] \
      [--message-type 7] [--title ...] [--desc ...] [--link ...]

默认从 .trellis 抓包存档读取原样卡片结构（含 base64 封面），最大限度先验证"协议能不能通"。
"""
import asyncio
import json
import os

from django.core.management.base import BaseCommand

_DEFAULT_CARD_JSON = os.path.join(
    os.path.dirname(__file__), '..', '..', '..', '..',
    '.trellis', 'tasks', '06-26-card-client-sync', 'research', 'native-card-content.json',
)


class Command(BaseCommand):
    help = "发送一条伪装卡片测试消息（逆向验证发送协议）"

    def add_arguments(self, parser):
        parser.add_argument('--account', required=True, help='DouyinAccount id')
        parser.add_argument('--conversation', required=True, help='platform_conversation_id')
        parser.add_argument('--card-json', default='', help='卡片 content_json 文件路径（默认用抓包存档）')
        parser.add_argument('--message-type', type=int, default=7, help='单个 message_type（文本=7）')
        parser.add_argument(
            '--message-types', default='',
            help='批量逗号分隔的 message_type 列表，如 "7,1,8,11,12"；设置后忽略 --message-type',
        )
        parser.add_argument('--title', default='', help='覆盖卡片标题')
        parser.add_argument('--desc', default='', help='覆盖卡片描述（中间显示文字）')
        parser.add_argument('--link', default='', help='覆盖跳转链接')
        parser.add_argument('--card-id', default='', help='设置 card_id 字段（企业卡片可能必填）')
        parser.add_argument('--dump-resp', action='store_true', help='打印 send 接口完整原始响应（排查 biz code 含义）')
        parser.add_argument('--tag-desc', action='store_true',
                            help='在 desc 后追加 [type=N] 标记，便于辨认哪条 type 渲染成功')

    def handle(self, *args, **opts):
        asyncio.run(self._run(opts))

    async def _run(self, opts):
        from asgiref.sync import sync_to_async

        from core.douyin.douyin_account_model import DouyinAccount
        from core.douyin.runtime.transport import build_transport
        from core.douyin.runtime.transport.wire.im_send_pb2 import (
            encode_send_message_request_pb2,
        )
        from core.douyin.runtime.transport.wire.im_protocol import (
            decode_send_message_response,
        )

        account = await sync_to_async(
            lambda: DouyinAccount.objects.filter(id=opts['account'], is_deleted=False).first()
        )()
        if account is None:
            self.stderr.write(self.style.ERROR(f"账号不存在: {opts['account']}"))
            return

        card_path = opts['card_json'] or _DEFAULT_CARD_JSON
        with open(card_path, encoding='utf-8') as f:
            card = json.load(f)
        if opts['title']:
            card['title'] = opts['title']
            card['push_detail'] = opts['title']
        if opts['desc']:
            card['desc'] = opts['desc']
        if opts['link']:
            card['link_url'] = opts['link']
        if opts['card_id']:
            card['card_id'] = opts['card_id']

        conv = opts['conversation']

        transport = build_transport()
        await transport.start(account)
        signer = transport._sign

        bd_ticket = {}
        get_bd = getattr(signer, 'get_bd_ticket', None)
        if callable(get_bd):
            try:
                bd_ticket = get_bd() or {}
            except Exception as e:  # noqa: BLE001
                self.stderr.write(self.style.WARNING(f"get_bd_ticket 失败: {e}"))
        if not bd_ticket.get('private_key'):
            self.stderr.write(self.style.ERROR("缺少 bd_ticket.private_key，无法签名发送（账号登录态可能不全）"))
            return

        s_v_web_id = ''
        try:
            cookies = await signer.get_cookies()
            s_v_web_id = (cookies or {}).get('s_v_web_id', '')
        except Exception:  # noqa: BLE001
            pass

        from core.douyin.runtime.transport.http_protocol import _ENDPOINTS, _BASE_IM_HEADERS
        endpoint = _ENDPOINTS['send_message']

        # 决定要试的 message_type 列表
        if opts['message_types'].strip():
            types = [int(x) for x in opts['message_types'].split(',') if x.strip()]
        else:
            types = [int(opts['message_type'])]

        import copy
        for mt in types:
            card_i = copy.deepcopy(card)
            base_desc = card_i.get('desc') or ''
            if opts['tag_desc']:
                card_i['desc'] = f"{base_desc} [type={mt}]".strip()
                card_i['title'] = f"{card_i.get('title','')} (t{mt})".strip()

            self.stdout.write("")
            self.stdout.write(self.style.WARNING(f"───── 发送 message_type={mt} ─────"))
            try:
                body, cm_id, seq_id = await sync_to_async(
                    encode_send_message_request_pb2, thread_sensitive=False
                )(
                    conversation_id=conv,
                    text='',
                    bd_ticket=bd_ticket,
                    s_v_web_id=s_v_web_id,
                    content_override=card_i,
                    message_type=mt,
                )
            except Exception as e:  # noqa: BLE001
                self.stderr.write(self.style.ERROR(f"  type={mt} 编码失败: {e}"))
                continue

            try:
                resp = await signer.signed_fetch(
                    method=endpoint['method'],
                    url=endpoint['url'],
                    body=body,
                    headers=_BASE_IM_HEADERS,
                    use_xhr=True,
                )
            except Exception as e:  # noqa: BLE001
                self.stderr.write(self.style.ERROR(f"  type={mt} 请求异常: {e}"))
                continue

            if not resp.ok or not resp.content:
                self.stderr.write(self.style.ERROR(
                    f"  type={mt} HTTP 失败 status={resp.status} text={resp.text[:200]!r}"))
                continue
            if opts['dump_resp']:
                # 打印完整响应：先试 utf-8 文本，再 hex，便于看 8004 的文字说明
                try:
                    self.stdout.write(f"  [raw text] {resp.content.decode('utf-8', 'replace')[:800]}")
                except Exception:  # noqa: BLE001
                    pass
                self.stdout.write(f"  [raw hex ] {resp.content[:400].hex()}")
            try:
                result = decode_send_message_response(resp.content)
                self.stdout.write(
                    f"  type={mt} → status_code={result.status_code} "
                    f"biz_status_code={result.biz_status_code} "
                    f"biz_status_text={result.biz_status_text!r} "
                    f"server_msg_id={result.server_msg_id} client_msg_id={cm_id}"
                )
            except Exception as e:  # noqa: BLE001
                self.stderr.write(self.style.ERROR(f"  type={mt} 解码响应失败: {e} raw={resp.content[:160]!r}"))
            # 间隔，避免发太快被限流
            await asyncio.sleep(2.0)

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            "全部发送完毕。请到对方抖音查看：哪条 type 渲染成了卡片（标题带 (tN)、描述带 [type=N] 便于辨认）。"
        ))

