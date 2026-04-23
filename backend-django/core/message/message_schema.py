#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: message_schema.py
@Desc: 消息 Schema 定义
"""
"""
消息 Schema 定义
"""
from datetime import datetime
from typing import Optional

from ninja import Schema, Field


class MessageOut(Schema):
    """消息输出"""
    id: str
    title: str
    content: str
    msg_type: str
    status: str
    link_type: str
    link_id: str
    sender_name: str = ""
    read_at: Optional[datetime] = None
    created_at: datetime = Field(None, alias="sys_create_datetime")


class MessageListOut(Schema):
    """消息列表输出"""
    id: str
    title: str
    content: str  # 截取前100字符
    msg_type: str
    status: str
    link_type: str
    link_id: str
    created_at: datetime = Field(None, alias="sys_create_datetime")


class UnreadCountOut(Schema):
    """未读数量输出"""
    total: int
    by_type: dict


class MarkReadInput(Schema):
    """标记已读输入"""
    msg_type: Optional[str] = Field(None, description="消息类型，不传则标记全部")
