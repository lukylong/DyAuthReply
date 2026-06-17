import { createRouter, createWebHashHistory } from 'vue-router';

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    {
      path: '/',
      name: 'splash',
      component: () => import('../views/SplashView.vue'),
    },
    {
      path: '/home',
      name: 'home',
      component: () => import('../views/HomeView.vue'),
    },
    {
      path: '/accounts',
      name: 'accounts',
      component: () => import('../views/AccountsView.vue'),
    },
    {
      path: '/chat',
      name: 'chat',
      component: () => import('../views/ChatView.vue'),
      meta: { wide: true },
    },
    {
      path: '/rules',
      name: 'rules',
      component: () => import('../views/RulesView.vue'),
    },
    {
      path: '/logs',
      name: 'logs',
      component: () => import('../views/LogsView.vue'),
    },
    {
      path: '/admin',
      name: 'admin',
      meta: { admin: true },
      component: () => import('../views/AdminConsoleView.vue'),
    },
  ],
});

router.beforeEach((to) => {
  if (to.meta.admin && typeof window !== 'undefined') {
    const token = sessionStorage.getItem('dyauthreply_admin_token');
    if (!token) return '/home';
  }
  return true;
});

export default router;
