#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: message_service.py
@Desc: 消息服务
"""
"""
消息服务
"""
import logging
import re
from datetime import datetime
from typing import List, Dict, Any, Optional

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction

from .message_model import Message

logger = logging.getLogger(__name__)


class NotifyService:
    """
    通知服务
    
    支持的渠道:
    - site: 站内消息（必须）
    - email: 邮件
    - sms: 短信
    - wechat: 企业微信
    - dingtalk: 钉钉
    - feishu: 飞书
    """

    @staticmethod
    def send(
            recipient_ids: List[str],
            title: str,
            content: str,
            channels: List[str] = None,
            msg_type: str = 'system',
            context: Dict[str, Any] = None,
            link_type: str = '',
            link_id: str = '',
            sender_id: str = None,
    ) -> Dict[str, bool]:
        """
        发送通知
        
        Args:
            recipient_ids: 接收人ID列表
            title: 消息标题
            content: 消息内容
            channels: 发送渠道列表，默认 ['site']
            msg_type: 消息类型
            context: 模板变量上下文
            link_type: 关联类型
            link_id: 关联ID
            sender_id: 发送人ID
            
        Returns:
            Dict[str, bool]: 各渠道发送结果
        """
        if channels is None:
            channels = ['site']

        # 解析模板变量
        if context:
            title = NotifyService._parse_template(title, context)
            content = NotifyService._parse_template(content, context)

        results = {}

        for channel in channels:
            try:
                if channel == 'site':
                    results['site'] = NotifyService._send_site_message(
                        recipient_ids, title, content, msg_type,
                        link_type, link_id, sender_id
                    )
                elif channel == 'email':
                    results['email'] = NotifyService._send_email(
                        recipient_ids, title, content
                    )
                elif channel == 'sms':
                    results['sms'] = NotifyService._send_sms(
                        recipient_ids, title, content
                    )
                elif channel == 'wechat':
                    results['wechat'] = NotifyService._send_wechat(
                        recipient_ids, title, content
                    )
                elif channel == 'dingtalk':
                    results['dingtalk'] = NotifyService._send_dingtalk(
                        recipient_ids, title, content
                    )
                elif channel == 'feishu':
                    results['feishu'] = NotifyService._send_feishu(
                        recipient_ids, title, content
                    )
                else:
                    logger.warning(f"未知的通知渠道: {channel}")
                    results[channel] = False
            except Exception as e:
                logger.error(f"发送通知失败 [{channel}]: {e}")
                results[channel] = False

        return results

    @staticmethod
    def _parse_template(template: str, context: Dict[str, Any]) -> str:
        """
        解析模板变量
        
        支持的变量格式:
        - ${variable} - 直接变量
        - ${form.field} - 表单字段
        """
        if not template or not context:
            return template

        def replace_var(match):
            var_path = match.group(1)

            # 处理嵌套路径 (如 form.field)
            parts = var_path.split('.')
            value = context

            for part in parts:
                if isinstance(value, dict):
                    value = value.get(part, '')
                else:
                    value = ''
                    break

            return str(value) if value else ''

        return re.sub(r'\$\{([^}]+)\}', replace_var, template)

    @staticmethod
    @transaction.atomic
    def _send_site_message(
            recipient_ids: List[str],
            title: str,
            content: str,
            msg_type: str,
            link_type: str,
            link_id: str,
            sender_id: str,
    ) -> bool:
        """发送站内消息"""
        from core.user.user_model import User

        try:
            sender = None
            if sender_id:
                try:
                    sender = User.objects.get(id=sender_id)
                except User.DoesNotExist:
                    pass

            messages = []
            for recipient_id in recipient_ids:
                try:
                    recipient = User.objects.get(id=recipient_id)
                    msg = Message(
                        recipient=recipient,
                        title=title,
                        content=content,
                        msg_type=msg_type,
                        link_type=link_type,
                        link_id=link_id,
                        sender=sender,
                    )
                    messages.append(msg)
                except User.DoesNotExist:
                    logger.warning(f"接收人不存在: {recipient_id}")

            # 批量创建消息
            created_messages = Message.objects.bulk_create(messages)

            # 通过 WebSocket 推送实时通知
            for msg in created_messages:
                NotifyService._push_websocket_notification(msg)

            logger.info(f"站内消息发送成功: {len(created_messages)} 条")
            return True

        except Exception as e:
            logger.error(f"站内消息发送失败: {e}")
            return False

    @staticmethod
    def _push_websocket_notification(message: Message):
        """通过 WebSocket 推送通知"""
        try:
            channel_layer = get_channel_layer()
            if not channel_layer:
                logger.warning("Channel layer 未配置")
                return

            # 发送到用户的通知组
            group_name = f"notifications_user_{message.recipient_id}"

            async_to_sync(channel_layer.group_send)(
                group_name,
                {
                    'type': 'notification_message',
                    'message': '新消息',
                    'data': {
                        'id': str(message.id),
                        'title': message.title,
                        'content': message.content[:100],  # 截取前100字符
                        'msg_type': message.msg_type,
                        'link_type': message.link_type,
                        'link_id': message.link_id,
                        'created_at': message.sys_create_datetime.isoformat() if message.sys_create_datetime else None,
                    }
                }
            )

            logger.debug(f"WebSocket 通知已推送: {message.id}")

        except Exception as e:
            logger.error(f"WebSocket 推送失败: {e}")

    @staticmethod
    def _send_email(recipient_ids: List[str], title: str, content: str) -> bool:
        """发送邮件"""
        from core.user.user_model import User

        try:
            # 获取收件人邮箱
            recipients = User.objects.filter(
                id__in=recipient_ids,
                email__isnull=False,
            ).exclude(email='').values_list('email', flat=True)

            if not recipients:
                logger.warning("没有有效的邮箱地址")
                return False

            # 检查邮件配置
            if not getattr(settings, 'EMAIL_HOST', None):
                logger.warning("邮件服务未配置")
                return False

            send_mail(
                subject=title,
                message=content,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=list(recipients),
                fail_silently=False,
            )

            logger.info(f"邮件发送成功: {len(recipients)} 封")
            return True

        except Exception as e:
            logger.error(f"邮件发送失败: {e}")
            return False

    @staticmethod
    def _send_sms(recipient_ids: List[str], title: str, content: str) -> bool:
        """
        发送短信通知
        
        使用阿里云短信服务发送通知短信
        需要配置:
        - ALIYUN_SMS_ACCESS_KEY_ID
        - ALIYUN_SMS_ACCESS_KEY_SECRET
        - ALIYUN_SMS_SIGN_NAME
        - ALIYUN_SMS_NOTIFY_TEMPLATE_CODE (通知模板)
        """
        from core.user.user_model import User

        try:
            # 检查短信配置
            notify_template = getattr(settings, 'ALIYUN_SMS_NOTIFY_TEMPLATE_CODE', None)
            sign_name = getattr(settings, 'ALIYUN_SMS_SIGN_NAME', None)

            if not notify_template or not sign_name:
                logger.warning("短信通知模板未配置，跳过短信发送")
                return False

            # 获取收件人手机号
            recipients = User.objects.filter(
                id__in=recipient_ids,
                mobile__isnull=False,
                is_active=True,
            ).exclude(mobile='').values_list('mobile', flat=True)

            if not recipients:
                logger.warning("没有有效的手机号码")
                return False

            # 导入短信服务
            try:
                from common.utils.sms_util import SmsService
                from alibabacloud_dysmsapi20170525 import models as dysmsapi_models
                from alibabacloud_tea_util import models as util_models
            except ImportError:
                logger.error("阿里云短信SDK未安装")
                return False

            sms_service = SmsService()

            # 截取内容（短信有字数限制）
            short_content = content[:50] + '...' if len(content) > 50 else content

            success_count = 0
            for phone in recipients:
                try:
                    # 构造模板参数
                    import json
                    template_param = json.dumps({
                        "title": title[:20],  # 标题限制20字
                        "content": short_content,
                    })

                    send_request = dysmsapi_models.SendSmsRequest(
                        phone_numbers=phone,
                        sign_name=sign_name,
                        template_code=notify_template,
                        template_param=template_param
                    )

                    runtime = util_models.RuntimeOptions()
                    response = sms_service.client.send_sms_with_options(send_request, runtime)

                    if response.body.code == 'OK':
                        success_count += 1
                    else:
                        logger.warning(f"短信发送失败 {phone}: {response.body.message}")

                except Exception as e:
                    logger.error(f"短信发送异常 {phone}: {e}")

            logger.info(f"短信发送完成: 成功 {success_count}/{len(list(recipients))} 条")
            return success_count > 0

        except Exception as e:
            logger.error(f"短信发送失败: {e}")
            return False

    @staticmethod
    def _send_wechat(recipient_ids: List[str], title: str, content: str) -> bool:
        """
        发送企业微信应用消息
        
        需要配置:
        - WECOM_CORP_ID: 企业ID
        - WECOM_AGENT_ID: 应用AgentId
        - WECOM_SECRET: 应用Secret
        
        用户需绑定 wechat_unionid 字段
        """
        import requests
        from django.core.cache import cache
        from core.user.user_model import User

        try:
            corp_id = getattr(settings, 'WECOM_CORP_ID', None)
            agent_id = getattr(settings, 'WECOM_AGENT_ID', None)
            secret = getattr(settings, 'WECOM_SECRET', None)

            if not all([corp_id, agent_id, secret]):
                logger.warning("企业微信配置不完整，跳过发送")
                return False

            # 获取用户的企业微信userid
            users = User.objects.filter(
                id__in=recipient_ids,
                wechat_unionid__isnull=False,
                is_active=True,
            ).exclude(wechat_unionid='').values_list('wechat_unionid', flat=True)

            if not users:
                logger.warning("没有绑定企业微信的用户")
                return False

            # 获取 access_token（缓存2小时）
            cache_key = f"wecom_access_token:{corp_id}"
            access_token = cache.get(cache_key)

            if not access_token:
                token_url = f"https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid={corp_id}&corpsecret={secret}"
                resp = requests.get(token_url, timeout=10)
                result = resp.json()

                if result.get('errcode') != 0:
                    logger.error(f"获取企业微信token失败: {result.get('errmsg')}")
                    return False

                access_token = result.get('access_token')
                cache.set(cache_key, access_token, timeout=7000)  # 缓存约2小时

            # 发送应用消息
            send_url = f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={access_token}"

            user_list = '|'.join(users)
            message_data = {
                "touser": user_list,
                "msgtype": "textcard",
                "agentid": agent_id,
                "textcard": {
                    "title": title[:128],
                    "description": content[:512],
                    "url": getattr(settings, 'FRONTEND_URL', 'https://example.com') + '/message/list',
                    "btntxt": "查看详情"
                }
            }

            resp = requests.post(send_url, json=message_data, timeout=10)
            result = resp.json()

            if result.get('errcode') == 0:
                logger.info(f"企业微信发送成功: {len(list(users))} 条")
                return True
            else:
                logger.error(f"企业微信发送失败: {result.get('errmsg')}")
                return False

        except Exception as e:
            logger.error(f"企业微信发送异常: {e}")
            return False

    @staticmethod
    def _send_dingtalk(recipient_ids: List[str], title: str, content: str) -> bool:
        """
        发送钉钉工作通知
        
        需要配置:
        - DINGTALK_APP_KEY: 应用AppKey
        - DINGTALK_APP_SECRET: 应用AppSecret
        - DINGTALK_AGENT_ID: 应用AgentId
        
        用户需绑定 dingtalk_unionid 字段
        """
        import requests
        from django.core.cache import cache
        from core.user.user_model import User

        try:
            app_key = getattr(settings, 'DINGTALK_APP_KEY', None)
            app_secret = getattr(settings, 'DINGTALK_APP_SECRET', None)
            agent_id = getattr(settings, 'DINGTALK_AGENT_ID', None)

            if not all([app_key, app_secret, agent_id]):
                logger.warning("钉钉配置不完整，跳过发送")
                return False

            # 获取用户的钉钉userid
            users = User.objects.filter(
                id__in=recipient_ids,
                dingtalk_unionid__isnull=False,
                is_active=True,
            ).exclude(dingtalk_unionid='').values_list('dingtalk_unionid', flat=True)

            if not users:
                logger.warning("没有绑定钉钉的用户")
                return False

            # 获取 access_token（缓存2小时）
            cache_key = f"dingtalk_access_token:{app_key}"
            access_token = cache.get(cache_key)

            if not access_token:
                token_url = "https://oapi.dingtalk.com/gettoken"
                resp = requests.get(token_url, params={
                    'appkey': app_key,
                    'appsecret': app_secret
                }, timeout=10)
                result = resp.json()

                if result.get('errcode') != 0:
                    logger.error(f"获取钉钉token失败: {result.get('errmsg')}")
                    return False

                access_token = result.get('access_token')
                cache.set(cache_key, access_token, timeout=7000)

            # 发送工作通知
            send_url = f"https://oapi.dingtalk.com/topapi/message/corpconversation/asyncsend_v2?access_token={access_token}"

            user_list = ','.join(users)
            message_data = {
                "agent_id": agent_id,
                "userid_list": user_list,
                "msg": {
                    "msgtype": "oa",
                    "oa": {
                        "head": {
                            "bgcolor": "FFBBBBBB",
                            "text": "通知"
                        },
                        "body": {
                            "title": title[:128],
                            "content": content[:512],
                        }
                    }
                }
            }

            resp = requests.post(send_url, json=message_data, timeout=10)
            result = resp.json()

            if result.get('errcode') == 0:
                logger.info(f"钉钉发送成功: {len(list(users))} 条")
                return True
            else:
                logger.error(f"钉钉发送失败: {result.get('errmsg')}")
                return False

        except Exception as e:
            logger.error(f"钉钉发送异常: {e}")
            return False

    @staticmethod
    def _send_feishu(recipient_ids: List[str], title: str, content: str) -> bool:
        """
        发送飞书消息
        
        需要配置:
        - FEISHU_APP_ID: 应用App ID
        - FEISHU_APP_SECRET: 应用App Secret
        
        用户需绑定 feishu_union_id 字段
        """
        import requests
        import json
        from django.core.cache import cache
        from core.user.user_model import User

        try:
            app_id = getattr(settings, 'FEISHU_APP_ID', None)
            app_secret = getattr(settings, 'FEISHU_APP_SECRET', None)

            if not all([app_id, app_secret]):
                logger.warning("飞书配置不完整，跳过发送")
                return False

            # 获取用户的飞书open_id
            users = User.objects.filter(
                id__in=recipient_ids,
                feishu_union_id__isnull=False,
                is_active=True,
            ).exclude(feishu_union_id='').values('id', 'feishu_union_id')

            if not users:
                logger.warning("没有绑定飞书的用户")
                return False

            # 获取 tenant_access_token（缓存2小时）
            cache_key = f"feishu_tenant_token:{app_id}"
            access_token = cache.get(cache_key)

            if not access_token:
                token_url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
                resp = requests.post(token_url, json={
                    'app_id': app_id,
                    'app_secret': app_secret
                }, timeout=10)
                result = resp.json()

                if result.get('code') != 0:
                    logger.error(f"获取飞书token失败: {result.get('msg')}")
                    return False

                access_token = result.get('tenant_access_token')
                cache.set(cache_key, access_token, timeout=7000)

            # 发送消息（飞书需要逐个发送或使用批量接口）
            send_url = "https://open.feishu.cn/open-apis/im/v1/messages"
            headers = {
                'Authorization': f'Bearer {access_token}',
                'Content-Type': 'application/json'
            }

            success_count = 0
            for user in users:
                try:
                    # 构造消息卡片
                    card_content = {
                        "config": {"wide_screen_mode": True},
                        "header": {
                            "title": {"tag": "plain_text", "content": title[:128]},
                            "template": "blue"
                        },
                        "elements": [
                            {
                                "tag": "div",
                                "text": {"tag": "plain_text", "content": content[:512]}
                            },
                            {
                                "tag": "action",
                                "actions": [
                                    {
                                        "tag": "button",
                                        "text": {"tag": "plain_text", "content": "查看详情"},
                                        "type": "primary",
                                        "url": getattr(settings, 'FRONTEND_URL',
                                                       'https://example.com') + '/message/list'
                                    }
                                ]
                            }
                        ]
                    }

                    message_data = {
                        "receive_id": user['feishu_union_id'],
                        "msg_type": "interactive",
                        "content": json.dumps(card_content)
                    }

                    resp = requests.post(
                        f"{send_url}?receive_id_type=union_id",
                        headers=headers,
                        json=message_data,
                        timeout=10
                    )
                    result = resp.json()

                    if result.get('code') == 0:
                        success_count += 1
                    else:
                        logger.warning(f"飞书发送失败 {user['feishu_union_id']}: {result.get('msg')}")

                except Exception as e:
                    logger.error(f"飞书发送异常 {user['feishu_union_id']}: {e}")

            logger.info(f"飞书发送完成: 成功 {success_count}/{len(list(users))} 条")
            return success_count > 0

        except Exception as e:
            logger.error(f"飞书发送异常: {e}")
            return False


class MessageService:
    """消息管理服务"""

    @staticmethod
    def get_list(
            user_id: str,
            msg_type: str = None,
            status: str = None,
    ):
        """获取用户消息列表"""
        queryset = Message.objects.filter(
            recipient_id=user_id,
            is_deleted=False,
        )

        if msg_type:
            queryset = queryset.filter(msg_type=msg_type)
        if status:
            queryset = queryset.filter(status=status)

        return queryset

    @staticmethod
    def get_unread_count(user_id: str) -> int:
        """获取未读消息数量"""
        return Message.objects.filter(
            recipient_id=user_id,
            status='unread',
            is_deleted=False,
        ).count()

    @staticmethod
    def get_unread_count_by_type(user_id: str) -> Dict[str, int]:
        """按类型获取未读消息数量"""
        from django.db.models import Count

        result = Message.objects.filter(
            recipient_id=user_id,
            status='unread',
            is_deleted=False,
        ).values('msg_type').annotate(count=Count('id'))

        return {item['msg_type']: item['count'] for item in result}

    @staticmethod
    def get_by_id(message_id: str, user_id: str) -> Optional[Message]:
        """获取消息详情"""
        try:
            return Message.objects.get(
                id=message_id,
                recipient_id=user_id,
                is_deleted=False,
            )
        except Message.DoesNotExist:
            return None

    @staticmethod
    def mark_as_read(message_id: str, user_id: str) -> bool:
        """标记消息为已读"""
        try:
            message = Message.objects.get(
                id=message_id,
                recipient_id=user_id,
                is_deleted=False,
            )
            message.mark_as_read()
            return True
        except Message.DoesNotExist:
            return False

    @staticmethod
    def mark_all_as_read(user_id: str, msg_type: str = None) -> int:
        """标记所有消息为已读"""
        queryset = Message.objects.filter(
            recipient_id=user_id,
            status='unread',
            is_deleted=False,
        )

        if msg_type:
            queryset = queryset.filter(msg_type=msg_type)

        count = queryset.update(
            status='read',
            read_at=datetime.now(),
        )

        return count

    @staticmethod
    def delete(message_id: str, user_id: str) -> bool:
        """删除消息（软删除）"""
        try:
            message = Message.objects.get(
                id=message_id,
                recipient_id=user_id,
                is_deleted=False,
            )
            message.soft_delete()
            return True
        except Message.DoesNotExist:
            return False

    @staticmethod
    def delete_all_read(user_id: str) -> int:
        """删除所有已读消息"""
        count = Message.objects.filter(
            recipient_id=user_id,
            status='read',
            is_deleted=False,
        ).update(is_deleted=True)

        return count
