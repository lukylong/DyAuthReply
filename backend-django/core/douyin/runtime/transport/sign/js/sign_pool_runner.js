/**
 * 常驻签名进程 runner —— 替代 PyExecJS「每次 call 起一个 node 子进程」的子进程风暴。
 *
 * 启动一次：node sign_pool_runner.js <dy_ab.js 路径>
 *   - 仅在启动时 eval 一次 dy_ab.js（建立 jsdom window / 加载 jsrsasign），之后常驻。
 *   - 从 stdin 逐行读 JSON 请求：{"id":<n>,"method":"get_ab|get_req_sign|get_ree_key","args":[...]}
 *   - 向 stdout 逐行写 JSON 响应：{"id":<n>,"result":<str>} 或 {"id":<n>,"error":"<msg>"}
 *
 * require 解析：本文件位于 sign/js/ 目录，eval 进来的 dy_ab.js 里的 require('jsrsasign')/
 * require('jsdom') 会基于本模块目录解析到 sign/js/node_modules（与 Dockerfile 安装位置一致）。
 *
 * 注意：刻意不加 'use strict'。dy_ab.js（混淆产物）依赖非严格模式的隐式全局赋值
 * （如 proxyObjs = ...），严格模式下 eval 会抛 ReferenceError。
 */

const fs = require('fs');
const readline = require('readline');

const dyAbPath = process.argv[2];
if (!dyAbPath) {
  process.stderr.write('usage: node sign_pool_runner.js <dy_ab.js>\n');
  process.exit(2);
}

// 直接 eval：使 dy_ab.js 顶层的 function get_ab/get_req_sign/get_ree_key 进入本模块作用域
// （与 PyExecJS 拼接 source + 调用语句执行等价）。
try {
  const source = fs.readFileSync(dyAbPath, 'utf8');
  // eslint-disable-next-line no-eval
  eval(source);
} catch (e) {
  process.stderr.write('LOAD_ERROR ' + (e && e.stack ? e.stack : e) + '\n');
  process.exit(3);
}

function dispatch(method, args) {
  switch (method) {
    case 'get_ab':
      // eslint-disable-next-line no-undef
      return get_ab.apply(null, args);
    case 'get_req_sign':
      // eslint-disable-next-line no-undef
      return get_req_sign.apply(null, args);
    case 'get_ree_key':
      // eslint-disable-next-line no-undef
      return get_ree_key.apply(null, args);
    default:
      throw new Error('unknown method: ' + method);
  }
}

const rl = readline.createInterface({ input: process.stdin });

rl.on('line', (line) => {
  const text = (line || '').trim();
  if (!text) return;
  let req;
  try {
    req = JSON.parse(text);
  } catch (e) {
    process.stdout.write(JSON.stringify({ id: null, error: 'bad json' }) + '\n');
    return;
  }
  const out = { id: req.id };
  try {
    out.result = dispatch(req.method, req.args || []);
  } catch (e) {
    out.error = String(e && e.stack ? e.stack : e);
  }
  process.stdout.write(JSON.stringify(out) + '\n');
});

rl.on('close', () => process.exit(0));

// 启动握手：告诉父进程已就绪
process.stdout.write(JSON.stringify({ id: 'ready', result: 'ok' }) + '\n');
