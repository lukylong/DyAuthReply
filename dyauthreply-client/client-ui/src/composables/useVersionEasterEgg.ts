import { ref } from 'vue';

const UNLOCK_CLICKS = 10;
const RESET_MS = 2500;

/** 版本号连点解锁（开发者隐藏入口） */
export function useVersionEasterEgg(onUnlock: () => void) {
  const clickCount = ref(0);
  let resetTimer: ReturnType<typeof setTimeout> | null = null;

  function onVersionClick() {
    if (resetTimer) clearTimeout(resetTimer);
    clickCount.value += 1;
    if (clickCount.value >= UNLOCK_CLICKS) {
      clickCount.value = 0;
      onUnlock();
      return;
    }
    resetTimer = setTimeout(() => {
      clickCount.value = 0;
      resetTimer = null;
    }, RESET_MS);
  }

  return { clickCount, onVersionClick };
}

export const APP_VERSION = 'v0.1.0';
