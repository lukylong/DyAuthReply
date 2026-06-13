"""
统一平台 Adapter 接口

所有社交平台（抖音、快手等）的 Adapter 都实现此接口，
Worker 层不关心底层用什么协议实现。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Message:
    """统一消息结构：各平台 Adapter 统一输出此格式"""

    msg_id: str  # 消息唯一ID
    from_user_id: str  # 发送者用户ID
    from_user_name: str  # 发送者昵称
    text: str  # 消息文本内容
    timestamp: int  # Unix 时间戳 (秒)
    conv_id: str = ""  # 会话ID (私信场景)
    extra: dict[str, Any] = field(default_factory=dict)  # 平台特有字段


@dataclass
class AccountInfo:
    """统一账号信息"""

    id: int  # 内部 DB ID
    platform: str  # 平台标识: "douyin" | "kuaishou"
    platform_user_id: str  # 平台侧用户ID
    account_name: str  # 账号昵称
    credential: dict[str, Any]  # 认证凭证: {"cookies": {...}, "token": "..."}
    last_cursor: str = ""  # 消息拉取游标
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Comment:
    """统一评论结构 (附加功能)"""

    comment_id: str
    video_id: str
    from_user_id: str
    from_user_name: str
    text: str
    timestamp: int
    reply_count: int = 0


class BasePlatformAdapter(ABC):
    """
    平台 Adapter 抽象基类

    所有平台必须实现以下方法:
      - login()       : 验证凭证是否有效
      - fetch_messages(): 拉取新消息
      - send_reply()  : 发送回复
      - is_alive()    : 心跳检测
      - fetch_comments() : (可选) 拉取评论
      - reply_comment()  : (可选) 回复评论
    """

    platform: str = "unknown"

    # ===== 必须实现 =====

    @abstractmethod
    async def login(self, account: AccountInfo) -> bool:
        """验证凭证有效性，成功返回 True"""
        ...

    @abstractmethod
    async def fetch_messages(
        self, account: AccountInfo, cursor: Optional[str] = None
    ) -> list[Message]:
        """拉取新消息列表，cursor 用于分页/增量拉取"""
        ...

    @abstractmethod
    async def send_reply(self, account: AccountInfo, msg: Message, text: str) -> bool:
        """发送私信回复，成功返回 True"""
        ...

    @abstractmethod
    async def is_alive(self, account: AccountInfo) -> bool:
        """心跳检测：判断当前登录态是否有效"""
        ...

    # ===== 可选实现 (附加功能: 评论回复) =====

    async def fetch_comments(
        self, account: AccountInfo, cursor: Optional[str] = None
    ) -> list[Comment]:
        """拉取新评论列表 (附加功能)"""
        raise NotImplementedError(f"fetch_comments not implemented for {self.platform}")

    async def reply_comment(
        self, account: AccountInfo, comment: Comment, text: str
    ) -> bool:
        """回复评论 (附加功能)"""
        raise NotImplementedError(f"reply_comment not implemented for {self.platform}")

    # ===== 提醒通知 =====

    async def on_login_expired(self, account: AccountInfo) -> None:
        """登录态过期回调：通知用户重新扫码 (由具体实现覆盖)"""
        pass
