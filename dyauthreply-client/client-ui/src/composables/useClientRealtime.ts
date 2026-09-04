import { readonly, ref } from 'vue';

import {
  DouyinRealtime,
  type RealtimeAccountStateChanged,
  type RealtimeNewMessage,
} from '../api/client';

export interface ClientRealtimeListener {
  onAccountStateChanged?: (data: RealtimeAccountStateChanged) => void;
  onClose?: () => void;
  onNewMessage?: (data: RealtimeNewMessage) => void;
  onOpen?: () => void;
}

const connected = ref(false);
const listeners = new Set<ClientRealtimeListener>();
let transport: DouyinRealtime | null = null;
let accountId = '';
let conversationId = '';

function notify<K extends keyof ClientRealtimeListener>(
  name: K,
  data?: Parameters<NonNullable<ClientRealtimeListener[K]>>[0],
) {
  for (const listener of listeners) {
    const handler = listener[name] as ((value?: unknown) => void) | undefined;
    handler?.(data);
  }
}

function start() {
  if (transport) return;
  transport = new DouyinRealtime({
    onAccountStateChanged: (data) => notify('onAccountStateChanged', data),
    onClose: () => {
      connected.value = false;
      notify('onClose');
    },
    onNewMessage: (data) => notify('onNewMessage', data),
    onOpen: () => {
      connected.value = true;
      transport?.subscribe(accountId, conversationId);
      notify('onOpen');
    },
  });
  transport.connect();
}

function stop() {
  transport?.close();
  transport = null;
  connected.value = false;
}

function subscribe(listener: ClientRealtimeListener): () => void {
  listeners.add(listener);
  start();
  if (connected.value) listener.onOpen?.();
  return () => listeners.delete(listener);
}

function setScope(nextAccountId = '', nextConversationId = '') {
  accountId = nextAccountId;
  conversationId = nextConversationId;
  transport?.subscribe(accountId, conversationId);
}

/** 全客户端共用一条本机 WebSocket：服务在线、账号变更、私信增量统一广播。 */
export function useClientRealtime() {
  return {
    connected: readonly(connected),
    setScope,
    start,
    stop,
    subscribe,
  };
}
