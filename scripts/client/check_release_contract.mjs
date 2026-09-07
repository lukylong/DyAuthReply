// Source/build release gate. Installed applications contain none of this Node tooling.
import { createHash } from 'node:crypto';
import { existsSync, readFileSync, statSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '../..');
const arg = (name) => {
  const index = process.argv.indexOf(name);
  return index < 0 ? '' : process.argv[index + 1] || '';
};
const text = (path) => readFileSync(join(root, path), 'utf8');
const json = (path) => JSON.parse(text(path));
const match = (path, pattern, label) => {
  const value = text(path).match(pattern)?.[1];
  if (!value) throw new Error(`Missing ${label} in ${path}`);
  return value;
};

const tauri = json('dyauthreply-client/desktop/src-tauri/tauri.conf.json');
const versions = new Map([
  ['client package', json('dyauthreply-client/package.json').version],
  ['desktop package', json('dyauthreply-client/desktop/package.json').version],
  ['Tauri config', tauri.version],
  ['desktop Cargo', match('dyauthreply-client/desktop/src-tauri/Cargo.toml', /^version\s*=\s*"([^"]+)"/m, 'package version')],
  ['desktop Cargo.lock', match('dyauthreply-client/desktop/src-tauri/Cargo.lock', /name = "dyauthreply"\nversion = "([^"]+)"/, 'lock version')],
  ['server default', match('backend-django/application/settings.py', /DOWNLOAD_LATEST_VERSION\s*=.*?or\s*'([^']+)'/, 'download version')],
]);
const unique = new Set(versions.values());
if (unique.size !== 1) {
  throw new Error(`Release versions differ: ${JSON.stringify(Object.fromEntries(versions))}`);
}
const version = [...unique][0];
if (!/^\d+\.\d+\.\d+$/.test(version)) throw new Error(`Invalid release version ${version}`);
const tag = arg('--tag');
if (tag && tag !== `client-v${version}`) {
  throw new Error(`Tag ${tag} does not match client-v${version}`);
}

const extensionVersion = json('browser-extension/douyin-cred-extractor/manifest.json').version;
const serverExtensionVersion = match(
  'backend-django/application/settings.py',
  /DOWNLOAD_EXTENSION_VERSION\s*=.*?or\s*'([^']+)'/,
  'browser extension version',
);
if (extensionVersion !== serverExtensionVersion) {
  throw new Error(
    `Browser extension versions differ: manifest=${extensionVersion} server=${serverExtensionVersion}`,
  );
}

if (tauri.productName !== 'D助手' || tauri.identifier !== 'com.dyauthreply.client') {
  throw new Error('Stable overwrite identity changed; existing installations would fork');
}
const bundle = tauri.bundle || {};
if (JSON.stringify(bundle.externalBin) !== JSON.stringify(['binaries/dy-agent'])) {
  throw new Error('Bundle must contain only the native dy-agent sidecar');
}
if (bundle.createUpdaterArtifacts !== true) throw new Error('Updater artifacts are disabled');
if (bundle.windows?.nsis?.installerHooks !== 'installer-hooks.nsh') {
  throw new Error('Windows transition hook is not configured');
}
for (const forbidden of ['launcher', 'python', 'node']) {
  if (JSON.stringify(bundle.externalBin).toLowerCase().includes(forbidden)) {
    throw new Error(`Legacy runtime is still bundled: ${forbidden}`);
  }
}
const hooks = text('dyauthreply-client/desktop/src-tauri/installer-hooks.nsh');
const retirement = text('dyauthreply-client/desktop/src-tauri/retire-client.ps1');
const nativeHost = text('dyauthreply-client/desktop/src-tauri/src/native_host.rs');
const engineGate = text('dyauthreply-client/agent/src/engine_gate.rs');
for (const required of ['NSIS_HOOK_PREINSTALL', 'retire-client.ps1', 'Abort']) {
  if (!hooks.includes(required)) throw new Error(`Installer hook lost ${required}`);
}
for (const required of ['$InstallDir', 'CreationDate', 'launcher.exe', 'dy-agent.exe', 'AddSeconds(75)']) {
  if (!retirement.includes(required)) throw new Error(`Retirement script lost ${required}`);
}
const retirementCode = retirement.split(/\r?\n/).filter((line) => !line.trimStart().startsWith('#')).join('\n');
if (/taskkill|Get-Process\s+-Name/i.test(retirementCode)) {
  throw new Error('Retirement must use installation-scoped paths, not global process names');
}
for (const required of ['Library/Application Support/DyAuthReply', 'join("DyAuthReply")']) {
  if (!nativeHost.includes(required)) throw new Error(`Stable data root lost ${required}`);
}
if (!engineGate.includes('EngineTransition') || !engineGate.includes('launcher.lock')) {
  throw new Error('Pre-migration cross-engine exclusion is missing');
}

const target = arg('--target');
if (target) {
  if (!/^[a-z0-9_-]+$/.test(target)) throw new Error('Invalid release target');
  const extension = target.includes('windows') ? '.exe' : '';
  const binary = join(root, 'dyauthreply-client/desktop/src-tauri/binaries', `dy-agent-${target}${extension}`);
  const manifestPath = join(root, 'dyauthreply-client/desktop/src-tauri/binaries/native-manifest.json');
  if (!existsSync(binary) || !existsSync(manifestPath)) throw new Error('Native sidecar build output is missing');
  const manifest = JSON.parse(readFileSync(manifestPath));
  const profile = arg('--profile') || 'release';
  if (!['debug', 'release'].includes(profile)) throw new Error('Invalid expected build profile');
  const sha256 = createHash('sha256').update(readFileSync(binary)).digest('hex');
  if (manifest.runtime !== 'rust' || manifest.profile !== profile || manifest.target !== target
      || manifest.app_version !== version || manifest.bytes !== statSync(binary).size
      || manifest.sha256 !== sha256) {
    throw new Error('Native sidecar manifest does not match the release target/binary');
  }
}

console.log(JSON.stringify({
  result: 'NATIVE_RELEASE_CONTRACT_PASS',
  version,
  tag: tag || null,
  target: target || null,
  productName: tauri.productName,
  identifier: tauri.identifier,
  dataRoot: 'DyAuthReply',
  runtime: 'rust',
  extensionVersion,
}));
