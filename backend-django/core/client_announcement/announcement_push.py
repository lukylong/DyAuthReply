# -*- coding: utf-8 -*-
"""客户端公告 WebSocket 推送"""
import logging
from datetime import datetime

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

logger = logging.getLogger(__name__)


def push_announcement_to_clients(announcement):
    """
    发布公告时通过 WebSocket 推送到所有在线客户端

    Args:
        announcement: ClientAnnouncement 实例
    """
    try:
        channel_layer = get_channel_layer()
        if not channel_layer:
            logger.warning("Channel layer not configured, skipping WebSocket push")
            return

        message = {
            "type": "send_message",  # Channels 消费者的处理方法名
            "message": {
                "type": "client_announcement",
                "data": {
                    "id": str(announcement.id),
                    "title": announcement.title,
                    "content": announcement.content,
                    "level": announcement.level,
                    "publish_time": announcement.publish_time.isoformat() if announcement.publish_time else None,
                    "timestamp": datetime.now().isoformat(),
                }
            }
        }

        # 推送到通知频道
        async_to_sync(channel_layer.group_send)("notifications", message)
        logger.info(f"Pushed announcement {announcement.id} to notifications channel")

    except Exception as e:
        logger.error(f"Failed to push announcement {announcement.id}: {e}", exc_info=True)
