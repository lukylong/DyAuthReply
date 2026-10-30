import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
const root = new URL('../../', import.meta.url);
const read = (path) => readFileSync(new URL(path, root), 'utf8');

test('native login preflight is awaited after origin validation and before the Agent request', () => {
  const source = read('dyauthreply-client/desktop/src-tauri/src/native_commands.rs');
  const block = source.slice(source.indexOf('pub async fn native_request('), source.indexOf('pub async fn native_request_image('));
  assert(block.indexOf('require_local(&window)?') < block.indexOf('needs_english_input'));
  assert(block.indexOf('prepare(&window).await?') < block.indexOf('host.request('));
  const guard = read('dyauthreply-client/desktop/src-tauri/src/browser_input.rs');
  assert(guard.includes('method == "POST" && path == "/api/client/v1/douyin/quick-auth/start"'));
  assert(guard.includes('.run_on_main_thread('));
  assert(guard.includes('windows_input::confirmed(&pending)?'));
  assert(guard.includes('for _ in 0..20'));
  assert(guard.includes('#[cfg(not(target_os = "windows"))]'));
});

test('switches exact US keyboard, verifies the focused thread, and never rewrites default language', () => {
  const source = read('dyauthreply-client/desktop/src-tauri/src/windows_input.rs');
  assert(source.includes('name != US_KLID'));
  assert(source.includes('GetForegroundWindow() != hwnd'));
  assert(source.includes('window_thread != GetCurrentThreadId()'));
  assert(source.includes('GetKeyboardLayout(pending.focus_thread)'));
  assert(source.includes('PostMessageW(focus, WM_INPUTLANGCHANGEREQUEST'));
  assert(!source.includes('SystemParametersInfoW'));
  assert(!source.includes('RegSetValue'));
  assert(!source.includes('SendInput'));
});

test('failed preflight can be retried without an existing session and explains input change', () => {
  const source = read('dyauthreply-client/client-ui/src/views/AccountsView.vue');
  assert(source.includes('!quickAuth && quickAuthError && quickComponent?.state === \'ready\''));
  assert(source.includes('英文（美国）键盘'));
});
