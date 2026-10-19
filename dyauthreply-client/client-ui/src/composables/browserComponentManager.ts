import { reactive } from 'vue';
import type { QuickAuthComponent, QuickAuthSession } from '../api/client';

export type BrowserAction = 'install' | 'repair' | 'rollback' | 'uninstall';
export interface BrowserComponentApi {
  status(): Promise<QuickAuthComponent>;
  current(): Promise<QuickAuthSession | null>;
  install(): Promise<QuickAuthComponent>;
  repair(): Promise<QuickAuthComponent>;
  rollback(): Promise<QuickAuthComponent>;
  uninstall(): Promise<QuickAuthComponent>;
}

const terminal = new Set(['completed', 'cancelled', 'timed_out', 'browser_exited', 'failed']);
export const browserStateLabel: Record<QuickAuthComponent['state'], string> = {
  ready: '文件校验正常', missing: '未安装', incompatible: '版本不兼容',
  corrupt: '文件损坏', installing: '正在下载 / 安装', failed: '安装失败',
};

/** Installation never starts a login window. Stale reads cannot undo a mutation. */
export function createBrowserComponentManager(api: BrowserComponentApi) {
  const state = reactive({
    component: null as QuickAuthComponent | null,
    session: null as QuickAuthSession | null,
    loading: false,
    busy: null as BrowserAction | null,
    error: '',
    notice: '',
    statusKnown: false,
  });
  let disposed = false;
  let sequence = 0;
  let timer: ReturnType<typeof setTimeout> | null = null;
  const activeSession = () => Boolean(state.session && !terminal.has(state.session.state));
  const installed = () => state.component?.managed_installed ??
    Boolean(state.component?.source === 'managed' && state.component.state !== 'missing');
  function stopTimer() {
    if (timer !== null) clearTimeout(timer);
    timer = null;
  }
  function schedule() {
    stopTimer();
    if (!disposed && !state.busy && (state.component?.state === 'installing' || activeSession())) {
      timer = setTimeout(() => void refresh(), 1500);
    }
  }
  function can(action: BrowserAction) {
    if (disposed || !state.statusKnown || state.loading || state.busy || activeSession() || state.component?.state === 'installing') return false;
    if (action === 'uninstall') return installed();
    if (action === 'rollback') return Boolean(state.component?.can_rollback);
    if (action === 'repair') return installed();
    return !installed();
  }
  async function refresh() {
    if (disposed || state.busy) return;
    const read = ++sequence;
    state.loading = true;
    try {
      const [component, session] = await Promise.all([api.status(), api.current()]);
      if (disposed || read !== sequence) return;
      const wasInstalling = state.component?.state === 'installing';
      state.component = component;
      state.session = session;
      state.statusKnown = true;
      state.error = '';
      if (component.state === 'failed') state.notice = '';
      else if (wasInstalling && component.state === 'ready') state.notice = '内置浏览器已安装完成，可返回账号页面重新快捷登录。';
    } catch (error) {
      if (!disposed && read === sequence) {
        state.error = error instanceof Error ? error.message : String(error);
        state.statusKnown = false;
      }
    } finally {
      if (!disposed && read === sequence) {
        state.loading = false;
        schedule();
      }
    }
  }
  async function act(action: BrowserAction) {
    if (!can(action)) return;
    ++sequence;
    stopTimer();
    state.busy = action;
    state.error = '';
    state.notice = '';
    try {
      const component = await api[action]();
      if (disposed) return;
      state.component = component;
      state.notice = action === 'uninstall'
        ? '内置浏览器已卸载；登录资料、账号和回复规则均已保留。'
        : action === 'rollback' ? '已回退到上一份组件，可返回账号页面重新快捷登录。'
        : '已开始下载，安装完成后请返回账号页面重新快捷登录。';
    } catch (error) {
      if (!disposed) state.error = error instanceof Error ? error.message : String(error);
    } finally {
      if (!disposed) {
        state.busy = null;
        schedule();
      }
    }
  }
  function dispose() { disposed = true; ++sequence; stopTimer(); }
  return { state, refresh, act, can, installed, activeSession, dispose };
}
