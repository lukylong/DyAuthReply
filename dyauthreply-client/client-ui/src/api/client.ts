const getApiPrefix = () => {
  if (
    typeof window !== 'undefined' &&
    ((window as any).__TAURI_INTERNALS__ ||
      window.location.protocol.startsWith('tauri') ||
      window.location.host.includes('tauri') ||
      window.location.protocol === 'file:')
  ) {
    return 'http://127.0.0.1:8765/api/client/v1';
  }
  return '/api/client/v1';
};

const API_PREFIX = getApiPrefix();
export const ADMIN_TOKEN_KEY = 'dyauthreply_admin_token';

async function parseError(res: Response): Promise<string> {
  const text = await res.text();
  try {
    const json = JSON.parse(text) as { detail?: unknown; message?: string };
    const detail = formatApiDetail(json.detail);
    return detail || json.message || text || res.statusText;
  } catch {
    return text || res.statusText;
  }
}

function formatApiDetail(detail: unknown): string {
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (typeof item === 'string') return item;
        if (item && typeof item === 'object') {
          const row = item as { msg?: string; loc?: Array<string | number> };
          const loc = Array.isArray(row.loc) ? row.loc.filter((x) => x !== 'body').join('.') : '';
          return row.msg ? (loc ? `${loc}: ${row.msg}` : row.msg) : JSON.stringify(item);
        }
        return String(item);
      })
      .join('；');
  }
  if (detail && typeof detail === 'object' && 'message' in detail) {
    return String((detail as { message: string }).message);
  }
  if (detail != null && typeof detail === 'object') {
    return JSON.stringify(detail);
  }
  return '';
}

export function getAdminToken(): string {
  if (typeof window === 'undefined') return '';
  return sessionStorage.getItem(ADMIN_TOKEN_KEY) || '';
}

export function setAdminToken(token: string) {
  sessionStorage.setItem(ADMIN_TOKEN_KEY, token);
}

export function clearAdminToken() {
  sessionStorage.removeItem(ADMIN_TOKEN_KEY);
}

export function isAdminLoggedIn(): boolean {
  return Boolean(getAdminToken());
}

async function request<T>(path: string, init?: RequestInit, admin = false): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(init?.headers as Record<string, string> | undefined),
  };
  if (admin) {
    const token = getAdminToken();
    if (token) headers['X-Admin-Token'] = token;
  }
  const res = await fetch(`${API_PREFIX}${path}`, {
    ...init,
    headers,
  });
  if (!res.ok) {
    throw new Error(await parseError(res));
  }
  const json = await res.json();
  if (json && typeof json === 'object' && 'code' in json && json.code === 2000) {
    return json.data as T;
  }
  return json as T;
}

export interface HealthInfo {
  ok: boolean;
  env: string;
  service: string;
  sign_js_ready?: boolean;
  sign_js_detail?: string;
}

export interface BootstrapInfo {
  user_id: string;
  username: string;
  data_dir: string;
  http_port: number;
  api_prefix: string;
}

export interface DouyinAccount {
  id: string;
  nickname: string;
  status: number;
  credential_state?: string;
  auto_reply_enabled?: boolean;
  reply_today?: number;
  daily_reply_quota?: number;
  sec_uid?: string;
  avatar?: string;
}

export interface QuickCreatePayload {
  bundle?: string;
  cookie?: string;
  web_protect?: string;
  keys?: string;
  auto_reply_enabled?: boolean;
  daily_reply_quota?: number;
}

export interface ImportCredentialPayload {
  bundle?: string;
  cookie?: string;
  web_protect?: string;
  keys?: string;
}

export function getHealth() {
  return request<HealthInfo>('/health');
}

export function getBootstrap() {
  return request<BootstrapInfo>('/bootstrap');
}

export interface RuntimeLogFile {
  name: string;
  path: string;
  size: number;
  modified_at: number;
}

export interface RuntimeLogTail {
  files: string[];
  content: string;
  message: string;
  log_dir?: string;
}

export function listRuntimeLogFiles() {
  return request<{ items: RuntimeLogFile[]; log_dir?: string }>('/runtime-logs/files', undefined, true);
}

export function tailRuntimeLogs(params?: { lines?: number; file?: string }) {
  return request<RuntimeLogTail>(
    withQuery('/runtime-logs/tail', {
      lines: params?.lines ?? 400,
      file: params?.file,
    }),
    undefined,
    true,
  );
}

export interface AdminLoginResult {
  token: string;
  expires_in: number;
  expires_at: number;
}

