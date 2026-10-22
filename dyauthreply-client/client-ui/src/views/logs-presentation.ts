// User-facing presentation only; keep stored audit reasons and delivery semantics unchanged.
export function resultTone(result: string): 'ok' | 'bad' | 'warn' | 'muted' {
  if (result === 'success') return 'ok';
  if (result === 'failed') return 'bad';
  if (['partial', 'uncertain', 'pending'].includes(result)) return 'warn';
  return 'muted';
}

export function reasonTitle(result: string): string {
  if (['cooldown', 'quota_exceeded', 'skipped'].includes(result)) return '跳过原因';
  if (['pending', 'silent'].includes(result)) return '等待原因';
  if (result === 'failed') return '失败原因';
  return '结果说明';
}

export function reasonText(reason: string | null | undefined, result: string): string {
  const text = (reason || '').trim();
  if (!text) return '';
  // Also protect cached/legacy responses from exposing raw internal reason codes.
  if (!/[a-z]/i.test(text)) return text;
  const fallback: Record<string, string> = {
    cooldown: '距离上次回复不足规则设置的冷却时间，本次已跳过。冷却结束后可用新消息测试，这条消息不会自动补发。',
    quota_exceeded: '本次回复达到次数限制，已跳过。请检查账号的每日额度及同一联系人回复限制。',
    silent: '当前处于静默时段，暂缓处理。',
    skipped: '本次消息未满足自动回复条件，已跳过。',
    pending: '任务正在等待处理，请稍后查看结果。',
    uncertain: '尚未确认消息是否送达，请勿重复发送。',
    partial: '这批回复只有部分消息取得成功回执，请核对每条消息的发送结果。',
    failed: '本次发送未完成，请查看运行诊断了解具体原因。',
  };
  const message = fallback[result] || '暂未识别具体处理原因，请查看运行诊断。';
  const code = /\bbusiness=(?:Some\()?(\d{1,10})\)?/.exec(text)?.[1];
  return code && code !== '0' ? `${message}（平台状态码：${code}）` : message;
}
