import type { DouyinConversationItem, DouyinMessageItem } from './session';

import { requestClient } from '#/api/request';

export interface DouyinConversationListResponse {
  items: DouyinConversationItem[];
  total: number;
  page: number;
  page_size: number;
  has_more: boolean;
}

/**
 * 获取账号的会话列表（消息回复模块专用，支持分页与搜索）
 */
export async function getAccountConversations(
  accountId: string,
  params?: { page?: number; page_size?: number; keyword?: string },
) {
  return requestClient.get<DouyinConversationListResponse>(
    `/api/core/douyin/account/${accountId}/conversations`,
    {
      params: {
        page: params?.page ?? 1,
        page_size: params?.page_size ?? 50,
        keyword: params?.keyword || undefined,
      },
    },
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
  return requestClient.post<{
    command_id?: string | null;
    message: string;
    success: boolean;
  }>(`/api/core/douyin/account/${accountId}/manual-reply`, {
    conversation_id: conversationId,
    text,
  });
}

/**
 * 查询 Worker 手动发送命令执行结果
 */
export async function getWorkerCommandStatus(commandId: string) {
  return requestClient.get<{
    command_id: string;
    consumed: boolean;
    error?: string | null;
    message_id?: string | null;
    status: 'failed' | 'pending' | 'success' | 'unknown';
  }>(`/api/core/douyin/worker-command/${commandId}`);
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
