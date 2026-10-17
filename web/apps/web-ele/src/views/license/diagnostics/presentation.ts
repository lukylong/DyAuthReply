import type {
  DiagnosticAccount,
  DiagnosticDevice,
  DiagnosticEvent,
} from '#/api/core/license-diagnostics';

export type Tone = 'danger' | 'info' | 'success' | 'warning';
export interface StateView {
  label: string;
  tone: Tone;
}
const states: Record<string, StateView> = {
  running: { label: '运行中', tone: 'success' },
  starting: { label: '启动中', tone: 'info' },
  draining: { label: '正在停止', tone: 'warning' },
  stopped: { label: '已停止', tone: 'danger' },
  faulted: { label: '运行异常', tone: 'danger' },
  paused_auto: { label: '自动回复已暂停', tone: 'info' },
  owned: { label: '托管授权有效', tone: 'success' },
  unowned: { label: '尚未取得托管授权', tone: 'warning' },
  acquiring: { label: '申请托管授权', tone: 'warning' },
  lost: { label: '托管授权待恢复', tone: 'danger' },
  expired: { label: '托管授权已过期', tone: 'danger' },
  ws_healthy: { label: '实时接收正常', tone: 'success' },
  http_degraded: { label: '补拉接收模式', tone: 'warning' },
  backoff: { label: '接收重连中', tone: 'warning' },
  disconnected: { label: '接收连接中断', tone: 'danger' },
  connecting: { label: '接收连接中', tone: 'info' },
  sendable: { label: '发送已验证', tone: 'success' },
  unknown: { label: '发送待验证', tone: 'warning' },
  receive_only: { label: '仅接收，发送受限', tone: 'warning' },
  risk_controlled: { label: '客户端发送受限', tone: 'warning' },
  auth_expired: { label: '登录已失效', tone: 'danger' },
  invalid: { label: '凭证异常', tone: 'danger' },
  normal: { label: '正常', tone: 'success' },
  high: { label: '存储空间偏紧', tone: 'warning' },
  low: { label: '空间余量偏低', tone: 'warning' },
  critical: { label: '存储空间紧张', tone: 'danger' },
};
export function stateView(value?: null | string): StateView {
  return (
    states[value || ''] || {
      label: value ? `未识别状态（${value}）` : '尚无数据',
      tone: 'info',
    }
  );
}
export function reportedMillis(value?: null | string): number {
  if (!value) return Number.NaN;
  // Legacy Django timestamps are naive Asia/Shanghai values, not browser-local time.
  const iso = value.replace(' ', 'T');
  return Date.parse(
    /(?:Z|[+-]\d{2}:?\d{2})$/i.test(iso) ? iso : `${iso}+08:00`,
  );
}
export function formatReportedAt(value?: null | string): string {
  const at = reportedMillis(value);
  return Number.isFinite(at)
    ? new Intl.DateTimeFormat('zh-CN', {
        timeZone: 'Asia/Shanghai',
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hour12: false,
      }).format(at)
    : '尚未收到上报';
}
export function freshness(value?: null | string, now = Date.now()) {
  const at = reportedMillis(value),
    age = now - at;
  if (!Number.isFinite(at) || age < -5 * 60_000)
    return { stale: true, text: '上报时间待核对' };
  const minutes = Math.floor(Math.max(0, age) / 60_000);
  return {
    stale: age > 35 * 60_000,
    text:
      minutes < 1
        ? '刚刚上报'
        : minutes < 60
          ? `${minutes} 分钟前上报`
          : `${Math.floor(minutes / 60)} 小时前上报`,
  };
}
export function automaticView(account: DiagnosticAccount): StateView {
  if (account.lifecycle !== 'running') return stateView(account.lifecycle);
  if (account.ownership !== 'owned')
    return { label: '已启用，授权待恢复', tone: 'warning' };
  if (account.identity_verified !== true)
    return { label: '已启用，身份待验证', tone: 'warning' };
  if (!['sendable', 'unknown'].includes(account.send || ''))
    return { label: '已启用，发送受限', tone: 'warning' };
  if (account.inbound !== 'ws_healthy')
    return { label: '已启用，接收需关注', tone: 'warning' };
  if (account.send === 'unknown')
    return { label: '已启用，发送待验证', tone: 'warning' };
  return { label: '已启用，等待消息', tone: 'success' };
}

