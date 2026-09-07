import type { DouyinAccount } from '../api/client';

/** Presence/receive health is not evidence that automatic sending is enabled. */
export function automaticSummary(
  accounts: ReadonlyArray<Pick<DouyinAccount, 'auto_reply_enabled' | 'runtime_auto_reply_enabled'>>,
  licensed: boolean,
) {
  const enabled = accounts.filter((a) => a.auto_reply_enabled === true).length;
  const running = licensed
    ? accounts.filter((a) => a.auto_reply_enabled === true && a.runtime_auto_reply_enabled === true).length
    : 0;
  const title = accounts.length === 0 ? '尚未托管任何抖音号'
    : !licensed || enabled === 0 ? '自动回复已暂停'
    : running > 0 ? '自动回复正在运行' : '自动回复等待就绪';
  return { enabled, running, title };
}
