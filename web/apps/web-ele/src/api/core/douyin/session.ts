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

export interface DouyinMessageItem {
  id: string;
  direction: 'in' | 'out';
  content_type: string;
  content: string;
  received_at?: null | string;
  processed: boolean;
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

export async function getSessionConversations(sessionId: string) {
  return requestClient.get<DouyinConversationItem[]>(
    `/api/core/douyin/session/${sessionId}/conversations`,
  );
}

export async function getSessionMessages(sessionId: string, conversationId: string) {
  return requestClient.get<DouyinMessageItem[]>(
    `/api/core/douyin/session/${sessionId}/conversation/${conversationId}/messages`,
  );
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
