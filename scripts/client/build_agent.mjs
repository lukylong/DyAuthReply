// Build-only tooling. The installed client contains no Node/Python runtime.
import { spawnSync } from 'node:child_process';
import { readFileSync, copyFileSync, mkdirSync, chmodSync, writeFileSync, statSync, mkdtempSync, rmSync } from 'node:fs';
import { createHash, createPublicKey } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { tmpdir } from 'node:os';
const root=join(dirname(fileURLToPath(import.meta.url)),'../..');
if(process.argv.includes('--prepare-nsis-hook')){
  const target=process.env.DY_AGENT_BUILD_TARGET||'';
  if(target.includes('windows')){
    const arch=target.startsWith('x86_64-')?'x64':target.startsWith('i686-')?'x86':target.startsWith('aarch64-')?'arm64':'';
    if(!arch)throw new Error('Unsupported Windows bundle architecture');
    const staging=join(root,'dyauthreply-client/desktop/src-tauri/target',target,'release','nsis',arch);
    mkdirSync(staging,{recursive:true});
    copyFileSync(join(root,'dyauthreply-client/desktop/src-tauri/retire-client.ps1'),join(staging,'retire-client.ps1'));
    console.log(`NSIS_RETIRE_HOOK_PREPARED=${join(staging,'retire-client.ps1')}`);
  }
  process.exit(0);
}
const arg=(name,fallback)=>{const i=process.argv.indexOf(name);return i<0?fallback:process.argv[i+1];};
const profile=arg('--profile',process.env.DY_AGENT_BUILD_PROFILE||'release');
if(!['debug','release'].includes(profile)) throw new Error('Expected debug or release profile');
const compiler=spawnSync('rustc',['-vV'],{encoding:'utf8'});
if(compiler.status!==0) throw new Error('Rust compiler is required');
const host=compiler.stdout.match(/^host: (.+)$/m)?.[1];
const target=arg('--target',process.env.DY_AGENT_BUILD_TARGET||host);
if(!target||!/^[a-z0-9_-]+$/.test(target)) throw new Error('Invalid Rust target');
const server=process.env.CLIENT_LICENSE_SERVER_URL;
const encodedKey=process.env.LICENSE_LEASE_PUBLIC_KEY_B64;
if(profile==='release'&&(!server||!encodedKey)) throw new Error('Release build requires the pinned authority URL and public verification key');
if(server||encodedKey){
  try {
    const url=new URL(server);const local=['127.0.0.1','localhost'].includes(url.hostname);
    if(url.username||url.password||url.search||url.hash||!(url.protocol==='https:'||(profile==='debug'&&local&&url.protocol==='http:'))) throw new Error();
    const pem=Buffer.from(encodedKey,'base64').toString('utf8');
    if(!pem.includes('BEGIN PUBLIC KEY')||pem.includes('PRIVATE KEY')||createPublicKey(pem).asymmetricKeyType!=='ed25519') throw new Error();
  } catch {throw new Error('Invalid pinned public authority configuration');}
}
const appVersion=JSON.parse(readFileSync(join(root,'dyauthreply-client/desktop/src-tauri/tauri.conf.json'))).version;
const cargo=['build','--locked','--manifest-path',join(root,'dyauthreply-client/agent/Cargo.toml'),'--bin','dy-agent'];
if(profile==='release')cargo.push('--release');
if(target!==host)cargo.push('--target',target);
const build=spawnSync('cargo',cargo,{cwd:root,stdio:'inherit',env:{...process.env,CLIENT_APP_VERSION:appVersion,CARGO_INCREMENTAL:'0',CARGO_PROFILE_DEV_DEBUG:'0',CARGO_PROFILE_TEST_DEBUG:'0'}});
if(build.status!==0)process.exit(build.status||1);
const extension=target.includes('windows')?'.exe':'';
const source=join(root,'dyauthreply-client/agent/target',target!==host?target:'',profile,`dy-agent${extension}`);
const dir=join(root,'dyauthreply-client/desktop/src-tauri/binaries');mkdirSync(dir,{recursive:true});
const destination=join(dir,`dy-agent-${target}${extension}`);
if(target.includes('apple-darwin')){
  const staging=mkdtempSync(join(tmpdir(),'dy-agent-sign-'));
  const signable=join(staging,'dy-agent');
  try {
    copyFileSync(source,signable);chmodSync(signable,0o755);
    const signed=spawnSync('codesign',['--force','--options','runtime','--sign','-',signable],{stdio:'inherit'});
    if(signed.status!==0)process.exit(signed.status||1);
    copyFileSync(signable,destination);
  } finally {rmSync(staging,{recursive:true,force:true});}
}else copyFileSync(source,destination);
chmodSync(destination,0o755);
const manifest={runtime:'rust',target,profile,bytes:statSync(destination).size,sha256:createHash('sha256').update(readFileSync(destination)).digest('hex'),app_version:appVersion};
writeFileSync(join(dir,'native-manifest.json'),JSON.stringify(manifest,null,2)+'\n');console.log(JSON.stringify(manifest));
