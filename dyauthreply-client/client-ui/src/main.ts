import { createApp } from 'vue';
import './style.css';
import App from './App.vue';
import router from './router';
import { useClientLicense } from './composables/useClientLicense';

// 清理旧版 Teleport 可能遗留在 body 上的遮罩，避免挡住整页点击
document.querySelectorAll('body > .overlay, body > .app-modal-overlay').forEach((el) => {
  el.remove();
});

router.afterEach(() => {
  document.querySelectorAll('body > .overlay, body > .app-modal-overlay').forEach((el) => {
    el.remove();
  });
});

useClientLicense().startPolling();

createApp(App).use(router).mount('#app');
