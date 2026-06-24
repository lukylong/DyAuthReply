import { requestClient } from '#/api/request';

export interface DouyinSession {
  id: string;
  account_id: string;
  account_nickname?: null | string;
  worker_id: string;
  context_id: string;
  status: 'error' | 'idle' | 'paused' | 'running' | 'stopped';
  started_at?: null | string;
  last_heartbeat?: null | string;
  last_message_at?: null | string;
  messages_today: number;
  replies_today: number;
  errors_today: number;
  cpu_percent: number;
  memory_mb: number;
  proxy_url?: null | string;
  error_message?: null | string;
  is_alive: boolean;
}

export interface DouyinConversationItem {
  id: string;
  peer_sec_uid: string;
  peer_nickname?: null | string;
  peer_avatar?: null | string;
  last_message_at?: null | string;
  last_message_preview?: null | string;
  unread_count: number;
}

export interface DouyinMessageMedia {
  kind?: string;
  url?: string;
  cover_url?: string;
  duration_s?: null | number | string;
  duration_ms?: null | number | string;
  width?: null | number;
  height?: null | number;
  item_id?: string;
  ai_text?: null | string;
  vid?: string;
  inline_pic?: string;
  encrypted?: boolean;
  [key: string]: unknown;
}

export interface DouyinMessageItem {
  id: string;
  direction: 'in' | 'out';
  content_type: string;
  content: string;
  received_at?: null | string;
  processed: boolean;
  sender_name?: null | string;
  sender_avatar?: null | string;
  media?: DouyinMessageMedia | null;
}


export interface PaginatedResponse<T> {
  total: number;
  items: T[];
}

export async function getSessionList(params: Record<string, unknown> = {}) {
  return requestClient.get<PaginatedResponse<DouyinSession>>('/api/core/douyin/session', { params });
}

export async function getLiveSessions() {
  return requestClient.get<DouyinSession[]>('/api/core/douyin/session/live');
}

export async function getSession(id: string) {
  return requestClient.get<DouyinSession>(`/api/core/douyin/session/${id}`);
}

export async function controlSession(
  id: string,
  action: 'pause' | 'restart' | 'resume' | 'stop',
) {
  return requestClient.post<{ message: string; success: boolean }>(
    `/api/core/douyin/session/${id}/control`,
    { action },
  );
}

export async function batchStopSession(ids: string[]) {
  return requestClient.post('/api/core/douyin/session/batch/stop', { ids });
}

export interface DouyinConversationListResponse {
  items: DouyinConversationItem[];
  total: number;
  page: number;
  page_size: number;
  has_more: boolean;
}

export async function getSessionConversations(
  sessionId: string,
  params?: { page?: number; page_size?: number; keyword?: string },
) {
  const res = await requestClient.get<DouyinConversationListResponse>(
    `/api/core/douyin/session/${sessionId}/conversations`,
    {
      params: {
        page: params?.page ?? 1,
        page_size: params?.page_size ?? 100,
        keyword: params?.keyword || undefined,
      },
    },
  );
  return res.items;
}

export async function getSessionMessages(sessionId: string, conversationId: string) {
  return requestClient.get<DouyinMessageItem[]>(
    `/api/core/douyin/session/${sessionId}/conversation/${conversationId}/messages`,
  );
}

/**
 * 拉取并解密私信加密图片，返回可用于 <img src> 的 object URL。
 * web 端走 JWT 鉴权，<img> 标签无法携带 token，故用 axios(blob) 拉取后转 object URL。
 * 调用方需在不再使用时 URL.revokeObjectURL 释放。
 */
export async function getMessageImageObjectUrl(
  accountId: string,
  conversationId: string,
  messageId: string,
): Promise<string> {
  const blob = await requestClient.get<Blob>(
    `/api/core/douyin/account/${accountId}/conversation/${conversationId}/message/${messageId}/image`,
    { responseType: 'blob' },
  );
  return URL.createObjectURL(blob);
}

export async function sendManualReply(
  sessionId: string,
  conversationId: string,
  text: string,
) {
  return requestClient.post<{ message: string; success: boolean }>(
    `/api/core/douyin/session/${sessionId}/manual-reply`,
    { conversation_id: conversationId, text },
  );
}

export async function triggerAutoReplyTest(
  sessionId: string,
  conversationId: string,
  text: string,
) {
  return requestClient.post<{ message: string; success: boolean }>(
    `/api/core/douyin/session/${sessionId}/auto-reply-test`,
    { conversation_id: conversationId, text },
  );
}
