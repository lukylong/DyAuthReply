import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';
import { fileURLToPath, URL } from 'node:url';
import { readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';

const apiTarget = process.env.VITE_DEV_API || 'http://127.0.0.1:8765';
const nativeTarget = process.env.VITE_NATIVE_AGENT || 'http://127.0.0.1:18765';
const clientRoot = process.env.CLIENT_DATA_DIR || (process.platform === 'darwin'
  ? join(homedir(), 'Library', 'Application Support', 'DyAuthReply')
  : join(process.env.APPDATA || homedir(), 'DyAuthReply'));
const nativeProxy = (ws = false) => ({
  ws,
  target: nativeTarget,
  changeOrigin: true,
  configure: (proxy: { on: (event: 'proxyReq' | 'proxyReqWs', cb: (req: { setHeader: (name: string, value: string) => void }) => void) => void }) => {
    const authenticate = (req: { setHeader: (name: string, value: string) => void }) => {
      try {
        const token = readFileSync(join(clientRoot, 'agent-v2', 'native-api-token'), 'utf8').trim();
        req.setHeader('Authorization', `Bearer ${token}`);
      } catch { /* The native service may still be starting. Never expose the token to browser JS. */ }
    };
    proxy.on('proxyReq', authenticate);
    if (ws) proxy.on('proxyReqWs', authenticate);
  },
});
const tauriConfig = JSON.parse(
  readFileSync(new URL('../desktop/src-tauri/tauri.conf.json', import.meta.url), 'utf-8'),
) as { version?: string };
const appVersion = tauriConfig.version || '0.0.0';

export default defineConfig({
  base: './',
  plugins: [vue()],
  define: {
    __APP_VERSION__: JSON.stringify(`v${appVersion}`),
  },
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      '/api/client/v1/license': nativeProxy(),
      '/api/client/v1/runtime/': nativeProxy(),
      '/api/client/v1/douyin/account/quick-create': nativeProxy(),
      '^/api/client/v1/douyin/account/[^/]+/import-credential$': nativeProxy(),
      '/api/client/v1/bootstrap': nativeProxy(),
      '/api/client/v1/health': nativeProxy(),
      '^/api/client/v1/douyin/account/all$': nativeProxy(),
      '^/api/client/v1/douyin/account/[^/]+/(conversations|manual-reply)(\\?|$)': nativeProxy(),
      '^/api/client/v1/douyin/account/[^/]+/conversation/[^/]+/messages$': nativeProxy(),
      '/api/client/v1/douyin/worker-command/': nativeProxy(),
      '/ws/client/douyin/': nativeProxy(true),
      '/api/client/v1/douyin/rule': nativeProxy(),
      '/api/client/v1/douyin/reply-log': nativeProxy(),
      '/api/client/v1/douyin/template': nativeProxy(),
      '^/api/client/v1/douyin/card(/all)?(\\?|$)': nativeProxy(),
      '^/api/client/v1/douyin/account/[^/]+$': nativeProxy(),
      '/api/client/v1': nativeProxy(),
      '/api': {
        target: apiTarget,
        changeOrigin: true,
      },
      '/ws': {
        target: apiTarget,
        changeOrigin: true,
        ws: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
});
