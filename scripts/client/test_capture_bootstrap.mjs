import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const source=readFileSync(process.env.DYCAPTURE_NATIVE_SOURCE || new URL('../../dyauthreply-client/agent/src/quick_auth/cdp.rs',import.meta.url),'utf8');
const popup=readFileSync(new URL('../../browser-extension/douyin-cred-extractor/popup.js',import.meta.url),'utf8');
const popupFunction=popup.slice(popup.indexOf('function navigateToRecommendation()'),popup.indexOf('async function signingPageInfo('));
const script=source.match(/const OPEN_RECOMMENDATION: &str = r"([\s\S]*?)";/)?.[1];
const handler=source.match(/const CLICK_RECOMMENDATION: &str = r"([\s\S]*?)";/)?.[1];
assert(script);assert(handler);
function run(path,links,{origin='https://www.douyin.com',blocked=false,ready='complete',native=true,useHandler=false,visibility='visible',rect={left:20,top:40,width:100,height:30}}={}){
 let clicks=0,hit=null;
 const anchors=links.map(([href,visible=true])=>{
  const a={href,textContent:'推荐',getClientRects:()=>visible?[{}]:[],getBoundingClientRect(){hit=a;return rect;},contains:other=>other===a,click(){clicks++;}};return a;
 });
 const context={URL,location:{origin,pathname:path,href:origin+path},innerWidth:1200,innerHeight:800,
  document:{readyState:ready,visibilityState:visibility,querySelectorAll:()=>anchors,elementFromPoint:()=>blocked?{}:hit}};
 const result=vm.runInNewContext(useHandler?handler:native?script:popupFunction+';navigateToRecommendation()',context);
 return {result:JSON.parse(JSON.stringify(result)),clicks};
}
test('native bootstrap resolves the observed visible link without JS click',()=>{
 assert.deepEqual(run('/jingxuan',[
  ['https://foreign.example/?recommend=1'],['/user/self'],['/?recommend=1',false],['/?recommend=1&from_nav=1'],
 ]),{result:{status:'target',x:70,y:55},clicks:0});
});
test('native bootstrap never clicks a loading, covered or out-of-viewport link',()=>{
 for(const options of [{blocked:true},{ready:'loading'},{rect:{left:-200,top:0,width:10,height:20}},{rect:{left:0,top:900,width:10,height:20}}]){
  assert.deepEqual(run('/jingxuan',[['/?recommend=1']],options),{result:{status:'not_ready'},clicks:0});
 }
});
test('native non-main origins and actual recommendation never trigger unrelated actions',()=>{
 assert.deepEqual(run('/jingxuan',[]),{result:{status:'not_ready'},clicks:0});
 assert.deepEqual(run('/jingxuan',[['/?recommend=1']],{origin:'https://passport.douyin.com'}),{result:{status:'wrong_origin'},clicks:0});
 assert.deepEqual(run('/',[['/?recommend=1']]),{result:{status:'already'},clicks:0});
});
test('extension bootstrap continues to select only the real visible same-origin link',()=>{
 assert.deepEqual(run('/jingxuan',[['https://foreign.example/?recommend=1'],['/?recommend=1',false],['/?recommend=1&from_nav=1']],{native:false}),{result:'clicked',clicks:1});
 assert.deepEqual(run('/jingxuan',[],{native:false}),{result:'not_ready',clicks:0});
 assert.deepEqual(run('/',[['/?recommend=1']],{native:false}),{result:'already',clicks:0});
});

test('interactive document with visible unobstructed navigation does not wait for media load',()=>{
 assert.deepEqual(run('/jingxuan',[['/?recommend=1']],{ready:'interactive'}),{result:{status:'target',x:70,y:55},clicks:0});
});

test('native fallback uses the site link handler when the link is covered, hidden or loading',()=>{
 for(const options of [{blocked:true},{visibility:'hidden'},{ready:'loading'}]){
  assert.deepEqual(run('/jingxuan',[['https://foreign.example/?recommend=1'],['/user/self'],['/?recommend=1&from_nav=1']],{...options,useHandler:true}),{result:{status:'clicked'},clicks:1});
 }
 assert.deepEqual(run('/jingxuan',[['https://foreign.example/?recommend=1']],{useHandler:true}),{result:{status:'not_ready'},clicks:0});
 assert.deepEqual(run('/',[['/?recommend=1']],{useHandler:true}),{result:{status:'already'},clicks:0});
 assert.deepEqual(run('/jingxuan',[['/?recommend=1']],{origin:'https://passport.douyin.com',useHandler:true}),{result:{status:'wrong_origin'},clicks:0});
});
