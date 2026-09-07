const getApiPrefix = () => {
  if (
    typeof window !== 'undefined' &&
    ((window as any).__TAURI_INTERNALS__ ||
      window.location.protocol.startsWith('tauri') ||
      window.location.host.includes('tauri') ||
      window.location.protocol === 'file:')
  ) {
    return 'http://127.0.0.1:18765/api/client/v1';
  }
  return '/api/client/v1';
};

const API_PREFIX = getApiPrefix();
export const ADMIN_TOKEN_KEY = 'dyauthreply_admin_token';

async function parseError(res: Response): Promise<string> {
  const text = await res.text();
  const fallback = res.statusText || `请求失败（HTTP ${res.status}）`;
  try {
    const json = JSON.parse(text) as { detail?: unknown; message?: string };
    const detail = formatApiDetail(json.detail);
    return detail || json.message || text || fallback;
  } catch {
    return text || fallback;
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
  let res: Response;
  if (isTauriRuntime()) {
    if (init?.body != null && typeof init.body !== 'string') throw new Error('本地接口需要 JSON 请求内容');
    const { invoke } = await import('@tauri-apps/api/core');
    const response = await invoke<{ status: number; body: string }>('native_request', {
      path: `/api/client/v1${path}`, method: init?.method || 'GET', body: init?.body ?? null,
      adminToken: admin ? getAdminToken() : null,
    });
    res = new Response(response.status === 204 ? null : response.body, { status: response.status });
  } else {
    res = await fetch(`${API_PREFIX}${path}`, { ...init, headers });
  }
  if (!res.ok) {
    throw new Error(await parseError(res));
  }
  const json = await res.json();
  if (json && typeof json === 'object' && json._runtime_reload === true) {
    if (isTauriRuntime()) {
      const { invoke } = await import('@tauri-apps/api/core');
      await invoke('native_reload_configuration');
    }
    else {
      const reload = await fetch(`${API_PREFIX}/runtime/reload`, { method: 'POST' });
      if (!reload.ok) throw new Error(await parseError(reload));
      const previous = await reload.json() as { instance: string };
      // A desktop-owned service restarts through its Rust supervisor, never via a browser-spawned worker.
      await new Promise((resolve) => setTimeout(resolve, 500));
      for (let attempt = 0; attempt < 120; attempt += 1) {
        try { const health = await fetch(`${API_PREFIX}/runtime/status`); if (health.ok && (await health.json()).instance !== previous.instance) break; } catch { /* restart in progress */ }
        if (attempt === 119) throw new Error('配置已保存，等待服务重启超时，请重新连接');
        await new Promise((resolve) => setTimeout(resolve, 500));
      }
    }
  }
  if (json && typeof json === 'object' && 'code' in json && json.code === 2000) {
    return json.data as T;
  }
  return json as T;
}

export async function restartNativeService(): Promise<void> {
  if (!isTauriRuntime()) return;
  const { invoke } = await import('@tauri-apps/api/core');
  await invoke('native_restart');
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
  license?: ClientLicenseStatus;
}

export interface ClientLicensePlanSummary {
  id: string;
  code: string;
  name: string;
  feature_flags?: Record<string, unknown>;
  heartbeat_interval_minutes: number;
  grace_period_minutes: number;
  max_devices: number;
}

export interface ClientLicenseStatus {
  state: 'unactivated' | 'active' | 'grace' | 'expired' | 'revoked' | 'invalid';
  state_label: string;
  can_use_business: boolean;
  needs_activation: boolean;
  device_fingerprint: string;
  device_name: string;
  os_type: string;
  os_version: string;
  app_version: string;
  activation_status: string;
  license_key_id: string;
  masked_code: string;
  activated_at?: string | null;
  last_check_in_at?: string | null;
  next_check_in_at?: string | null;
  last_valid_until?: string | null;
  expires_at?: string | null;
  lease_expires_at?: string | null;
  lease_sequence?: number;
  heartbeat_interval_minutes: number;
  grace_period_minutes: number;
  last_error?: string;
  plan?: ClientLicensePlanSummary | null;
}

export interface DouyinAccount {
  id: string;
  nickname: string;
  status: number;
  credential_state?: string;
  last_probe_error?: string | null;
  auto_reply_enabled?: boolean;
  runtime_auto_reply_enabled?: boolean;
  reply_today?: number;
  daily_reply_quota?: number;
  sec_uid?: string;
  avatar?: string;
  unique_id?: string;
  follower_count?: number;
  following_count?: number;
  aweme_count?: number;
  total_favorited?: number;
  last_profile_sync_at?: string | null;
}

export interface QuickCreatePayload {
  bundle?: string;
  cookie?: string;
  web_protect?: string;
  keys?: string;
  auto_reply_enabled?: boolean;
  runtime_auto_reply_enabled?: boolean;
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

export function getLicenseStatus() {
  return request<ClientLicenseStatus>('/license/status');
}

export function activateLicense(data: { license_code: string }) {
  return request<ClientLicenseStatus>('/license/activate', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export function refreshLicenseStatus() {
  return request<ClientLicenseStatus>('/license/check-in', {
    method: 'POST',
    body: JSON.stringify({}),
  });
}

export function deactivateLicense(reason = '客户端主动解绑') {
  return request<ClientLicenseStatus>('/license/deactivate', {
    method: 'POST',
    body: JSON.stringify({ reason }),
  });
}

export interface AppUpdateInfo {
  current_version: string;
  latest_version: string;
  has_update: boolean;
  mandatory: boolean;
  notes: string;
  download_url: string;
  release_page: string;
  extension_version?: string;
  extension_url?: string;
  extension_file?: string;
}

export function checkAppUpdate(current = '') {
  return request<AppUpdateInfo>(withQuery('/app-update/check', { current }));
}

export interface ClientAnnouncement {
  id: string;
  title: string;
  content: string;
  level: 'info' | 'warning' | 'urgent';
  publish_time: string | null;
  expire_time: string | null;
}

export function listClientAnnouncements(limit = 10) {
  return request<ClientAnnouncement[]>(withQuery('/announcements', { limit }));
}

// ==================== 应用内自动更新（tauri-plugin-updater + 多镜像竞速）====================

/** 是否运行在 Tauri 桌面壳内（决定能否走应用内 updater）。 */
export function inTauriRuntime(): boolean {
  return isTauriRuntime();
}

/** Rust `check_app_update_mirrors` 返回结构。 */
export interface TauriUpdateCheck {
  available: boolean;
  currentVersion: string;
  version?: string | null;
  notes?: string | null;
  endpointUsed?: string | null;
}

/** 下载进度事件（与 Rust UpdateProgress 对齐）。 */
export type UpdateProgress =
  | { event: 'started'; contentLength?: number | null }
  | { event: 'progress'; downloaded: number; contentLength?: number | null }
  | { event: 'finished' };

/** 镜像竞速 + check：检查是否有签名更新可用。仅在 Tauri 壳内可用。 */
export async function checkUpdateViaTauri(mirrors: string[]): Promise<TauriUpdateCheck> {
  const { invoke } = await import('@tauri-apps/api/core');
  return invoke<TauriUpdateCheck>('check_app_update_mirrors', { mirrors });
}

/**
 * 下载并安装更新（镜像竞速 -> 下载带进度 -> 签名校验 -> 释放后端 -> 覆盖安装 -> 重启）。
 * 成功后应用会自动重启，此 Promise 通常不会 resolve；失败会 reject。
 */
export async function runUpdateViaTauri(
  mirrors: string[],
  onProgress?: (p: UpdateProgress) => void,
): Promise<void> {
  const core = await import('@tauri-apps/api/core');
  const channel = new core.Channel<UpdateProgress>();
  if (onProgress) channel.onmessage = onProgress;
  await core.invoke('download_and_install_update', { mirrors, onEvent: channel });
}

export async function openExternalUrl(url: string): Promise<void> {
  if (!url) return;
  const tauri = (window as unknown as { __TAURI__?: { opener?: { openUrl?: (u: string) => Promise<void> } } }).__TAURI__;
  try {
    if (tauri?.opener?.openUrl) {
      await tauri.opener.openUrl(url);
      return;
    }
  } catch {
    // fall back to window.open below
  }
  window.open(url, '_blank');
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

export function createLocalAdminSession() {
  return request<AdminLoginResult>('/admin/local-session', { method: 'POST' });
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

export interface CapacityPolicy { conservative: boolean; manual_limit: number | null }
export interface CapacityBenchmark {
  estimated_accounts: number; tested_accounts: number; tested_events: number;
  duration_ms: number; completed_at_ms: number; model_version: string;
  scheduler_events_per_second: number; cpu_usage_percent: number;
  available_memory_bytes: number; cpu_limit_accounts: number;
  memory_limit_accounts: number; scheduler_limit_accounts: number;
}
export interface CapacityEstimate {
  logical_cpus: number; total_memory_bytes: number; available_memory_bytes: number;
  reported_available_memory_bytes: number; used_memory_bytes: number;
  hardware_limit: number; validated_ceiling: number; effective_limit: number;
  hosted_accounts: number; occupancy_percent: number | null; memory_headroom_accounts: number;
  recommended_min_accounts: number; recommended_max_accounts: number;
  memory_pressure_percent: number; memory_sample_source: 'system_available' | 'derived_total_minus_used';
  limiting_factor: string; cpu_usage_percent: number | null; policy: CapacityPolicy; model_version: string;
  benchmark: CapacityBenchmark | null;
}
export function getCapacity() { return request<CapacityEstimate>('/runtime/capacity'); }
export function runCapacityBenchmark() {
  return request<{ benchmark: CapacityBenchmark }>('/runtime/capacity/benchmark', {
    method: 'POST', body: '{}',
  });
}
export function saveCapacity(policy: CapacityPolicy) {
  return request<CapacityEstimate>('/runtime/capacity', { method: 'PUT', body: JSON.stringify(policy) });
}

export interface ProfileStats {
  ok: boolean;
  error?: string | null;
  nickname?: string | null;
  avatar?: string | null;
  unique_id?: string | null;
  follower_count: number;
  following_count: number;
  aweme_count: number;
  total_favorited: number;
  last_profile_sync_at?: string | null;
  cached: boolean;
}

export interface WorkItem {
  aweme_id: string;
  desc: string;
  cover: string;
  work_type: string;
  like_count: number;
  comment_count: number;
  collect_count: number;
  share_count: number;
  create_time: number;
  share_url: string;
}

export interface WorksResult {
  ok: boolean;
  error?: string | null;
  items: WorkItem[];
  max_cursor: string;
  has_more: boolean;
}

/** 账号主页统计（缓存优先，refresh=true 强制实时拉取） */
export function getProfileStats(accountId: string, refresh = false) {
  return request<ProfileStats>(
    withQuery(`/douyin/account/${accountId}/profile-stats`, {
      refresh: refresh ? 'true' : undefined,
    }),
  );
}

/** 账号作品列表（实时分页） */
export function getAccountWorks(
  accountId: string,
  params?: { cursor?: string; count?: number },
) {
  return request<WorksResult>(
    withQuery(`/douyin/account/${accountId}/works`, {
      cursor: params?.cursor ?? '0',
      count: params?.count ?? 18,
    }),
  );
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

export type QuickAuthMode = 'create' | 'refresh' | 'recover';
export type QuickAuthState =
  | 'launching'
  | 'awaiting_login'
  | 'collecting'
  | 'awaiting_confirmation'
  | 'importing'
  | 'closing'
  | 'completed'
  | 'cancelled'
  | 'timed_out'
  | 'browser_exited'
  | 'failed';

export interface QuickAuthComponent {
  state: 'ready' | 'missing' | 'incompatible' | 'corrupt' | 'installing' | 'failed';
  version: string;
  source: 'managed' | 'development_browser';
  install_required: boolean;
  message: string;
  downloaded_bytes: number;
  total_bytes: number;
  progress_percent?: number | null;
  can_rollback: boolean;
}

export interface QuickAuthCompleteness {
  cookie: boolean;
  scoped_cookies: boolean;
  server_data: boolean;
  private_key: boolean;
  dtrait: boolean;
  identity: boolean;
  ready: boolean;
}

export interface QuickAuthAccount {
  sec_uid: string;
  nickname: string;
  unique_id: string;
  avatar: string;
}

export interface QuickAuthSession {
  session_id: string;
  mode: QuickAuthMode;
  target_account_id?: string | null;
  state: QuickAuthState;
  message: string;
  started_at_ms: number;
  expires_at_ms: number;
  completeness: QuickAuthCompleteness;
  account?: QuickAuthAccount | null;
  component: QuickAuthComponent;
}

export function getQuickAuthComponent() {
  return request<QuickAuthComponent>('/douyin/quick-auth/component');
}

export function installQuickAuthComponent() {
  return request<QuickAuthComponent>('/douyin/quick-auth/component/install', {
    method: 'POST',
    body: '{}',
  });
}

export function rollbackQuickAuthComponent() {
  return request<QuickAuthComponent>('/douyin/quick-auth/component/rollback', {
    method: 'POST',
    body: '{}',
  });
}

export function startQuickAuth(mode: QuickAuthMode, targetAccountId?: string) {
  return request<QuickAuthSession>('/douyin/quick-auth/start', {
    method: 'POST',
    body: JSON.stringify({ mode, target_account_id: targetAccountId || null }),
  });
}

export function getQuickAuthSession(sessionId: string) {
  return request<QuickAuthSession>(`/douyin/quick-auth/${sessionId}`);
}

export function getCurrentQuickAuthSession() {
  return request<QuickAuthSession | null>('/douyin/quick-auth/current');
}

export function confirmQuickAuth(sessionId: string) {
  return request<DouyinAccount & { success: boolean; quick_auth: QuickAuthSession; runtime_reload_required?: boolean }>(
    `/douyin/quick-auth/${sessionId}/confirm`,
    { method: 'POST', body: '{}' },
  );
}

export function cancelQuickAuth(sessionId: string) {
  return request<QuickAuthSession>(`/douyin/quick-auth/${sessionId}/cancel`, {
    method: 'POST',
    body: '{}',
  });
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

export interface MessageMedia {
  kind?: string;
  url?: string;
  cover_url?: string;
  duration_s?: number | string | null;
  duration_ms?: number | string | null;
  width?: number | null;
  height?: number | null;
  item_id?: string;
  ai_text?: string | null;
  vid?: string;
  inline_pic?: string;
  encrypted?: boolean;
  [key: string]: unknown;
}

export interface MessageItem {
  server_message_id?: string;
  client_message_id?: string;
  id: string;
  direction: 'in' | 'out';
  content_type: string;
  content: string;
  received_at?: string | null;
  processed: boolean;
  sender_name?: string | null;
  sender_avatar?: string | null;
  media?: MessageMedia | null;
}

export interface ConversationListResponse {
  items: ConversationItem[];
  total: number;
  page: number;
  page_size: number;
  has_more: boolean;
}

export function listConversations(
  accountId: string,
  params?: { page?: number; page_size?: number; keyword?: string },
) {
  return request<ConversationListResponse>(
    withQuery(`/douyin/account/${accountId}/conversations`, {
      page: params?.page ?? 1,
      page_size: params?.page_size ?? 50,
      keyword: params?.keyword,
    }),
  );
}

export function listMessages(accountId: string, conversationId: string) {
  return request<MessageItem[]>(
    `/douyin/account/${accountId}/conversation/${conversationId}/messages`,
  );
}

/**
 * 加密图片解密代理 URL（后端下载密文→AES-GCM 解密→转 JPEG）。
 * 客户端走本机回环鉴权，<img> 可直接引用此 URL。
 */
export function messageImageUrl(
  accountId: string,
  conversationId: string,
  messageId: string,
): string {
  return `${API_PREFIX}/douyin/account/${accountId}/conversation/${conversationId}/message/${messageId}/image`;
}

export function refreshConversationUser(accountId: string, conversationId: string) {
  return request<{ success: boolean; message?: string }>(
    `/douyin/account/${accountId}/conversation/${conversationId}/refresh-user`,
    { method: 'POST' },
  );
}

export function sendManualReply(accountId: string, conversationId: string, text: string, requestId: string) {
  return request<{ success: boolean; message?: string; command_id?: string | null; client_message_id?: string }>(
    `/douyin/account/${accountId}/manual-reply`,
    {
      method: 'POST',
      body: JSON.stringify({ conversation_id: conversationId, text, request_id: requestId }),
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
  retention_days?: number;
  has_gap?: boolean;
  sync_pending?: boolean;
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
  account_ids?: string[];
  account_nicknames?: string[];
  match_type: string;
  match_type_display?: string;
  keywords?: string[];
  regex_pattern?: string | null;
  reply_text?: string;
  links?: RuleLink[] | string[];
  send_mode?: string;
  template_id?: string | null;
  template_name?: string | null;
  card_ids?: string[];
  priority: number;
  status: boolean;
  cooldown_seconds?: number;
  remark?: string | null;
  time_window_start?: string | null;
  time_window_end?: string | null;
  weekday_mask?: string;
}

export interface RuleLink {
  title?: string;
  url: string;
}

export interface DouyinRuleInput {
  account_id?: string | null;
  account_ids?: string[];
  force_move?: boolean;
  name: string;
  match_type: string;
  keywords?: string[];
  regex_pattern?: string | null;
  reply_text?: string;
  links?: RuleLink[];
  send_mode?: string;
  template_id?: string | null;
  card_ids?: string[];
  priority?: number;
  status?: boolean;
  cooldown_seconds?: number;
  channel?: string;
  weekday_mask?: string;
  time_window_start?: string | null;
  time_window_end?: string | null;
}

export interface RuleAccountConflict {
  account_id: string;
  account_nickname: string;
  rule_id: string;
  rule_name: string;
}

/** 解析后端 409 冲突响应；非冲突返回 null */
export function parseAccountConflict(err: unknown): RuleAccountConflict[] | null {
  const msg = err instanceof Error ? err.message : typeof err === 'string' ? err : '';
  if (!msg || !msg.includes('account_conflict')) return null;
  try {
    const parsed = JSON.parse(msg);
    if (parsed?.code === 'account_conflict' && Array.isArray(parsed.conflicts)) {
      return parsed.conflicts as RuleAccountConflict[];
    }
  } catch {
    return null;
  }
  return null;
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
  mode?: string;
  batch_id?: string | null;
  platform_message_ids?: string[];
  attempt_count?: number;
  content_is_excerpt?: boolean;
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
  pending?: number;
  uncertain?: number;
  partial?: number;
  has_gap?: boolean;
  retention_days?: number;
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

export function deleteAccount(accountId: string) {
  return request<DouyinAccount>(`/douyin/account/${accountId}`, {
    method: 'DELETE',
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

export function cloneRule(ruleId: string) {
  return request<DouyinRule>(`/douyin/rule/${ruleId}/clone`, { method: 'POST' });
}

export interface DryRunMatchResult {
  matched: boolean;
  rule_id?: string;
  rule_name?: string;
  match_type?: string;
  reply_preview?: string;
  miss_reasons: string[];
}

export function dryRunMatch(params: { text: string; account_id?: string; channel?: string }) {
  return request<DryRunMatchResult>('/douyin/rule/dry-run-match', {
    method: 'POST',
    body: JSON.stringify({ channel: 'dm', ...params }),
  });
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

// ---------- 伪装卡片 ----------
export interface DouyinCard {
  id: string;
  title: string;
  description?: string;
  cover_file_id?: string | null;
  cover_url?: string | null;
  target_url: string;
  remark?: string | null;
  status: boolean;
  landing_url?: string | null;
  sync_state?: string;
}

export interface DouyinCardInput {
  title: string;
  description?: string;
  cover_file_id?: string | null;
  target_url: string;
  remark?: string | null;
  status?: boolean;
}

export interface DouyinCardSimple {
  id: string;
  title: string;
  cover_url?: string | null;
  target_url?: string | null;
}

export function listCards(params?: { page?: number; pageSize?: number; title?: string }) {
  return request<PageResult<DouyinCard>>(
    withQuery('/douyin/card', {
      page: params?.page ?? 1,
      page_size: params?.pageSize ?? 100,
      title: params?.title,
    }),
  );
}

export function listCardsAll() {
  return request<DouyinCardSimple[]>('/douyin/card/all');
}

export function createCard(data: DouyinCardInput) {
  return request<DouyinCard>('/douyin/card', {
    method: 'POST',
    body: JSON.stringify({ status: true, ...data }),
  });
}

export function updateCard(cardId: string, data: DouyinCardInput) {
  return request<DouyinCard>(`/douyin/card/${cardId}`, {
    method: 'PUT',
    body: JSON.stringify(data),
  });
}

export function deleteCard(cardId: string) {
  return request<{ success: boolean }>(`/douyin/card/${cardId}`, { method: 'DELETE' });
}

/** 上传封面图：受限 JSON IPC，经 Rust Agent 转为 multipart 并上传公网。 */
export async function uploadCardCover(file: File): Promise<{ cover_file_id: string; cover_url: string }> {
  if (!file.type.startsWith('image/') || file.size <= 0 || file.size > 2 * 1024 * 1024) {
    throw new Error('封面仅支持不超过 2MB 的图片');
  }
  const dataUrl = await new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error('封面读取失败'));
    reader.onload = () => resolve(String(reader.result || ''));
    reader.readAsDataURL(file);
  });
  const comma = dataUrl.indexOf(',');
  if (comma < 0) throw new Error('封面编码失败');
  return request<{ cover_file_id: string; cover_url: string }>('/douyin/card/cover', {
    method: 'POST',
    body: JSON.stringify({
      name: file.name,
      mime: file.type,
      data_base64: dataUrl.slice(comma + 1),
    }),
  });
}


export function listReplyLogs(params?: {
  mode?: 'automatic' | 'manual' | 'all';
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
      mode: params?.mode,
    }),
  );
}

export function getReplyLogStat(accountId?: string, scope?: 'all' | 'today', mode: 'automatic' | 'manual' | 'all' = 'automatic') {
  return request<DouyinReplyLogStat>(
    withQuery('/douyin/reply-log/stat/summary', { account_id: accountId, scope, mode }),
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
    2: '登录失效',
    3: '已禁用',
  };
  return map[status] ?? `状态${status}`;
}

export function credentialLabel(state?: string): string {
  const map: Record<string, string> = {
    sendable: '可发送',
    receive_only: '仅接收',
    risk_controlled: '发送封控（仅接收）',
    invalid: '已失效',
    unknown: '未知',
  };
  return map[state || ''] || state || '未知';
}

// ==================== 实时私信 WebSocket（方案 D）====================

function isTauriRuntime(): boolean {
  return (
    typeof window !== 'undefined' &&
    Boolean(
      (window as any).__TAURI_INTERNALS__ ||
        window.location.protocol.startsWith('tauri') ||
        window.location.host.includes('tauri') ||
        window.location.protocol === 'file:',
    )
  );
}

export function getRealtimeWsUrl(): string {
  if (isTauriRuntime()) return 'ws://127.0.0.1:18765/ws/client/douyin/';
  if (typeof window === 'undefined') return '';
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${proto}//${window.location.host}/ws/client/douyin/`;
}

export interface RealtimeNewMessage {
  account_id: string;
  conversation_ids: string[];
}

export interface RealtimeAccountStateChanged {
  revision: string;
  session_id?: string;
}

export interface RealtimeHandlers {
  onReplyLogChanged?: (data: RealtimeAccountStateChanged) => void;
  onQuickAuthChanged?: (data: RealtimeAccountStateChanged) => void;
  onNewMessage?: (data: RealtimeNewMessage) => void;
  onAccountStateChanged?: (data: RealtimeAccountStateChanged) => void;
  onOpen?: () => void;
  onClose?: () => void;
}

/**
 * 客户端实时私信连接：维护一条到本机 API 的 WebSocket，断线自动重连（指数退避），
 * 定时 ping 保活。收到 new_message 信号后由调用方走 REST 拉增量。
 */
export class DouyinRealtime {
  private ws: WebSocket | null = null;
  private nativeId: string | null = null;
  private nativeConnecting = false;
  private nativeGeneration = 0;
  private readonly url: string;
  private readonly handlers: RealtimeHandlers;
  private sub: { account_id?: string; conversation_id?: string } = {};
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private pingTimer: ReturnType<typeof setInterval> | null = null;
  private closed = false;
  private backoff = 1000;

  constructor(handlers: RealtimeHandlers) {
    this.handlers = handlers;
    this.url = getRealtimeWsUrl();
  }

  get connected(): boolean {
    return this.nativeId !== null || this.ws?.readyState === WebSocket.OPEN;
  }

  connect(): void {
    if (!this.url || this.closed) return;
    if (isTauriRuntime()) { void this.connectNative(); return; }
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
      return;
    }
    try {
      this.ws = new WebSocket(this.url);
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.ws.onopen = () => {
      this.backoff = 1000;
      this.sendSubscribe();
      this.startPing();
      this.handlers.onOpen?.();
    };
    this.ws.onmessage = (ev) => {
      try { this.dispatch(JSON.parse(ev.data as string)); } catch { /* Ignore malformed notifications. */ }
    };
    this.ws.onclose = () => {
      this.stopPing();
      this.handlers.onClose?.();
      if (!this.closed) this.scheduleReconnect();
    };
    this.ws.onerror = () => {
      this.ws?.close();
    };
  }

  private dispatch(msg: { type?: string; data?: unknown }): void {
    if (msg.type === 'reply_log_changed' && msg.data) this.handlers.onReplyLogChanged?.(msg.data as RealtimeAccountStateChanged);
    else if (msg.type === 'quick_auth_changed' && msg.data) this.handlers.onQuickAuthChanged?.(msg.data as RealtimeAccountStateChanged);
    else if (msg.type === 'new_message' && msg.data) this.handlers.onNewMessage?.(msg.data as RealtimeNewMessage);
    else if (msg.type === 'account_state_changed' && msg.data) this.handlers.onAccountStateChanged?.(msg.data as RealtimeAccountStateChanged);
  }

  private async connectNative(): Promise<void> {
    if (this.nativeId || this.nativeConnecting || this.closed) return;
    this.nativeConnecting = true;
    const generation = ++this.nativeGeneration;
    try {
      const { invoke, Channel } = await import('@tauri-apps/api/core');
      const channel = new Channel<{ event: string; data?: { type?: string; data?: unknown } }>();
      channel.onmessage = (event) => {
        if (generation !== this.nativeGeneration) return;
        if (event.event === 'message' && event.data) this.dispatch(event.data);
        if (event.event === 'reload_required') {
          ++this.nativeGeneration;
          this.nativeId = null; this.nativeConnecting = false; this.stopPing();
          this.handlers.onClose?.();
          void invoke('native_reload_configuration')
            .catch(() => undefined)
            .finally(() => { if (!this.closed) this.scheduleReconnect(); });
          return;
        }
        if (event.event === 'closed') {
          ++this.nativeGeneration;
          this.nativeId = null; this.nativeConnecting = false; this.stopPing();
          this.handlers.onClose?.(); this.scheduleReconnect();
        }
      };
      const id = await invoke<string>('native_ws_connect', { channel });
      if (this.closed || generation !== this.nativeGeneration) {
        await invoke('native_ws_close', { connectionId: id }); return;
      }
      this.nativeId = id; this.nativeConnecting = false; this.backoff = 1000;
      this.sendSubscribe(); this.startPing(); this.handlers.onOpen?.();
    } catch {
      if (generation !== this.nativeGeneration) return;
      this.nativeConnecting = false; this.handlers.onClose?.(); this.scheduleReconnect();
    }
  }

  private sendNotification(input: object): void {
    const id = this.nativeId;
    if (id) {
      void import('@tauri-apps/api/core').then(({ invoke }) => invoke('native_ws_send', { connectionId: id, input })).catch(() => {
        if (this.nativeId !== id) return;
        this.nativeId = null; ++this.nativeGeneration; this.stopPing(); this.handlers.onClose?.(); this.scheduleReconnect();
      });
    } else this.ws?.send(JSON.stringify(input));
  }

  subscribe(accountId?: string, conversationId?: string): void {
    this.sub = { account_id: accountId || undefined, conversation_id: conversationId || undefined };
    this.sendSubscribe();
  }

  private sendSubscribe(): void {
    if (this.connected && this.sub.account_id) {
      this.sendNotification({ type: 'subscribe', ...this.sub });
    }
  }

  private startPing(): void {
    this.stopPing();
    this.pingTimer = setInterval(() => {
      if (this.connected) this.sendNotification({ type: 'ping' });
    }, 25000);
  }

  private stopPing(): void {
    if (this.pingTimer) {
      clearInterval(this.pingTimer);
      this.pingTimer = null;
    }
  }

  private scheduleReconnect(): void {
    if (this.closed || this.reconnectTimer) return;
    const delay = this.backoff;
    this.backoff = Math.min(this.backoff * 2, 15000);
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, delay);
  }

  close(): void {
    this.closed = true;
    ++this.nativeGeneration; this.nativeConnecting = false;
    const id = this.nativeId; this.nativeId = null;
    if (id) void import('@tauri-apps/api/core').then(({ invoke }) => invoke('native_ws_close', { connectionId: id })).catch(() => {});
    this.stopPing();
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    try {
      this.ws?.close();
    } catch {
      // ignore
    }
    this.ws = null;
  }
}
