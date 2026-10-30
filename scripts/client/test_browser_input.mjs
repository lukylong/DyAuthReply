import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
const root = process.env.TEST_REPO_ROOT ? new URL(`file://${process.env.TEST_REPO_ROOT}/`) : new URL('../../', import.meta.url);
const read = (path) => readFileSync(new URL(path, root), 'utf8');

test('input preparation is advisory while origin validation remains mandatory', () => {
  const source = read('dyauthreply-client/desktop/src-tauri/src/native_commands.rs');
  const block = source.slice(source.indexOf('pub async fn native_request('), source.indexOf('pub async fn native_request_image('));
  assert(block.indexOf('require_local(&window)?') < block.indexOf('needs_english_input'));
  assert(block.includes('require_local(&window)?'));
  assert(block.includes('crate::input_policy::start_after_attempt('));
  assert(block.indexOf('prepare(&window)') < block.indexOf('|| host.request('));
  assert(!block.includes('prepare(&window).await?'));
  const guard = read('dyauthreply-client/desktop/src-tauri/src/browser_input.rs');
  assert(guard.includes('method == "POST" && path == "/api/client/v1/douyin/quick-auth/start"'));
  assert(guard.includes('.run_on_main_thread('));
  assert(guard.includes('windows_input::confirmed(&pending)?'));
  assert(guard.includes('for _ in 0..20'));
  assert(guard.includes('send.is_closed()'));
  assert(guard.includes('from_secs(2), receive'));
  assert(guard.includes('#[cfg(not(target_os = "windows"))]'));
});

test('manual English is respected without exact KLID name validation', () => {
  const source = read('dyauthreply-client/desktop/src-tauri/src/windows_input.rs');
  assert(!source.includes('GetKeyboardLayoutNameW'));
  assert(source.indexOf('if english_keyboard(GetKeyboardLayout(focus_thread))') < source.indexOf('let layout ='));
  assert(!source.includes('fn ImmIsIME('));
  assert(source.includes('english_language(layout as usize)'));
  assert(source.includes('GetForegroundWindow() != hwnd'));
  assert(source.includes('window_thread != GetCurrentThreadId()'));
  assert(source.includes('GetKeyboardLayout(pending.focus_thread)'));
  assert(source.includes('PostMessageW(focus, WM_INPUTLANGCHANGEREQUEST'));
  assert(!source.includes('SystemParametersInfoW'));
  assert(!source.includes('RegSetValue'));
  assert(!source.includes('SendInput'));
});

test('advisory policy always calls start and preserves actual launch failures', () => {
  const source = read('dyauthreply-client/desktop/src-tauri/src/input_policy.rs');
  const policy = source.slice(0, source.indexOf('#[cfg(test)]'));
  assert(policy.includes('if let Err(warning) = prepare.await'));
  assert(policy.includes('warn(warning);'));
  assert(policy.includes('start().await'));
  assert(!policy.includes('prepare.await?'));
});

test('failed preflight can be retried without an existing session and explains input change', () => {
  const source = read('dyauthreply-client/client-ui/src/views/AccountsView.vue');
  assert(source.includes('!quickAuth && quickAuthError && quickComponent?.state === \'ready\''));
  assert(source.includes('英文（美国）键盘'));
});
