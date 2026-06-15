import type { DouyinConversationItem, DouyinMessageItem } from './session';

import { requestClient } from '#/api/request';

/**
 * 获取账号的会话列表（消息回复模块专用）
 */
export async function getAccountConversations(accountId: string) {
  return requestClient.get<DouyinConversationItem[]>(
    `/api/core/douyin/account/${accountId}/conversations`,
  );
}

/**
 * 获取会话消息列表（消息回复模块专用）
 */
export async function getAccountMessages(
  accountId: string,
  conversationId: string,
) {
  return requestClient.get<DouyinMessageItem[]>(
    `/api/core/douyin/account/${accountId}/conversation/${conversationId}/messages`,
  );
}

/**
 * 发送手动回复（消息回复模块专用）
 */
export async function sendAccountManualReply(
  accountId: string,
  conversationId: string,
  text: string,
) {
  return requestClient.post<{ message: string; success: boolean }>(
    `/api/core/douyin/account/${accountId}/manual-reply`,
    { conversation_id: conversationId, text },
  );
}

/**
 * 刷新会话对方的用户资料（头像/昵称）
 */
export async function refreshConversationUserApi(
  accountId: string,
  conversationId: string,
) {
  return requestClient.post<{ message: string; success: boolean }>(
    `/api/core/douyin/account/${accountId}/conversation/${conversationId}/refresh-user`,
  );
}