export function accountIssue(account: DiagnosticAccount): string {
  if (['auth_expired', 'invalid'].includes(account.send || ''))
    return '请在客户端恢复登录';
  if (account.ownership !== 'owned')
    return '托管授权尚未确认，请查看客户端授权与网络';
  if (account.identity_verified !== true)
    return '账号身份尚未确认，等待校验结果';
  if (account.inbound !== 'ws_healthy')
    return '接收链路需关注；补拉或重连不等于登录失效';
  if (account.send !== 'sendable')
    return '发送未通过验证或受限，请查看该账号发送记录';
  if (account.lifecycle === 'paused_auto')
    return '自动回复当前暂停，接收与手动发送状态独立';
  if (account.lifecycle !== 'running') return '账号任务尚未进入正常运行状态';
  return '最近快照正常；发送是否送达仍以具体消息回执为准';
}
export function summarizeDevice(row: DiagnosticDevice, now = Date.now()) {
  const fresh = freshness(row.last_report_at, now);
  const accounts = row.accounts || [];
  const receiving = accounts.filter(
    (a) =>
      a.identity_verified === true &&
      a.ownership === 'owned' &&
      a.inbound === 'ws_healthy',
  ).length;
  const sending = accounts.filter((a) => a.send === 'sendable').length;
  const automatic = accounts.filter(
    (a) =>
      a.lifecycle === 'running' &&
      a.ownership === 'owned' &&
      a.identity_verified === true &&
      a.inbound === 'ws_healthy' &&
      a.send === 'sendable',
  ).length;
  const needsAttention = accounts.some(
    (a) =>
      a.identity_verified !== true ||
      a.ownership !== 'owned' ||
      a.inbound !== 'ws_healthy' ||
      a.send !== 'sendable' ||
      !['running', 'paused_auto'].includes(a.lifecycle || ''),
  );
  let label = '运行正常',
    tone: Tone = 'success',
    advice = '最近快照中的收发及托管状态正常；并非每条消息都已送达的保证。';
  if (fresh.stale) {
    label = '状态待更新';
    tone = 'warning';
    advice =
      '超过约两次上报周期或时间异常。请检查客户端是否运行、诊断开关及网络；这不代表抖音账号离线。';
  } else if (row.runtime_phase !== 'running') {
    label = row.runtime_phase
      ? stateView(row.runtime_phase).label
      : '运行状态待确认';
    tone = ['faulted', 'stopped'].includes(row.runtime_phase || '')
      ? 'danger'
      : 'warning';
    advice = '客户端服务尚未报告正常运行，请查看下方事件。';
  } else if (
    needsAttention ||
    row.protocol_parity === false ||
    ['low', 'critical', 'high', 'full'].includes(row.storage_pressure || '') ||
    Number(row.runtime?.open_circuit_count || 0) > 0
  ) {
    label = '需要关注';
    tone = 'warning';
    advice =
      '部分账号、协议检查或存储状态异常，请按下方各项提示排查；历史告警数量不等于当前故障数量。';
  } else if (!accounts.length) {
    label = '服务运行，暂无账号';
    tone = 'info';
    advice = '客户端已上报运行状态，但当前快照没有托管账号。';
  }
  return {
    label,
    tone,
    advice,
    freshness: fresh,
    total: accounts.length,
    receiving,
    sending,
    automatic,
    pending: accounts.length - sending,
  };
}
export function metric(value: unknown): string {
  return typeof value === 'number' && Number.isFinite(value)
    ? value.toLocaleString('zh-CN')
    : '—';
}
const messages: Record<string, string> = {
  'dy-agent runtime is ready': '客户端运行服务已就绪',
  'auto-reply dropped by cooldown or quota': '自动回复因冷却或额度限制跳过',
  'auto-reply deferred by reply guard': '自动回复暂缓，等待发送条件满足',
  'peer profile backfilled': '联系人资料已补齐',
  'conversation peer resolved from member list': '已确认会话中的联系人',
  'quick-auth component downloaded and verified':
    '快捷登录组件已下载并通过校验',
  'inbound page processed': '已完成一批消息接收处理',
  'platform request rejected': '平台拒绝了本次请求',
  'auto-reply queued': '自动回复已进入发送队列',
  'native send preparation failed before network attempt':
    '发送准备失败，尚未提交给平台',
  'native send completed': '客户端发送流程已结束，请核对消息回执',
  'account removed without restarting other accounts':
    '账号已停止托管，其他账号不受影响',
};
export function eventMessage(event: DiagnosticEvent): string {
  return messages[event.message] || event.message;
}
const fieldNames: Record<string, string> = {
  account: '匿名账号',
  step: '处理环节',
  status: '响应状态',
  classification: '结果分类',
  response_codes: '平台响应码',
  error: '异常原因',
  rule: '规则编号',
  retry_attempt: '重试次数',
  inserted: '新接收数量',
  candidates: '待匹配数量',
  ignored: '跳过数量',
  deferred: '延后数量',
  reconciled_sends: '回执核验数量',
  result: '结果',
  reason: '原因',
  phase: '运行阶段',
  attempt: '发送尝试',
  elapsed_ms: '耗时（毫秒）',
  count: '数量',
  version: '版本',
  body: '脱敏响应摘要',
  host: '服务域名',
  source: '来源',
  members: '数量',
};
const values: Record<string, string> = {
  send: '发送消息',
  identity: '校验账号身份',
  self_profile: '获取账号资料',
  self_works: '获取主页作品',
  inbox: '拉取消息',
  reconcile: '补拉与核验',
  frontier: '实时接收连接',
  rule_cooldown: '规则仍在冷却期',
  account_daily_quota: '账号今日回复额度已用完',
  peer_daily_quota: '该联系人今日回复次数已达上限',
  BusinessRejected: '该消息被平台拒绝',
  RiskControlled: '发送受到平台限制',
  Delivered: '已取得发送成功回执',
  Uncertain: '结果待核验，请勿重复发送',
};
export function eventFields(event: DiagnosticEvent): string {
  const fields =
    event.fields && typeof event.fields === 'object' ? event.fields : {};
  return (
    Object.entries(fields)
      .map(
        ([key, value]) =>
          `${fieldNames[key] || key}：${typeof value === 'object' && value !== null ? JSON.stringify(value) : values[String(value)] || String(value ?? '—')}`,
      )
      .join('；') || '无附加信息'
  );
}
export function moduleLabel(target: string): string {
  if (target.includes('protocol')) return '平台通信';
  if (target.includes('automation') || target.includes('rules'))
    return '自动回复';
  if (target.includes('inbound') || target.includes('frontier'))
    return '消息接收';
  if (target.includes('store') || target.includes('workbench'))
    return '本地数据';
  if (target.includes('license') || target.includes('hosted'))
    return '授权与托管';
  if (target.includes('runtime')) return '运行调度';
  return '客户端';
}
