#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: device_util.py
@Desc: 设备信息解析工具 - 从 User-Agent 字符串中提取浏览器、操作系统、设备类型等信息
"""
"""
设备信息解析工具
从 User-Agent 字符串中提取浏览器、操作系统、设备类型等信息
"""
import logging
from typing import Tuple, Optional

logger = logging.getLogger(__name__)

# 尝试导入 user-agents 库用于解析设备信息
try:
    from user_agents import parse

    HAS_USER_AGENTS = True
except ImportError:
    HAS_USER_AGENTS = False


def extract_device_info(user_agent: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    从 User-Agent 字符串提取浏览器、操作系统、设备类型
    
    Args:
        user_agent: User-Agent 字符串
    
    Returns:
        Tuple: (browser_type, os_type, device_type)
        - browser_type: 浏览器类型 (Chrome, Firefox, Safari, Edge, Opera, IE, Unknown)
        - os_type: 操作系统 (Windows, macOS, iOS, Android, Linux, Unknown)
        - device_type: 设备类型 (desktop, mobile, tablet, other)
    
    示例:
        >>> user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/91.0"
        >>> browser, os, device = extract_device_info(user_agent)
        >>> print(browser, os, device)
        Chrome Windows desktop
    """
    if not user_agent:
        return None, None, None

    # 如果安装了 user-agents 库，优先使用它来解析
    if HAS_USER_AGENTS:
        try:
            return _extract_with_user_agents_lib(user_agent)
        except Exception as e:
            logger.warning(f"使用 user-agents 库解析失败，将使用简单解析: {str(e)}")

    # 回退到简单的字符串匹配解析
    return _extract_user_agent_simple(user_agent)


def _extract_with_user_agents_lib(user_agent: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    使用 user-agents 库解析 User-Agent
    
    Args:
        user_agent: User-Agent 字符串
    
    Returns:
        Tuple: (browser_type, os_type, device_type)
    """
    ua = parse(user_agent)

    # 提取浏览器信息
    browser_type = ua.browser.family if ua.browser else 'Unknown'

    # 提取操作系统信息
    os_type = ua.os.family if ua.os else 'Unknown'

    # 判断设备类型
    if ua.is_mobile:
        device_type = 'mobile'
    elif ua.is_tablet:
        device_type = 'tablet'
    elif ua.is_pc:
        device_type = 'desktop'
    else:
        device_type = 'other'

    return browser_type, os_type, device_type


def _extract_user_agent_simple(user_agent: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    使用简单的字符串匹配解析 User-Agent
    
    当 user-agents 库不可用时使用此方法。
    
    Args:
        user_agent: User-Agent 字符串
    
    Returns:
        Tuple: (browser_type, os_type, device_type)
    """
    if not user_agent:
        return None, None, None

    ua_lower = user_agent.lower()

    # 判断浏览器类型
    browser_type = _detect_browser(ua_lower)

    # 判断操作系统
    os_type = _detect_os(ua_lower)

    # 判断设备类型
    device_type = _detect_device_type(ua_lower)

    return browser_type, os_type, device_type


def _detect_browser(ua_lower: str) -> str:
    """
    检测浏览器类型
    
    Args:
        ua_lower: 小写的 User-Agent 字符串
    
    Returns:
        浏览器类型字符串
    """
    # 检测顺序很重要，特别是 Chrome 和 Edge（Edge 也包含 Chrome）

    if 'edg' in ua_lower:
        return 'Edge'
    elif 'chrome' in ua_lower and 'chromium' not in ua_lower:
        return 'Chrome'
    elif 'firefox' in ua_lower:
        return 'Firefox'
    elif 'safari' in ua_lower and 'chrome' not in ua_lower:
        return 'Safari'
    elif 'opera' in ua_lower or 'opr/' in ua_lower:
        return 'Opera'
    elif 'trident' in ua_lower or 'msie' in ua_lower:
        return 'IE'
    elif 'chromium' in ua_lower:
        return 'Chromium'
    else:
        return 'Unknown'


def _detect_os(ua_lower: str) -> str:
    """
    检测操作系统
    
    Args:
        ua_lower: 小写的 User-Agent 字符串
    
    Returns:
        操作系统类型字符串
    """
    # 检测顺序很重要（例如 iPad 应该在 Mac 之前）

    if 'windows' in ua_lower or 'win' in ua_lower:
        return 'Windows'
    elif 'iphone' in ua_lower:
        return 'iOS'
    elif 'ipad' in ua_lower:
        return 'iOS'
    elif 'mac' in ua_lower or 'osx' in ua_lower:
        return 'macOS'
    elif 'android' in ua_lower:
        return 'Android'
    elif 'linux' in ua_lower:
        return 'Linux'
    elif 'x11' in ua_lower:
        return 'Unix'
    else:
        return 'Unknown'


def _detect_device_type(ua_lower: str) -> str:
    """
    检测设备类型
    
    Args:
        ua_lower: 小写的 User-Agent 字符串
    
    Returns:
        设备类型字符串 (desktop, mobile, tablet, other)
    """
    # 移动设备检测
    mobile_keywords = [
        'mobile',
        'android',
        'iphone',
        'ipod',
        'blackberry',
        'windows phone',
        'webos',
        'palm',
        'symbian',
    ]

    for keyword in mobile_keywords:
        if keyword in ua_lower:
            return 'mobile'

    # 平板设备检测
    tablet_keywords = [
        'ipad',
        'tablet',
        'kindle',
        'nexus 7',
        'nexus 10',
        'xoom',
    ]

    for keyword in tablet_keywords:
        if keyword in ua_lower:
            return 'tablet'

    # 如果是移动系统但不是以上任何情况
    if any(os_name in ua_lower for os_name in ['android', 'ios', 'windows phone']):
        return 'mobile'

    # 默认为桌面
    return 'desktop'


def get_browser_version(user_agent: str) -> Optional[str]:
    """
    从 User-Agent 中提取浏览器版本号
    
    Args:
        user_agent: User-Agent 字符串
    
    Returns:
        浏览器版本号，如果无法提取则返回 None
    """
    if not user_agent or not HAS_USER_AGENTS:
        return None

    try:
        ua = parse(user_agent)
        if ua.browser and ua.browser.version_string:
            return ua.browser.version_string
    except Exception as e:
        logger.warning(f"提取浏览器版本失败: {str(e)}")

    return None


def get_os_version(user_agent: str) -> Optional[str]:
    """
    从 User-Agent 中提取操作系统版本号
    
    Args:
        user_agent: User-Agent 字符串
    
    Returns:
        操作系统版本号，如果无法提取则返回 None
    """
    if not user_agent or not HAS_USER_AGENTS:
        return None

    try:
        ua = parse(user_agent)
        if ua.os and ua.os.version_string:
            return ua.os.version_string
    except Exception as e:
        logger.warning(f"提取操作系统版本失败: {str(e)}")

    return None
