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
  'account identity verification failed': '账号身份校验失败',
  'IM session expired; login recovery required': '私信登录已失效，需要恢复登录',
  'peer profile backfill unavailable': '联系人资料暂不可用',
  'peer profile backfill failed': '联系人资料补全失败',
  'native audit synchronization pending': '回复记录同步等待重试',
  'workbench maintenance deferred': '聊天数据维护已延后',
  'quick-auth main site waiting for UIFID; retrying':
    '快捷登录正在等待账号信息',
  'quick-auth main site UIFID ready': '快捷登录已获取账号信息',
  'quick-auth main site page was hidden; restoring its window':
    '正在恢复快捷登录窗口',
  'quick-auth capture ready': '快捷登录资料已准备完成',
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
  'reply batch stopped': '本批回复已停止后续发送',
  'receive reconciliation failed': '消息补拉失败',
  'conversation history read repair failed; serving cache':
    '历史消息拉取失败，暂时显示缓存',
  'hosted authorization suspended; awaiting license rotation':
    '托管授权暂不可用，等待凭证恢复',
  'native license renewal pending; retry retains durable request identity':
    '客户端授权续签等待重试',
  'remote lease release unconfirmed; awaiting server TTL':
    '托管租约释放尚未确认',
  'encrypted chat image unavailable': '聊天图片加载失败',
  'quick-auth session failed': '快捷登录未完成',
  'native send was not delivered': '本条消息未取得成功回执',
  'platform risk control rejected the send; sending paused until recheck':
    '平台限制发送，账号发送已暂停',
  'native send preparation failed before network attempt':
    '发送准备失败，尚未提交给平台',
  'native send completed': '客户端发送流程已结束，请核对消息回执',
  'account removed without restarting other accounts':
    '账号已停止托管，其他账号不受影响',
};
export function eventMessage(event: DiagnosticEvent): string {
  const fields = event.fields || {};
  if (event.message === 'auto-reply queued') {
    if (fields.trigger_type === 'mutual_follow')
      return '回关自动回复已进入发送队列';
    if (fields.trigger_type === 'message')
      return '普通消息自动回复已进入发送队列';
    return '自动回复已进入发送队列（触发来源未上报）';
  }
  if (event.message === 'account automatic policy changed') {
    return fields.enabled === true
      ? '自动回复配置已启用'
      : fields.enabled === false
        ? '自动回复配置已关闭'
        : '自动回复配置已变更';
  }
  if (event.message === 'native send completed')
    return '本条消息已取得发送成功回执';
  return messages[event.message] || event.message;
}

export function eventCategory(event: DiagnosticEvent): string {
  const message = event.message || '';
  if (/quick-auth/.test(message)) return '快捷登录';
  if (/authorization|license|lease/.test(message)) return '授权与托管';
  if (/image/.test(message)) return '图片与媒体';
  if (/auto-reply|automatic policy/.test(message)) return '自动回复';
  if (/send|reply batch/.test(message)) return '消息发送';
  if (/inbound|receive|history|peer/.test(message)) return '消息接收';
  return moduleLabel(event.target || '');
}

export function traceRef(event: DiagnosticEvent): string {
  const value = event.fields?.trace_id;
  return typeof value === 'string' && /^[0-9a-f]{16}$/.test(value) ? value : '';
}

