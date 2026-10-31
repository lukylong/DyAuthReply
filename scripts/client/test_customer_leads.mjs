// Local browser integration against the ignored Rust browser_fixture server.
// Set PLAYWRIGHT_MODULE to an installed Playwright module, LEAD_UI_URL (default
// localhost:5173), and optionally CHROMIUM_EXECUTABLE / LEAD_EVIDENCE_DIR.
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const evidence = resolve(process.env.LEAD_EVIDENCE_DIR || '.local/customer-leads-20261031');
mkdirSync(evidence, { recursive: true });
const base = process.env.LEAD_UI_URL || 'http://127.0.0.1:5173';
const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_EXECUTABLE || undefined });
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, permissions: ['clipboard-read', 'clipboard-write'] });
const page = await context.newPage();
const errors = [];
page.on('pageerror', e => errors.push(e.message));
await page.route('**/api/client/v1/**', async route => {
  const path = new URL(route.request().url()).pathname;
  if (path.includes('/douyin/leads') || path.endsWith('/account/all') || path.endsWith('/conversations') || (/\/conversation\//.test(path) && !path.endsWith('/refresh-user'))) return route.continue();
  let body = {};
  if (path.includes('/license')) body = { state: 'active', state_label: '测试授权', can_use_business: true };
  if (path.includes('settings')) body = { version_update: { enabled: false }, runtime: { auto_start: false } };
  if (path.includes('version') || path.includes('update')) body = { has_update: false };
  if (path.endsWith('/refresh-user')) body = { success: false };
  await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
});
const checks = [];
const check = label => { checks.push(label); console.log(`PASS ${label}`); };
const contains = async text => { await page.getByText(text, { exact: true }).first().waitFor(); };
const waitRows = async count => {
  await page.waitForFunction(n => document.querySelectorAll('tbody tr .phone').length === n, count);
};
try {
  await page.goto(`${base}/#/leads`);
  await contains('13800138000'); await contains('13900139000'); await waitRows(2);
  assert.match(await page.locator('tbody tr').filter({ hasText: '13800138000' }).locator('.remark').textContent(), /测试消息：请联系/);
  check('live extraction displays two leads and prefix remarks from Rust SQLite, old message excluded');
  await page.screenshot({ path: resolve(evidence, 'leads-desktop.png'), fullPage: true });
  const row = page.locator('tbody tr').filter({ hasText: '13800138000' });
  await row.getByRole('button', { name: '来源', exact: true }).click();
  await contains('测试消息：请联系 13800138000');
  await page.getByRole('button', { name: '关闭', exact: true }).click();
  await row.getByRole('button', { name: '复制', exact: true }).click();
  assert.equal(await page.evaluate(() => navigator.clipboard.readText()), '13800138000');
  check('source modal and phone clipboard');
  await page.getByLabel('接收账号', { exact: true }).selectOption('fixture-a');
  await page.getByRole('button', { name: '查询', exact: true }).click(); await waitRows(1);
  const downloadPromise = page.waitForEvent('download');
  await page.getByRole('button', { name: '导出筛选结果', exact: true }).click();
  const download = await downloadPromise; const csvPath = resolve(evidence, 'leads-export.csv'); await download.saveAs(csvPath);
  const csv = readFileSync(csvPath, 'utf8'); assert.match(csv, /手机号,备注/); assert.match(csv, /测试消息：请联系/); assert.match(csv, /13800138000/); assert.doesNotMatch(csv, /13900139000/);
  check('account filter and complete filtered CSV export');
  await page.getByLabel('搜索', { exact: true }).fill('no-such-customer');
  await page.getByRole('button', { name: '查询', exact: true }).click(); await waitRows(0); await contains('暂无匹配的留资记录');
  await page.getByLabel('搜索', { exact: true }).fill('dy_demo_a');
  await page.getByRole('button', { name: '查询', exact: true }).click(); await waitRows(1);
  check('empty state and Douyin handle search');
  await page.getByRole('button', { name: '暂停采集', exact: true }).click(); await contains('已暂停');
  await page.reload(); await contains('已暂停');
  await page.getByRole('button', { name: '启用采集', exact: true }).click(); await contains('采集中');
  check('collection toggle persists across reload');
  await page.getByRole('button', { name: '扫描本地历史', exact: true }).click();
  await page.getByRole('button', { name: '开始扫描', exact: true }).click(); await contains('13700137000'); await waitRows(3);
  check('explicit local history scan adds old message without auto-reply');
  await page.locator('tbody tr').filter({ hasText: '13700137000' }).getByRole('button', { name: '删除', exact: true }).click();
  await page.getByRole('button', { name: '确认删除', exact: true }).click(); await waitRows(2);
  await page.getByRole('button', { name: '扫描本地历史', exact: true }).click(); await page.getByRole('button', { name: '开始扫描', exact: true }).click();
  await page.waitForFunction(() => document.querySelector('[data-testid="lead-pending"]')?.textContent === '0');
  await waitRows(2);
  check('delete and rescan do not resurrect retained historical sources');
  await page.setViewportSize({ width: 1100, height: 800 });
  await page.screenshot({ path: resolve(evidence, 'leads-compact.png'), fullPage: true });
  await page.locator('tbody tr').filter({ hasText: '13800138000' }).getByRole('button', { name: '会话', exact: true }).click();
  await page.waitForURL('**/#/chat?**'); await contains('测试消息：请联系 13800138000');
  assert.match(page.url(), /account=fixture-a/);
  check('lead opens exact account and conversation beyond the first 50 chats');
  assert.deepEqual(errors, []);
  check('no unhandled browser errors');
  writeFileSync(resolve(evidence, 'BROWSER.json'), JSON.stringify({ checks, pageErrors: errors, result: 'PASS', fixture: 'local Rust API/SQLite; license/settings mocked, no real Douyin accounts' }, null, 2));
} catch (error) {
  await page.screenshot({ path: resolve(evidence, 'leads-failure.png'), fullPage: true });
  throw error;
} finally { await browser.close(); }
