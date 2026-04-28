"""抖音 IM 协议 wire format 编解码（Phase 3）。

为什么不直接用 google.protobuf？
  - 我们没有完整的 .proto 文件，只有 sniffer 抓到的 wire bytes
  - 引 protobuf runtime 后，schema 漂移会让升级成本变高
  - wire format 本身只有 4 种 tag type，30 行就能写完编解码

仅覆盖 IM 三个 verb 需要的字段，不做通用 protobuf 库。
"""
from core.douyin.runtime.transport.wire.codec import (
    WIRE_LEN,
    WIRE_VARINT,
    decode_message,
    encode_field,
    encode_varint,
    iter_fields,
)
from core.douyin.runtime.transport.wire.im_protocol import (
    GET_BY_CONVERSATION_CMD_ID,
    GET_BY_USER_CMD_ID,
    GET_BY_USER_INIT_CMD_ID,
    GET_CONVERSATION_LIST_CMD_ID,
    IM_BUILD_ID,
    IM_SDK_VERSION,
    SEND_MESSAGE_CMD_ID,
    SendMessageResult,
    decode_send_message_response,
    encode_send_message_request,
    encode_send_message_request_from_template,
)
from core.douyin.runtime.transport.wire.im_protocol_recv import (
    ConversationListItem,
    GetByConversationResult,
    GetByUserResult,
    IMMessage,
    IMParticipant,
    ListConversationsResult,
    decode_get_by_conversation_response,
    decode_get_by_user_response,
    decode_im_message,
    decode_list_conversations_response,
    encode_get_by_conversation_request,
    encode_get_by_user_request,
    encode_list_conversations_request,
)

__all__ = [
    "WIRE_LEN",
    "WIRE_VARINT",
    "decode_message",
    "encode_field",
    "encode_varint",
    "iter_fields",
    "IM_BUILD_ID",
    "IM_SDK_VERSION",
    "SEND_MESSAGE_CMD_ID",
    "GET_BY_CONVERSATION_CMD_ID",
    "GET_BY_USER_CMD_ID",
    "GET_BY_USER_INIT_CMD_ID",
    "GET_CONVERSATION_LIST_CMD_ID",
    "SendMessageResult",
    "decode_send_message_response",
    "encode_send_message_request",
    "encode_send_message_request_from_template",
    "ConversationListItem",
    "GetByConversationResult",
    "GetByUserResult",
    "IMMessage",
    "IMParticipant",
    "ListConversationsResult",
    "decode_get_by_conversation_response",
    "decode_get_by_user_response",
    "decode_im_message",
    "decode_list_conversations_response",
    "encode_get_by_conversation_request",
    "encode_get_by_user_request",
    "encode_list_conversations_request",
]
