import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const source=readFileSync(new URL('../../dyauthreply-client/client-ui/src/views/LogsView.vue',import.meta.url),'utf8');
test('audit creation time is labelled processing time, not original message time',()=>{
 assert.doesNotMatch(source,/>触发时间</);
 assert.match(source,/<th[^>]*>处理时间<\/th>/);
 assert.match(source,/<dt[^>]*>处理时间<\/dt>/);
});
test('detail explains that delayed historical messages predate the processing timestamp',()=>{
 assert.ok(source.includes('客户端处理该记录的时间，不是消息原始发送时间'));
});
