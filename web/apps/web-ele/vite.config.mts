import { defineConfig } from '@vben/vite-config';

import ElementPlus from 'unplugin-element-plus/vite';

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
            rewrite: (path) => path.replace(/^\/basic-api/, ''),
            // mock代理目标地址
            target: 'http://localhost:8000',
            ws: true,
          },
          '/ws': {
            changeOrigin: true,
            target: 'ws://localhost:8000',
            ws: true,
          },
        },
      },
    },
  };
});