export interface AdminDashboard {
  service: {
    env: string;
    data_dir: string;
    http_port: number;
    accounts_total: number;
    accounts_auto_reply_on: number;
    accounts_online: number;
    accounts: Array<Record<string, unknown>>;
    sessions: Array<Record<string, unknown>>;
    checked_at: string;
  };
  processes: {
    api: Record<string, unknown>;
    related_processes: Array<Record<string, unknown>>;
    system: Record<string, unknown>;
  };
  database: Record<string, unknown>;
}

export interface EmergencyStopResult {
  ok: boolean;
  message: string;
  accounts_stopped: number;
  commands_cleared: number;
  messages_marked_processed: number;
  stopped_at: string;
}

export function adminLogin(password: string) {
  return request<AdminLoginResult>(
    '/admin/login',
    { method: 'POST', body: JSON.stringify({ password }) },
  );
}

export function adminLogout() {
  return request<{ ok: boolean }>('/admin/logout', { method: 'POST' }, true);
}

export function getAdminDashboard() {
  return request<AdminDashboard>('/admin/dashboard', undefined, true);
}

export function adminEmergencyStop(reason = '管理员急停') {
  return request<EmergencyStopResult>(
    '/admin/emergency-stop',
    { method: 'POST', body: JSON.stringify({ reason }) },
    true,
  );
}

export function listAccounts() {
  return request<DouyinAccount[]>('/douyin/account/all');
}

export function quickCreateAccount(data: QuickCreatePayload) {
  return request<DouyinAccount>('/douyin/account/quick-create', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export function importCredential(accountId: string, data: ImportCredentialPayload) {
  return request<{ success: boolean; message?: string }>(
    `/douyin/account/${accountId}/import-credential`,
    {
      method: 'POST',
      body: JSON.stringify(data),
    },
  );
}

export interface ConversationItem {
  id: string;
  peer_sec_uid: string;
  peer_nickname?: string | null;
  peer_avatar?: string | null;
  peer_unique_id?: string | null;
  last_message_at?: string | null;
  last_message_preview?: string | null;
  unread_count: number;
}

export interface MessageItem {
  id: string;
  direction: 'in' | 'out';
  content_type: string;
  content: string;
  received_at?: string | null;
  processed: boolean;
  sender_name?: string | null;
  sender_avatar?: string | null;
}

export function listConversations(accountId: string) {
  return request<ConversationItem[]>(`/douyin/account/${accountId}/conversations`);
}

export function listMessages(accountId: string, conversationId: string) {
  return request<MessageItem[]>(
    `/douyin/account/${accountId}/conversation/${conversationId}/messages`,
  );
}

export function sendManualReply(accountId: string, conversationId: string, text: string) {
  return request<{ success: boolean; message?: string; command_id?: string | null }>(
    `/douyin/account/${accountId}/manual-reply`,
    {
      method: 'POST',
      body: JSON.stringify({ conversation_id: conversationId, text }),
    },
  );
}

export interface WorkerCommandStatus {
  command_id: string;
  consumed: boolean;
  status: 'pending' | 'success' | 'failed' | 'unknown';
  error?: string | null;
  message_id?: string | null;
}

export function getWorkerCommandStatus(commandId: string) {
  return request<WorkerCommandStatus>(`/douyin/worker-command/${commandId}`);
}

export interface PageResult<T> {
  items: T[];
  total: number;
}

function withQuery(path: string, params?: Record<string, string | number | boolean | undefined>) {
  if (!params) return path;
  const qs = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== '') qs.set(key, String(value));
  }
  const q = qs.toString();
  return q ? `${path}?${q}` : path;
}

export interface DouyinRule {
  id: string;
  name: string;
  account_id?: string | null;
  account_nickname?: string | null;
  match_type: string;
  match_type_display?: string;
  keywords?: string[];
  regex_pattern?: string | null;
  reply_text?: string;
  links?: RuleLink[] | string[];
  send_mode?: string;
  template_id?: string | null;
  template_name?: string | null;
  priority: number;
  status: boolean;
  cooldown_seconds?: number;
  remark?: string | null;
}

export interface RuleLink {
  title?: string;
  url: string;
}

export interface DouyinRuleInput {
  account_id?: string | null;
  name: string;
  match_type: string;
  keywords?: string[];
  regex_pattern?: string | null;
  reply_text?: string;
  links?: RuleLink[];
  send_mode?: string;
  template_id?: string | null;
  priority?: number;
  status?: boolean;
  cooldown_seconds?: number;
  channel?: string;
  weekday_mask?: string;
}

export interface DouyinTemplate {
  id: string;
  name: string;
  content: string;
  status: boolean;
  use_count?: number;
  variables?: string[];
  remark?: string | null;
}

