import assert from 'node:assert/strict';
import { automaticSummary } from '../../dyauthreply-client/client-ui/src/domain/accountRuntime.ts';
assert.equal(automaticSummary([{auto_reply_enabled:false,runtime_auto_reply_enabled:false}],true).title,'自动回复已暂停');
assert.equal(automaticSummary([{auto_reply_enabled:true,runtime_auto_reply_enabled:false}],true).title,'自动回复等待就绪');
assert.equal(automaticSummary([{auto_reply_enabled:true,runtime_auto_reply_enabled:true}],true).title,'自动回复正在运行');
assert.equal(automaticSummary([{auto_reply_enabled:true,runtime_auto_reply_enabled:true}],false).title,'自动回复已暂停');
assert.equal(automaticSummary([{}],true).running,0);
assert.equal(automaticSummary([],true).title,'尚未托管任何抖音号');
console.log('ACCOUNT_RUNTIME_SUMMARY_6_CASES_PASS');
