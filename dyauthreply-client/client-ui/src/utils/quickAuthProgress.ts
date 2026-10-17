export function acceptsQuickAuthResponse(
  requestId: string,
  currentId: string | undefined,
  requestSequence: number,
  currentSequence: number,
  busy: boolean,
): boolean {
  return !busy && requestId === currentId && requestSequence === currentSequence;
}

export function freshQuickAuthProgress(session: { progress_fresh?: boolean } | null): boolean {
  // Old agents do not expose freshness; preserve their UI contract.
  return !!session && session.progress_fresh !== false;
}
