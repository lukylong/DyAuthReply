// Pins the quick-auth browser component (Chrome for Testing) to an exact version and
// archive SHA-256 computed from Google's origin. The client downloads from the China
// mirror first and trusts only these digests, so a mirror can speed up but never alter it.
//
// Usage:
//   node scripts/client/pin_quick_auth_component.mjs              # pin current Stable
//   node scripts/client/pin_quick_auth_component.mjs --check      # re-download and re-verify the pin
//   node scripts/client/pin_quick_auth_component.mjs --available  # HEAD both sources (release preflight)
//   node scripts/client/pin_quick_auth_component.mjs --version 155.0.8059.79
import { createHash } from 'node:crypto';
import { readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '../..');
const pinPath = join(root, 'dyauthreply-client/agent/quick-auth-component.json');
const CATALOG = 'https://googlechromelabs.github.io/chrome-for-testing/last-known-good-versions.json';
const PLATFORMS = ['mac-arm64', 'mac-x64', 'win64'];
const MIRROR = 'https://cdn.npmmirror.com/binaries/chrome-for-testing/{version}/{platform}/chrome-{platform}.zip';
const ORIGIN = 'https://storage.googleapis.com/chrome-for-testing-public/{version}/{platform}/chrome-{platform}.zip';
const expand = (template, version, platform) =>
  template.replaceAll('{version}', version).replaceAll('{platform}', platform);
// Pinning always hashes Google's origin and requires the public mirror to be identical.
export const sources = (version, platform) => [expand(MIRROR, version, platform), expand(ORIGIN, version, platform)];
const arg = (name) => {
  const index = process.argv.indexOf(name);
  return index < 0 ? '' : process.argv[index + 1] || '';
};

async function digest(url) {
  const response = await fetch(url, { redirect: 'error' });
  if (!response.ok) throw new Error(`${url} -> HTTP ${response.status}`);
  const hash = createHash('sha256');
  let size = 0;
  for await (const chunk of response.body) {
    hash.update(chunk);
    size += chunk.length;
  }
  return { sha256: hash.digest('hex'), size };
}

async function pinPlatform(version, platform) {
  const [mirror, origin] = sources(version, platform);
  const [fromOrigin, fromMirror] = await Promise.all([digest(origin), digest(mirror)]);
  if (fromOrigin.sha256 !== fromMirror.sha256 || fromOrigin.size !== fromMirror.size) {
    throw new Error(`${platform}: mirror content differs from Google origin`);
  }
  console.error(`${platform}: ${fromOrigin.size} bytes sha256=${fromOrigin.sha256} (mirror identical)`);
  return fromOrigin;
}

const check = process.argv.includes('--check');
if (process.argv.includes('--available')) {
  const pinned = JSON.parse(readFileSync(pinPath, 'utf8'));
  for (const platform of PLATFORMS) {
    for (const url of pinned.sources.map((t) => expand(t, pinned.version, platform))) {
      const response = await fetch(url, { method: 'HEAD', redirect: 'error' });
      const size = Number(response.headers.get('content-length'));
      if (!response.ok || size !== pinned.platforms[platform].size) {
        throw new Error(`${url} unavailable or size differs (HTTP ${response.status}, ${size} bytes)`);
      }
    }
  }
  console.log(JSON.stringify({ result: 'QUICK_AUTH_COMPONENT_SOURCES_PASS', version: pinned.version }));
  process.exit(0);
}
let version = arg('--version');
if (check) {
  const pinned = JSON.parse(readFileSync(pinPath, 'utf8'));
  version = pinned.version;
  for (const platform of PLATFORMS) {
    const fresh = await pinPlatform(version, platform);
    const expected = pinned.platforms[platform];
    if (fresh.sha256 !== expected.sha256 || fresh.size !== expected.size) {
      throw new Error(`${platform}: pinned digest no longer matches the published archive`);
    }
  }
  console.log(JSON.stringify({ result: 'QUICK_AUTH_COMPONENT_PIN_PASS', version }));
} else {
  if (!version) {
    const catalog = await (await fetch(CATALOG)).json();
    version = catalog.channels.Stable.version;
  }
  if (!/^\d+(\.\d+){1,3}$/.test(version)) throw new Error(`invalid version ${version}`);
  const platforms = {};
  for (const platform of PLATFORMS) platforms[platform] = await pinPlatform(version, platform);
  // Keep any extra (e.g. self-hosted) sources configured in the existing pin.
  let configured = [MIRROR, ORIGIN];
  try { configured = JSON.parse(readFileSync(pinPath, 'utf8')).sources || configured; } catch {}
  writeFileSync(pinPath, `${JSON.stringify({ version, sources: configured, platforms }, null, 2)}\n`);
  console.log(JSON.stringify({ result: 'QUICK_AUTH_COMPONENT_PINNED', version }));
}