export function eventAdvice(event: DiagnosticEvent): string {
  const f = event.fields || {},
    message = event.message || '';
  const codes = String(f.response_codes || '');
  if (message === 'auto-reply queued')
    return '已建立回复任务；入队不等于发送成功。点击关联号查看同批各条消息的回执。';
  if (message === 'account automatic policy changed')
    return '这是已保存的开关变更；配置来源见下方，是否已生效请结合最新账号快照，不据此推断操作人。';
  if (message === 'reply batch stopped')
    return '该批后续消息已停止。先查看同关联号的失败或配置变更原因，不要整批重复发送。';
  if (message === 'native send completed')
    return '此条已取得成功回执，不代表整个批次均已送达或对方已读。请按关联号核对其余分段。';
  if (f.classification === 'RiskControlled' || message.includes('risk control'))
    return '当时已暂停该账号发送，接收可能仍正常。检查平台提示与最新状态；复查时间不等于保证恢复时间。';
  if (codes.includes('business=Some(7467)'))
    return '7467：该收件人的消息额度限制。等待对方回复或关系变化；不要按整个账号掉线处理或自动重发。';
  if (codes.includes('business=Some(8101)'))
    return '8101：本条发送被按业务拒绝处理。核对该段文案/链接及会话，不等于整个账号被风控，也不要只凭 HTTP 200 判断成功。';
  if (f.classification === 'LoginExpired')
    return '当时登录已失效，请核对该账号登录状态；历史记录不代表现在仍未恢复。';
  if (f.classification === 'Uncertain' || f.classification === 'Inconclusive')
    return '结果待核验，不应自动重发，先查询该条消息的回执。';
  if (message === 'native send was not delivered')
    return '这条发送未取得成功回执；先核对技术原文及同批其他分段，不直接判整个账号离线。';
  if (message === 'receive reconciliation failed')
    return f.reason === 'inbound_spool_full'
      ? '入向缓冲已满，可能延迟处理；检查本机待处理任务与存储。此事件不代表实时接收连接一定断开。'
      : f.reason
        ? '消息补拉失败，查看原因码及附近授权/网络事件；不等于实时接收也全部中断。'
        : '旧日志未上报具体失败原因，需新版客户端日志补充；不要仅据此判断账号掉线。';
  if (message.includes('history read repair'))
    return '本次未取到最新历史消息，暂用缓存；不表示历史数据丢失，核对接收和登录状态。';
  if (
    message.includes('hosted authorization') ||
    message.includes('license renewal')
  )
    return '托管服务授权暂不可用，与抖音登录失效不同。核对后续续签、网络及最新租约状态，历史告警不代表持续中断。';
  if (message.includes('lease release'))
    return '释放请求未获得确认，系统等待租约过期；不要据此推断已出现重复发送。';
  if (message === 'encrypted chat image unavailable')
    return '图片获取或解密失败，可能影响图片显示；不等于自动回复全部停止。';
  if (message === 'quick-auth session failed')
    return String(f.error || '').includes('登录窗口已关闭')
      ? '登录窗口已关闭，本次登录未完成；若是主动关闭可忽略，不据此判断组件损坏。'
      : '检查退出原因、系统兼容性与内置浏览器状态，保留登录资料后再考虑修复组件。';
  return '';
}
const fieldNames: Record<string, string> = {
  account: '匿名账号',
  trace_id: '回复关联号',
  trigger_type: '触发类型',
  message_source: '发送来源',
  segment_index: '第几条',
  segment_count: '本批总条数',
  content_kind: '内容类型',
  content_chars: '内容字数',
  enabled: '当前配置',
  previous_enabled: '变更前配置',
  change_source: '配置来源',
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
  recheck_minutes: '建议复查间隔（分钟）',
  count: '数量',
  version: '版本',
  body: '脱敏响应摘要',
  host: '服务域名',
  source: '来源',
  members: '数量',
};
const values: Record<string, string> = {
  send: '发送消息',
  mutual_follow: '回关/互关通知',
  message: '普通入向消息',
  automatic: '自动回复',
  manual: '手动发送',
  text: '文字',
  link: '链接（平台是否渲染为卡片待核对）',
  account_settings: '账号设置',
  emergency_stop: '管理急停',
  configuration: '配置变更',
  inbound_spool_full: '入向消息缓冲已满',
  inbound_reconcile_failed: '入向消息补拉失败',
  DeliveredSoft: '已取得成功回执（含业务提示）',
  LoginExpired: '登录已失效',
  ProtocolRejected: '请求被协议层拒绝',
  Inconclusive: '响应不足以确认结果',
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
      .filter(([key]) => key !== 'trace_id')
      .map(([key, value]) => {
        let label = values[String(value)] || String(value ?? '—');
        if (
          (key === 'enabled' || key === 'previous_enabled') &&
          typeof value === 'boolean'
        )
          label = value ? '启用' : '关闭';
        if (typeof value === 'object' && value !== null)
          label = JSON.stringify(value);
        if (key === 'response_codes') {
          const raw = String(value);
          const http = raw.match(/http=(\d+)/)?.[1];
          const business = raw.match(/business=Some\((-?\d+)\)/)?.[1];
          const check = raw.match(/raw_check=Some\((-?\d+)\)/)?.[1];
          label =
            [
              http && `HTTP ${http}`,
              business && `业务码 ${business}`,
              check && `检查码 ${check}`,
            ]
              .filter(Boolean)
              .join(' / ') || '详见技术原文';
        }
        return `${fieldNames[key] || key}：${label}`;
      })
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
