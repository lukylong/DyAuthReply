// Pins the quick-auth browser component (Chrome for Testing) to an exact version and
// archive SHA-256 computed from Google's origin. The client downloads from the China
// mirror first and trusts only these digests, so a mirror can speed up but never alter it.
//
// Usage:
//   node scripts/client/pin_quick_auth_component.mjs              # pin current Stable
//   node scripts/client/pin_quick_auth_component.mjs --check      # re-download and re-verify the pin
//   node scripts/client/pin_quick_auth_component.mjs --available  # HEAD both sources (release preflight)
//   node scripts/client/pin_quick_auth_component.mjs --version 155.0.8059.79
//   node scripts/client/pin_quick_auth_component.mjs --windows-compat-version 152.0.7977.169
//   node scripts/client/pin_quick_auth_component.mjs --check --profile windows_compat
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

// Standard and Windows compatibility pins are independent: updating one must
// not silently upgrade/downgrade the other platforms.
const configured = JSON.parse(readFileSync(pinPath, 'utf8'));
const profile = arg('--profile');
if (profile && !['standard', 'windows_compat'].includes(profile)) throw new Error(`invalid profile ${profile}`);
const check = process.argv.includes('--check');
const available = process.argv.includes('--available');
if (check || available) {
  const sets = [
    { name: 'standard', pin: configured, platforms: PLATFORMS },
    { name: 'windows_compat', pin: configured.windows_compat, platforms: ['win64'] },
  ].filter(set => !profile || set.name === profile);
  for (const { name, pin, platforms } of sets) {
    if (!pin || !/^\d+(\.\d+){1,3}$/.test(pin.version) || !Array.isArray(pin.sources) || !pin.sources.length) {
      throw new Error(`invalid ${name} component pin`);
    }
    for (const platform of platforms) {
      const expected = pin.platforms?.[platform];
      if (!expected || !/^[a-f0-9]{64}$/i.test(expected.sha256) || !Number.isSafeInteger(expected.size) || expected.size < 1024 * 1024) {
        throw new Error(`invalid ${name}/${platform} archive pin`);
      }
      const urls = pin.sources.map(t => expand(t, pin.version, platform));
      if (urls.at(-1) !== expand(ORIGIN, pin.version, platform)) throw new Error(`${name} must retain the official origin`);
      for (const url of urls) {
        if (check) {
          const fresh = await digest(url);
          if (fresh.sha256 !== expected.sha256 || fresh.size !== expected.size) throw new Error(`${name}/${platform}: archive differs from pin`);
        } else {
          const response = await fetch(url, { method: 'HEAD', redirect: 'error', signal: AbortSignal.timeout(30000) });
          const size = Number(response.headers.get('content-length'));
          if (!response.ok || size !== expected.size) throw new Error(`${url} unavailable or size differs (HTTP ${response.status}, ${size} bytes)`);
        }
      }
    }
  }
  console.log(JSON.stringify({ result: check ? 'QUICK_AUTH_COMPONENT_PIN_PASS' : 'QUICK_AUTH_COMPONENT_SOURCES_PASS',
    profiles: Object.fromEntries(sets.map(set => [set.name, set.pin.version])) }));
  process.exit(0);
}

const compatibilityVersion = arg('--windows-compat-version');
if (compatibilityVersion) {
  if (!/^\d+(\.\d+){1,3}$/.test(compatibilityVersion)) throw new Error(`invalid version ${compatibilityVersion}`);
  const templates = configured.windows_compat?.sources || [MIRROR, ORIGIN];
  const official = await digest(expand(ORIGIN, compatibilityVersion, 'win64'));
  for (const url of templates.map(t => expand(t, compatibilityVersion, 'win64'))) {
    if (url === expand(ORIGIN, compatibilityVersion, 'win64')) continue;
    const mirrored = await digest(url);
    if (mirrored.sha256 !== official.sha256 || mirrored.size !== official.size) throw new Error('compatibility mirror differs from official origin');
  }
  configured.windows_compat = { version: compatibilityVersion, sources: templates, platforms: { win64: official } };
  writeFileSync(pinPath, `${JSON.stringify(configured, null, 2)}\n`);
  console.log(JSON.stringify({ result: 'QUICK_AUTH_COMPAT_COMPONENT_PINNED', version: compatibilityVersion }));
  process.exit(0);
}

let version = arg('--version');
if (!version) {
  const catalog = await (await fetch(CATALOG)).json();
  version = catalog.channels.Stable.version;
}
if (!/^\d+(\.\d+){1,3}$/.test(version)) throw new Error(`invalid version ${version}`);
const platforms = {};
for (const platform of PLATFORMS) platforms[platform] = await pinPlatform(version, platform);
writeFileSync(pinPath, `${JSON.stringify({ ...configured, version, sources: configured.sources || [MIRROR, ORIGIN], platforms }, null, 2)}\n`);
console.log(JSON.stringify({ result: 'QUICK_AUTH_COMPONENT_PINNED', version }));
