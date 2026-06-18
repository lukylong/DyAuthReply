import { ref } from 'vue';

import { createLocalAdminSession, setAdminToken } from '../api/client';

export const APP_VERSION = 'v0.1.0';

export function useHiddenAdminEntry(onUnlock: () => void) {
  const unlocking = ref(false);

  async function onHiddenAdminClick(event: MouseEvent) {
    if (!event.altKey || unlocking.value) return;
    unlocking.value = true;
    try {
      const res = await createLocalAdminSession();
      setAdminToken(res.token);
      onUnlock();
    } finally {
      unlocking.value = false;
    }
  }

  return { unlocking, onHiddenAdminClick };
}
