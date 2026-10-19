import type { DouyinAccount } from '../api/client';
import { hasUnavailableLease } from './accountLease';

type AccountState = Partial<Pick<DouyinAccount,
  'status' | 'credential_state' | 'identity_verified' | 'identity_error' |
  'last_probe_error' | 'runtime_state' | 'auto_reply_enabled' | 'runtime_auto_reply_enabled'>>;

/** Presentation only. These labels must never authorize receiving or sending. */
export function accountStatusPresentation(account: AccountState | null | undefined): {
  label: '在线' | '离线' | '受限'; tone: 'success' | 'warning' | 'muted'; description: string;
} {
  const a = account ?? {};
  if (a.credential_state === 'invalid' || a.status === 2) {
    return { label: '离线', tone: 'muted', description: '登录已失效，请使用快捷登录恢复。' };
  }
  if (a.status === 3) return { label: '受限', tone: 'warning', description: '账号已禁用，当前不能参与自动回复。' };
  if (hasUnavailableLease(a)) {
    return { label: '离线', tone: 'muted', description: '客户端托管连接正在恢复，不等于抖音登录已失效。' };
  }
  if (a.status === 0) return { label: '离线', tone: 'muted', description: '账号暂未连接，请查看下面的登录和接收状态。' };
  if (a.credential_state === 'receive_only' || a.runtime_state?.send === 'risk_controlled') {
    return { label: '受限', tone: 'warning', description: '账号的发送功能暂时受限，接收状态请见下方。' };
  }
  if (a.status === 1 && a.identity_verified === true && !a.identity_error) {
    return { label: '在线', tone: 'success', description: '登录和接收正常；在线不代表每条消息都能成功发送。' };
  }
  return { label: '离线', tone: 'muted', description: a.identity_error ? '账号信息校验未通过，正在重试。' : '账号正在等待连接或身份验证。' };
}

export function accountStatusDetails(account: AccountState | null | undefined) {
  const a = account ?? {};
  const invalid = a.credential_state === 'invalid' || a.status === 2;
  const risk = a.runtime_state?.send === 'risk_controlled' ||
    (a.credential_state === 'receive_only' && ['客户端协议发送', '发送封控', '发送风控', 'business=', 'raw_check='].some(marker => a.last_probe_error?.includes(marker)));
  const sending = invalid ? '登录已失效，暂不可发送'
    : risk ? '发送受限，等待恢复'
    : a.credential_state === 'receive_only' ? '发送信息不完整，请更新登录'
    : a.credential_state === 'sendable' ? '上次发送成功，后续发送以实际回执为准'
    : '发送待验证，尚未取得实际发送成功回执';
  const receiving: Record<string, string> = {
    ws_healthy: '实时接收正常', http_degraded: '正在使用备用方式接收',
    connecting: '正在连接', backoff: '连接异常，正在重试', disconnected: '未连接',
  };
  return [
    { label: '登录状态', value: invalid ? '已失效，请重新登录' : a.identity_error ? '账号信息校验异常' : a.identity_verified === true ? '已验证' : '等待验证' },
    { label: '消息接收', value: invalid ? '登录失效，接收已暂停' : hasUnavailableLease(a) ? '托管连接恢复中' : a.status === 3 ? '账号已禁用' : receiving[a.runtime_state?.inbound ?? ''] ?? (a.status === 1 ? '接收正常' : '未连接') },
    { label: '消息发送', value: sending },
    { label: '自动回复', value: a.auto_reply_enabled ? (a.runtime_auto_reply_enabled ? '正在运行' : '已开启，等待就绪') : '已关闭' },
    { label: '客户端托管', value: hasUnavailableLease(a) ? '连接恢复中，与抖音登录状态分开判断' : a.status === 3 ? '账号已禁用' : a.runtime_state?.ownership === 'owned' ? '托管正常' : '等待托管就绪' },
  ];
}

export function accountStatusTechnicalInfo(account: AccountState | null | undefined): string {
  return [...new Set([account?.last_probe_error, account?.identity_error].filter(Boolean))].join('\n');
}
