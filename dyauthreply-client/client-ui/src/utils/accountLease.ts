/** Lease ownership and platform login are independent facts. */
export function hasUnavailableLease(account: { runtime_state?: { ownership?: string } }): boolean {
  return account.runtime_state?.ownership === 'lost' || account.runtime_state?.ownership === 'expired';
}
