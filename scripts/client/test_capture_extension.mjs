import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {webcrypto} from 'node:crypto';
const root=new URL('../../browser-extension/douyin-cred-extractor/',import.meta.url);
const core=readFileSync(new URL('capture-core.js',root),'utf8');
const popupSource=readFileSync(process.env.DYCAPTURE_POPUP_PATH || new URL('popup.js',root),'utf8');
const fixture=JSON.parse(readFileSync(new URL('../../dyauthreply-client/agent/tests/fixtures/native_signing.json',import.meta.url),'utf8'));
const ctx=vm.createContext({URL,TextDecoder,Uint8Array,atob,btoa,crypto:webcrypto});vm.runInContext(core,ctx);
const c=ctx.DyCapture, token='S'.repeat(164);
const cookie='sessionid=synthetic; sessionid_ss=synthetic; sid_tt=synthetic; UIFID=browser; s_v_web_id=verify_browser; bd_ticket_guard_tssign=ts.2.synthetic';
const headers=Object.fromEntries(['www.douyin.com','imapi.douyin.com','creator.douyin.com'].map(h=>[h,cookie]));
const keys=JSON.stringify({ec_privateKey:fixture.private_key});
const ticket=JSON.stringify({ticket:'synthetic',ts_sign:'ts.2.synthetic',client_cert:'pub.'+fixture.ree_public});
assert.equal(c.tokenFromUrl('https://www.douyin.com/path?msToken='+token),token);
for(const url of ['http://www.douyin.com','https://evil.test','https://www.douyin.com.evil.test','https://creator.douyin.com'])assert.equal(c.tokenFromUrl(url+'/?msToken='+token),'');
assert.equal(c.missing(headers,token,'synthetic').length,0);
assert(c.missing({...headers,'imapi.douyin.com':cookie+'; sid_tt=other'},token,'synthetic').length);
assert(c.missing(headers,'','synthetic').includes('浏览器 SDK Token'));
assert(await c.selectMaterial([keys],[ticket],cookie));
assert.equal(await c.selectMaterial([keys],[ticket],cookie.replace('ts.2.synthetic','ts.2.foreign')),null);
assert.equal(await c.selectMaterial([keys],[ticket.replace(fixture.ree_public,'other')],cookie),null);
assert(await c.selectMaterial(['{}',keys],['{}',ticket],cookie));
console.log('PASS core: real token scope, completeness, session conflicts, matching key/certificate');

