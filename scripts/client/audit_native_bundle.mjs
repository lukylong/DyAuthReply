import { readdirSync, statSync, readFileSync } from 'node:fs';
import { join, basename } from 'node:path';
import { createHash } from 'node:crypto';
const root=process.argv[2];if(!root)throw new Error('Expected unpacked native app/bundle path');
const files=[];
function walk(path){for(const name of readdirSync(path)){const full=join(path,name);if(statSync(full).isDirectory())walk(full);else files.push(full);}}
walk(root);
const forbidden=files.filter(p=>/^(python(?:\d(?:\.\d+)*)?(?:\.exe|\.dll)?|python\d+\.dll|launcher(?:-[\w-]+)?(?:\.exe)?|node(?:\.exe)?|libpython.*|base_library\.zip)$/i.test(basename(p))||/[\\/]_MEI[^\\/]*[\\/]|[\\/]Python\.framework[\\/]/.test(p));
if(forbidden.length)throw new Error(`Unexpected legacy runtime files: ${forbidden.join(', ')}`);
const agents=files.filter(p=>/^dy-agent(?:\.exe)?$/.test(basename(p)));if(agents.length!==1)throw new Error('Expected exactly one native service executable');
const manifests=files.filter(p=>basename(p)==='native-manifest.json');if(manifests.length!==1)throw new Error('Expected exactly one native runtime manifest');
const manifest=JSON.parse(readFileSync(manifests[0]));
const sha=createHash('sha256').update(readFileSync(agents[0])).digest('hex');if(sha!==manifest.sha256||manifest.runtime!=='rust')throw new Error('Native runtime manifest mismatch');
console.log(JSON.stringify({result:'NATIVE_BUNDLE_AUDIT_PASS',files:files.length,legacy_runtime_files:forbidden.length,agent_sha256:sha,app_version:manifest.app_version}));