export interface DouyinTemplateInput {
  name: string;
  content: string;
  status?: boolean;
  remark?: string | null;
}

export interface DouyinReplyLog {
  id: string;
  account_id?: string | null;
  account_nickname?: string | null;
  conversation_id?: string | null;
  peer_nickname?: string | null;
  matched_rule_id?: string | null;
  rule_name?: string | null;
  reply_text?: string;
  result: string;
  result_display?: string;
  error_message?: string | null;
  trigger_message_content?: string | null;
  sys_create_datetime?: string;
  duration_ms?: number | null;
}

export interface DouyinReplyLogStat {
  total: number;
  success: number;
  failed: number;
  skipped: number;
  cooldown: number;
  quota_exceeded: number;
  silent: number;
  avg_duration_ms: number;
}

export interface AccountPatch {
  auto_reply_enabled?: boolean;
  daily_reply_quota?: number;
  min_interval_seconds?: number;
  max_interval_seconds?: number;
}

export function patchAccount(accountId: string, data: AccountPatch) {
  return request<DouyinAccount>(`/douyin/account/${accountId}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
}

export function listRulesByAccount(accountId: string) {
  return request<DouyinRule[]>(`/douyin/rule/by/account/${accountId}`);
}

export function listRules(params?: { account_id?: string; page?: number; pageSize?: number }) {
  return request<PageResult<DouyinRule>>(
    withQuery('/douyin/rule', { page: params?.page ?? 1, pageSize: params?.pageSize ?? 100, account_id: params?.account_id }),
  );
}

export function createRule(data: DouyinRuleInput) {
  return request<DouyinRule>('/douyin/rule', {
    method: 'POST',
    body: JSON.stringify({
      channel: 'dm',
      weekday_mask: '1111111',
      send_mode: 'multi_message',
      priority: 0,
      status: true,
      cooldown_seconds: 300,
      ...data,
    }),
  });
}

export function patchRule(ruleId: string, data: Partial<DouyinRuleInput>) {
  return request<DouyinRule>(`/douyin/rule/${ruleId}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
}

export function deleteRule(ruleId: string) {
  return request<DouyinRule>(`/douyin/rule/${ruleId}`, { method: 'DELETE' });
}

export function listTemplatesAll() {
  return request<Pick<DouyinTemplate, 'id' | 'name'>[]>('/douyin/template/all');
}

export function listTemplates(params?: { page?: number; pageSize?: number }) {
  return request<PageResult<DouyinTemplate>>(
    withQuery('/douyin/template', { page: params?.page ?? 1, pageSize: params?.pageSize ?? 100 }),
  );
}

export function createTemplate(data: DouyinTemplateInput) {
  return request<DouyinTemplate>('/douyin/template', {
    method: 'POST',
    body: JSON.stringify({ status: true, ...data }),
  });
}

export function patchTemplate(templateId: string, data: Partial<DouyinTemplateInput>) {
  return request<DouyinTemplate>(`/douyin/template/${templateId}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
}

export function deleteTemplate(templateId: string) {
  return request<{ success: boolean }>(`/douyin/template/${templateId}`, { method: 'DELETE' });
}

export function listReplyLogs(params?: {
  account_id?: string;
  result?: string;
  page?: number;
  pageSize?: number;
}) {
  return request<PageResult<DouyinReplyLog>>(
    withQuery('/douyin/reply-log', {
      page: params?.page ?? 1,
      pageSize: params?.pageSize ?? 50,
      account_id: params?.account_id,
      result: params?.result,
    }),
  );
}

export function getReplyLogStat(accountId?: string) {
  return request<DouyinReplyLogStat>(
    withQuery('/douyin/reply-log/stat/summary', { account_id: accountId }),
  );
}

export function matchTypeLabel(type: string): string {
  const map: Record<string, string> = {
    contains: '关键词',
    regex: '正则',
    default: '兜底',
  };
  return map[type] ?? type;
}

export function resultLabel(result: string): string {
  const map: Record<string, string> = {
    success: '成功',
    failed: '失败',
    skipped: '跳过',
    cooldown: '冷却中',
    quota_exceeded: '超配额',
    silent: '静默时段',
  };
  return map[result] ?? result;
}

export function statusLabel(status: number): string {
  const map: Record<number, string> = {
    0: '未登录',
    1: '在线',
    2: '暂停',
    3: '已禁用',
  };
  return map[status] ?? `状态${status}`;
}

export function credentialLabel(state?: string): string {
  const map: Record<string, string> = {
    sendable: '可发送',
    receive_only: '仅接收',
    invalid: '已失效',
    unknown: '未知',
  };
  return map[state || ''] || state || '未知';
}
