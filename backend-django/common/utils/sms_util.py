#!/usr/bin/env python
# -*- coding: utf-8 -*-
# time: 2024/12/28
# file: sms_util.py
# author: 臧成龙
# QQ: 939589097

import json
import random
import string
from datetime import datetime, timedelta

from django.core.cache import cache
from ninja.errors import HttpError

try:
    from alibabacloud_dysmsapi20170525.client import Client as DysmsapiClient
    from alibabacloud_tea_openapi import models as open_api_models
    from alibabacloud_dysmsapi20170525 import models as dysmsapi_models
    from alibabacloud_tea_util import models as util_models
except ImportError:
    DysmsapiClient = None
    open_api_models = None
    dysmsapi_models = None
    util_models = None


class SmsService:
    """阿里云短信服务类"""

    def __init__(self, access_key_id: str = None, access_key_secret: str = None, endpoint: str = None):
        """
        初始化短信服务
        
        Args:
            access_key_id: 阿里云AccessKey ID
            access_key_secret: 阿里云AccessKey Secret
            endpoint: 阿里云短信服务端点，默认为 dysmsapi.aliyuncs.com
        """
        if not DysmsapiClient:
            raise HttpError(500, "阿里云短信SDK未安装，请安装alibabacloud_dysmsapi20170525包")

        self.access_key_id = access_key_id or self._get_config_value('ALIYUN_SMS_ACCESS_KEY_ID')
        self.access_key_secret = access_key_secret or self._get_config_value('ALIYUN_SMS_ACCESS_KEY_SECRET')
        self.endpoint = endpoint or 'dysmsapi.aliyuncs.com'

        if not self.access_key_id or not self.access_key_secret:
            raise HttpError(500, "阿里云短信服务配置不完整，请检查AccessKey配置")

        # 创建配置
        config = open_api_models.Config(
            access_key_id=self.access_key_id,
            access_key_secret=self.access_key_secret,
            endpoint=self.endpoint
        )

        # 创建客户端
        self.client = DysmsapiClient(config)

    def _get_config_value(self, key: str, default: str = None) -> str:
        """从Django settings中获取配置值"""
        from django.conf import settings
        return getattr(settings, key, default)

    def generate_verification_code(self, length: int = 6) -> str:
        """
        生成数字验证码
        
        Args:
            length: 验证码长度，默认6位
            
        Returns:
            生成的验证码字符串
        """
        return ''.join(random.choices(string.digits, k=length))

    def send_verification_code(
            self,
            phone_number: str,
            template_code: str = None,
            sign_name: str = None,
            code_length: int = 6,
            expire_minutes: int = 5
    ) -> dict:
        """
        发送短信验证码
        
        Args:
            phone_number: 手机号码
            template_code: 短信模板代码
            sign_name: 短信签名
            code_length: 验证码长度，默认6位
            expire_minutes: 验证码有效期（分钟），默认5分钟
            
        Returns:
            发送结果字典
        """
        # 验证手机号格式
        if not self._validate_phone_number(phone_number):
            raise HttpError(422, "手机号码格式不正确")

        # 检查发送频率限制
        self._check_send_frequency(phone_number)

        # 生成验证码
        verification_code = self.generate_verification_code(code_length)

        # 获取默认模板和签名
        template_code = template_code or self._get_config_value('ALIYUN_SMS_TEMPLATE_CODE')
        sign_name = sign_name or self._get_config_value('ALIYUN_SMS_SIGN_NAME')

        if not template_code or not sign_name:
            raise HttpError(500, "短信模板或签名未配置")

        # 构造请求参数
        template_param = json.dumps({"code": verification_code})

        send_sms_request = dysmsapi_models.SendSmsRequest(
            phone_numbers=phone_number,
            sign_name=sign_name,
            template_code=template_code,
            template_param=template_param
        )

        runtime = util_models.RuntimeOptions()

        try:
            # 发送短信
            response = self.client.send_sms_with_options(send_sms_request, runtime)

            if response.body.code == 'OK':
                # 发送成功，保存验证码到缓存
                self._save_verification_code(phone_number, verification_code, expire_minutes)

                # 记录发送频率
                self._record_send_frequency(phone_number)

                return {
                    "success": True,
                    "message": "验证码发送成功",
                    "phone_number": phone_number,
                    "expire_minutes": expire_minutes
                }
            else:
                raise HttpError(500, f"短信发送失败: {response.body.message}")

        except Exception as e:
            if isinstance(e, HttpError):
                raise e
            raise HttpError(500, f"短信发送异常: {str(e)}")

    def verify_code(self, phone_number: str, code: str) -> bool:
        """
        验证短信验证码
        
        Args:
            phone_number: 手机号码
            code: 验证码
            
        Returns:
            验证是否成功
        """
        if not self._validate_phone_number(phone_number):
            return False

        # 从缓存中获取验证码
        cache_key = f"sms_code:{phone_number}"
        cached_code = cache.get(cache_key)

        if not cached_code:
            return False

        # 验证码匹配
        if cached_code == code:
            # 验证成功后删除缓存
            cache.delete(cache_key)
            return True

        return False

    def _validate_phone_number(self, phone_number: str) -> bool:
        """
        验证手机号码格式
        
        Args:
            phone_number: 手机号码
            
        Returns:
            是否为有效手机号
        """
        import re
        # 中国大陆手机号正则表达式
        pattern = r'^1[3-9]\d{9}$'
        return bool(re.match(pattern, phone_number))

    def _check_send_frequency(self, phone_number: str):
        """
        检查发送频率限制
        
        Args:
            phone_number: 手机号码
        """
        frequency_key = f"sms_frequency:{phone_number}"
        send_times = cache.get(frequency_key, [])

        now = datetime.now()

        # 清理1小时前的记录
        send_times = [t for t in send_times if now - datetime.fromisoformat(t) < timedelta(hours=1)]

        # 检查1分钟内是否已发送
        one_minute_ago = now - timedelta(minutes=1)
        recent_sends = [t for t in send_times if datetime.fromisoformat(t) > one_minute_ago]
        if recent_sends:
            raise HttpError(422, "发送太频繁，请1分钟后再试")

        # 检查1小时内发送次数
        if len(send_times) >= 5:  # 1小时内最多5次
            raise HttpError(422, "发送次数超限，请1小时后再试")

    def _record_send_frequency(self, phone_number: str):
        """
        记录发送频率
        
        Args:
            phone_number: 手机号码
        """
        frequency_key = f"sms_frequency:{phone_number}"
        send_times = cache.get(frequency_key, [])

        now = datetime.now()
        send_times.append(now.isoformat())

        # 清理1小时前的记录
        send_times = [t for t in send_times if now - datetime.fromisoformat(t) < timedelta(hours=1)]

        # 保存到缓存，过期时间1小时
        cache.set(frequency_key, send_times, timeout=3600)

    def _save_verification_code(self, phone_number: str, code: str, expire_minutes: int):
        """
        保存验证码到缓存
        
        Args:
            phone_number: 手机号码
            code: 验证码
            expire_minutes: 过期时间（分钟）
        """
        cache_key = f"sms_code:{phone_number}"
        cache.set(cache_key, code, timeout=expire_minutes * 60)


# 默认短信服务实例
def get_sms_service() -> SmsService:
    """获取默认短信服务实例"""
    return SmsService()


def send_verification_code(phone_number: str, **kwargs) -> dict:
    """
    发送短信验证码的便捷函数
    
    Args:
        phone_number: 手机号码
        **kwargs: 其他参数
        
    Returns:
        发送结果
    """
    service = get_sms_service()
    return service.send_verification_code(phone_number, **kwargs)


def verify_sms_code(phone_number: str, code: str) -> bool:
    """
    验证短信验证码的便捷函数
    
    Args:
        phone_number: 手机号码
        code: 验证码
        
    Returns:
        验证是否成功
    """
    service = get_sms_service()
    return service.verify_code(phone_number, code)