async function popupCase(mode, entry="creator"){
 const elements=new Map();const element=id=>{if(!elements.has(id))elements.set(id,{value:'',hidden:true,classList:{add(){},remove(){},toggle(){}},addEventListener(){}});return elements.get(id);};
 const needsWarmup=['warmup','never_ready','redirected','redirect_bounce_once','redirect_never_changes','redirect_link_missing'].includes(mode);
 let identityRead=false, materialRead=false, pageFailures=0, initialDocumentReads=0, recommendationClicks=0, recommendationLoads=0;
 const timers=new Map(),scheduledDelays=[];let timerId=0;
 const switches=()=>recommendationClicks+recommendationLoads;
 const redirectPending=()=>['redirected','redirect_link_missing'].includes(mode)?switches()===0:mode==='redirect_bounce_once'?switches()<2:mode==='redirect_never_changes';
 let now=100000, removed=[], mainStarted=entry==='www'&&!needsWarmup, recommendationOpened=false;
 let mainId=entry==='www'?1:null;
 const tabs=new Map([[1,{id:1,windowId:1,url:`https://${entry}.douyin.com/`,cookieStoreId:'0'}]]);
 const context=vm.createContext({URL,TextDecoder,Uint8Array,atob,btoa,crypto:webcrypto,console,unescape,encodeURIComponent,
   Date:class extends Date {static now(){now+=1000;return now;}},setTimeout(fn,delay){scheduledDelays.push(delay);const id=++timerId;timers.set(id,true);if(delay<5000)fn();else if(mode.endsWith('_timeout'))setImmediate(()=>{if(timers.get(id))fn();});return id;},clearTimeout(id){timers.delete(id);},
   setInterval:()=>0,clearInterval:()=>{},
   document:{getElementById:element,addEventListener(){}},HTMLElement:class{},navigator:{}});
 if(['tab_metadata_timeout','cookie_store_timeout','cookie_store_unmapped'].includes(mode))delete tabs.get(1).cookieStoreId;
 const chrome={
  runtime:{connect:()=>{
    const listeners=[],disconnects=[],owned=new Set();let closed=false;
    const cleanup=async()=>{for(const id of [...owned]){owned.delete(id);await chrome.tabs.remove(id);}};
    const port={onMessage:{addListener:f=>listeners.push(f)},onDisconnect:{addListener:f=>disconnects.push(f)},
      postMessage:message=>{if(message.op==='ping')return;Promise.resolve().then(async()=>{
        if(message.op==='finish'){await cleanup();return null;}
        const tab=await chrome.tabs.create({windowId:message.windowId,url:message.url,active:false});owned.add(tab.id);return tab;
      }).then(value=>{if(!closed)for(const f of listeners)f({id:message.id,ok:true,value});});},
      disconnect:()=>{closed=true;void cleanup();for(const f of disconnects)f();}};
    return port;
  }},
  tabs:{query:async()=>mode==='tab_query_timeout'?new Promise(()=>{}):[tabs.get(1)],
    create:async options=>{
      const next={id:tabs.size+1,windowId:1,url:options.url,cookieStoreId:'0'};
      if(new URL(options.url).hostname==='www.douyin.com'){
        assert.equal(options.url,'https://www.douyin.com/?recommend=1','UIFID must bootstrap from the selected feed, then switch in-app');
        recommendationOpened=true;mainStarted=true;mainId=next.id;
      }
      tabs.set(next.id,next);return next;
    },
    get:async id=>{if(mode==='tab_metadata_timeout'&&id===1)return new Promise(()=>{});if(mode==='closed_tab'&&id!==1)throw new Error('closed');return {...tabs.get(id),cookieStoreId:['cookie_store_timeout','cookie_store_unmapped'].includes(mode)?undefined:mode==='store_mismatch'&&id===mainId?'other':'0',status:mode==='about_blank'&&initialDocumentReads<2?'complete':'loading',url:mode==='about_blank'&&initialDocumentReads<2?'about:blank':tabs.get(id).url};},remove:async id=>removed.push(id),
    update:async(id,options)=>{assert.equal(id,mainId,'only the owned main tab may be reloaded');assert.equal(options.url,'https://www.douyin.com/jingxuan','never fully load the recommendation feed');recommendationLoads++;return tabs.get(id);}},
  cookies:{getAllCookieStores:async()=>mode==='cookie_store_timeout'?new Promise(()=>{}):[{id:'0',tabIds:mode==='cookie_store_unmapped'?[]:[...tabs.keys()]}],getAll:async({url})=>{
    if(mode==='cookie_permission')throw new Error('synthetic-private-error');
    if(mode==='cookie_timeout')return new Promise(()=>{});
    let raw=needsWarmup&&(!recommendationOpened||mode==='never_ready'||redirectPending())?cookie.replace('UIFID=browser','UIFID_TEMP=temporary'):cookie;
    if((mode==='identity_switch'&&identityRead)||(mode==='final_switch'&&materialRead&&url==='https://www.douyin.com/'))raw=raw.replace(/(sessionid|sessionid_ss|sid_tt)=synthetic/g,'$1=another-session');
    const rows=raw.split('; ').map(v=>{const[n,...rest]=v.split('=');return {name:n,value:rest.join('=')};});
    if(mode==='nameless')rows.push({name:'',value:'marker'});
    if(mode==='malformed_cookie')rows.push({name:'synthetic',value:'private\ninvalid'});
    return rows;
  }},
  scripting:{executeScript:async({func,target})=>{
    if(String(func).includes('media/user/info'))assert(mainStarted,'main-site warmup must start before the remote identity lookup');
    if(String(func).includes('media/user/info')&&mode==='identity_slow')return [{result:await func()}];
    if(String(func).includes("searchParams.get('recommend')")){if(mode==='redirect_link_missing')return [{result:'not_ready'}];recommendationClicks++;return [{result:'clicked'}];}
    let host=new URL(tabs.get(target.tabId).url).hostname;
    if(String(func).includes('media/user/info'))identityRead=true;
    if(String(func).includes('document.readyState')&&mode==='about_blank'&&++initialDocumentReads<2)host='';
    const signing=!String(func).includes('document.readyState')&&!String(func).includes('media/user/info');
    if(signing&&mode==='about_blank'&&initialDocumentReads<2)host='';
    if(signing&&mode==='wrong_origin')host='foreign.example';
    if(signing&&mode==='page_throw_once'&&pageFailures++===0)throw new Error('transient navigation');
    if(signing&&mode==='script_timeout')return new Promise(()=>{});
    return [{result:String(func).includes('document.readyState')?{ready:'interactive',host}:String(func).includes('media/user/info')?
      (mode==='identity'?{error:'not authenticated'}:mode==='identity_timeout'?{error:'timeout'}:{nickname:'SAMPLE',sec_uid:'self'}):
      {origin:host,pathname:redirectPending()&&host==='www.douyin.com'?'/jingxuan':'/',keys:[keys],tickets:host==='creator.douyin.com'?[ticket]:[ticket.replace('ts.2.synthetic','ts.2.stale')],dtrait_blob:mode==='missing'?'':'synthetic-blob',dtrait_path:'/passport/token/beat/web/',ua:'Chrome/152.0',browser_profile:{platform:'MacIntel'}}}];
  }},
  storage:{session:{get:async list=>mode==='stale_source_token'&&mainId===1?{}:Object.fromEntries(list.map(k=>[k,{value:k.endsWith('_token')?token:k.endsWith('_ticket')?ticket:'dtrait',capturedAt:999999,storeId:'0',tabId:mainId,sourceHost:'www.douyin.com'}]))}}
 };
 context.chrome=chrome;vm.runInContext(core,context);
 context.AbortSignal={timeout:ms=>({budgetMs:ms})};
 context.fetch=async(_url,options)=>{
   if(options.signal.budgetMs<12000)throw Object.assign(new Error('synthetic timeout'),{name:'TimeoutError'});
   return {ok:true,json:async()=>({status_code:0,user:{nickname:'SAMPLE',sec_uid:'self',short_id:'123'}})};
 };
 const originalSelect=context.DyCapture.selectMaterial;
 context.DyCapture.selectMaterial=async(...args)=>{const result=await originalSelect(...args);materialRead=true;return result;};
 vm.runInContext(popupSource,context);
 await vm.runInContext(mode==='double_click'?'Promise.all([grab(),grab()])':'grab()',context);
 if(['success','identity_slow','stale_source_token','warmup','redirected','redirect_bounce_once','redirect_link_missing','nameless','about_blank','page_throw_once','double_click'].includes(mode)){
  assert(element('bundle').value.startsWith('DYCRED1.'),element('tabHint').textContent);
  const payload=JSON.parse(Buffer.from(element('bundle').value.slice(8),'base64url'));
  assert.equal(payload.capture_version,2);assert.equal(payload.query_ms_token,token);assert.equal(payload.sec_uid,'self');
  assert.equal(element('result').hidden,false);
 } else {assert.equal(element('bundle').value,'');assert.equal(element('result').hidden,true);assert.equal(element('tabHint').hidden,false);}
 if(needsWarmup)assert(recommendationOpened,'missing UIFID must initialize the recommendation feed');
 if(mode==='redirected')assert.deepEqual([recommendationClicks,recommendationLoads],[1,0]);
 if(mode==='redirect_bounce_once')assert.deepEqual([recommendationClicks,recommendationLoads],[2,0],'a click that bounces back is clicked again');
 if(mode==='redirect_link_missing')assert.deepEqual([recommendationClicks,recommendationLoads],[0,1],'a missing link is retried from a fresh selected document');
 if(mode==='redirect_never_changes'){assert(recommendationClicks>=2&&recommendationLoads>=1,'keep switching automatically until the deadline');assert.doesNotMatch(element('tabHint').textContent,/已尝试3次|手动/);}
 assert.equal(element('grabBtn').disabled,false);
 if(mode==='identity_slow')assert(scheduledDelays.includes(18000),'script deadline must allow the longer identity request to finish');
 if(mode==='identity_timeout')assert.match(element('tabHint').textContent,/资料接口响应超时/);
 if(['tab_query_timeout','tab_metadata_timeout','cookie_store_timeout'].includes(mode))assert.match(element('tabHint').textContent,/超时/);
 if(mode==='cookie_store_unmapped')assert.equal(identityRead,false,'never guess a cookie store when the tab is not mapped');
 assert.deepEqual(removed,[...tabs.keys()].filter(id=>id!==1));
 assert.equal(tabs.get(1).url,`https://${entry}.douyin.com/`,'never navigate or close the user tab');
 console.log('PASS popup: '+mode+' / '+entry+', real UIFID requirement and owned-tab cleanup');
}
await popupCase('success');await popupCase('success','www');await popupCase('stale_source_token','www');await popupCase('warmup','www');await popupCase('never_ready','www');
for(const mode of ['redirected','redirect_bounce_once','redirect_link_missing','redirect_never_changes','missing','identity','nameless','malformed_cookie','cookie_permission','cookie_timeout','script_timeout','identity_switch','final_switch','store_mismatch','wrong_origin','closed_tab','about_blank','page_throw_once','double_click','identity_timeout','tab_query_timeout','tab_metadata_timeout','cookie_store_timeout','cookie_store_unmapped','identity_slow'])await popupCase(mode);
console.log('PASS extension capture regression suite');
const listeners={}, saved={};
const bg=vm.createContext({URL,TextDecoder,Uint8Array,atob,btoa,Date,crypto:webcrypto});
bg.importScripts=()=>vm.runInContext(core,bg);
bg.chrome={tabs:{get:async id=>({id}),onRemoved:{addListener:f=>listeners.removed=f}},cookies:{getAllCookieStores:async()=>[{id:'normal',tabIds:[1]},{id:'private',tabIds:[2]}]},storage:{session:{set:async v=>Object.assign(saved,v),get:async()=>saved,remove:async ks=>ks.forEach(k=>delete saved[k])}},webRequest:Object.fromEntries(['onBeforeRequest','onHeadersReceived','onBeforeSendHeaders'].map(name=>[name,{addListener:f=>listeners[name]=f}]))};
vm.runInContext(readFileSync(new URL('background.js',root),'utf8'),bg);
listeners.onBeforeRequest({tabId:1,url:'https://www.douyin.com/?msToken='+token});
listeners.onBeforeRequest({tabId:2,url:'https://www.douyin.com/?msToken='+token});
listeners.onBeforeRequest({tabId:1,url:'https://evil.test/?msToken='+token});
listeners.onBeforeRequest({tabId:-1,url:'https://www.douyin.com/?msToken='+token});
listeners.onHeadersReceived({tabId:1,url:'https://www.douyin.com/',responseHeaders:[{name:'x-ms-token',value:'bad\nvalue'}]});
await new Promise(r=>setImmediate(r));
assert.equal(Object.keys(saved).length,2);
assert(saved.dy_capture_v2_normal_1_www_douyin_com_token===undefined);
assert.equal(saved['dy_capture_v2_normal_1_www.douyin.com_token'].storeId,'normal');
assert.equal(saved['dy_capture_v2_private_2_www.douyin.com_token'].storeId,'private');
await listeners.removed(1);assert.equal(Object.keys(saved).length,1);
console.log('PASS background: store/tab/origin isolation, invalid headers, tab cleanup');
