import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { createRequire } from 'node:module';
const root=resolve(import.meta.dirname,'../..');
const sourceRoot=process.env.LEADS_SOURCE_ROOT || root;
const source=readFileSync(resolve(sourceRoot,'dyauthreply-client/client-ui/src/views/LeadsView.vue'),'utf8');
const router=readFileSync(resolve(sourceRoot,'dyauthreply-client/client-ui/src/router/index.ts'),'utf8');
const require=createRequire(resolve(root,'dyauthreply-client/client-ui/package.json'));
const {parse}=require('@vue/compiler-sfc');
const {baseParse}=require('@vue/compiler-dom');
const descriptor=parse(source).descriptor;
test('lead route reuses the existing wide workbench shell',()=>{
 const route=router.match(/path: '\/leads',[\s\S]*?\n    },/)[0];
 assert.match(route,/meta: \{ wide: true \}/);
 assert.match(source,/\.leads-page\s*\{[^}]*height:100%[^}]*min-height:0/);
 assert.match(source,/\.table-scroll\s*\{[^}]*flex:1[^}]*min-height:0[^}]*overflow:auto/);
});
test('summary remains inline and complete time/remark content stays accessible',()=>{
 assert.doesNotMatch(descriptor.template.content,/class="summary"/);
 for(const text of ['inline-stats','lead-pending','采集说明','首次','最近','row.first_at','row.last_at','row.remark','showSources(row)','左右滚动查看完整字段'])assert.ok(source.includes(text),text);
 assert.match(source,/thead/);assert.match(source,/th \{[^}]*position:sticky/);
 assert.match(source,/\.pinned-actions \{[^}]*position:sticky[^}]*right:0/);
});
test('non-query actions inside the compact filter form never submit search',()=>{
 const ast=baseParse(descriptor.template.content);const buttons=[];
 function visit(node,inForm=false){if(node.type===1){inForm ||= node.tag==='form';if(inForm&&node.tag==='button')buttons.push(node);for(const child of node.children||[])visit(child,inForm);}else for(const child of node.children||[])visit(child,inForm);}
 visit(ast);assert.equal(buttons.length,3);
 const types=buttons.map(b=>b.props.find(p=>p.type===6&&p.name==='type')?.value?.content);
 assert.deepEqual(types,['submit','button','button']);
});
test('all pre-existing main actions and source views are still wired',()=>{
 for(const handler of ['toggle','scanOpen = true','exportCsv','showSources(row)','copy(row)','openChat(row)','deleteTarget = row','page--; load()','page++; load()'])assert.ok(source.includes(handler),handler);
 assert.match(source,/aria-label="接收账号"/);assert.match(source,/aria-label="搜索"/);
 assert.match(source,/role="region" aria-label="留资表格/);
 assert.match(source,/:global\(\.app-layout:has\(\.leads-page\)\) \{ overflow:clip;/);
});
