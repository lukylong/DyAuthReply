import { defineConfig } from '@vben/vite-config';

import ElementPlus from 'unplugin-element-plus/vite';

// 静默化已知无害代理错误：后端主动关闭 keep-alive 时 http-proxy 偶尔会写入
// 半关闭 socket 触发 `writeAfterFIN` / `ECONNRESET` / `ERR_STREAM_WRITE_AFTER_END`，
// 这里吞掉，防止刷屏（真实业务失败仍会由 axios 侧报错）。
const isHarmlessProxyError = (err: NodeJS.ErrnoException): boolean => {
  const msg = err?.message || '';
  return (
    err?.code === 'ECONNRESET' ||
    err?.code === 'EPIPE' ||
    err?.code === 'ERR_STREAM_WRITE_AFTER_END' ||
    msg.includes('This socket has been ended by the other party') ||
    msg.includes('socket hang up')
  );
};

export default defineConfig(async () => {
  return {
    application: {},
    vite: {
      plugins: [
        ElementPlus({
          format: 'esm',
        }),
      ],
      server: {
        proxy: {
          '/basic-api': {
            changeOrigin: true,
            configure: (proxy) => {
              proxy.on('error', (err) => {
                if (isHarmlessProxyError(err)) {
                  // eslint-disable-next-line no-console
                  console.debug(`[proxy] ignored harmless error: ${err.message}`);
                  return;
                }
                // eslint-disable-next-line no-console
                console.error('[proxy] /basic-api error:', err);
              });
            },
            rewrite: (path) => path.replace(/^\/basic-api/, ''),
            target: 'http://localhost:8000',
            // 注意：/basic-api 只走 HTTP，不走 WebSocket；
            // WebSocket 已经由 src/api/core/websocket.ts 直连 ws://localhost:8000/ws/*。
            ws: false,
          },
        },
      },
    },
  };
});
