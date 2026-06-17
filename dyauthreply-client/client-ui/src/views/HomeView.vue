<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { RouterLink } from 'vue-router';
import { getBootstrap, getHealth, type BootstrapInfo } from '../api/client';

const loading = ref(true);
const error = ref('');
const health = ref<{ ok: boolean; env: string } | null>(null);
const bootstrap = ref<BootstrapInfo | null>(null);

onMounted(async () => {
  try {
    health.value = await getHealth();
    bootstrap.value = await getBootstrap();
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    loading.value = false;
  }
});
</script>

<template>
  <section v-if="loading" class="card">正在连接本地服务…</section>
  <section v-else-if="error" class="card error">
    <h2>无法连接后端</h2>
    <p>{{ error }}</p>
    <p class="hint">请先启动桌面客户端，或运行 launcher。</p>
  </section>
  <template v-else>
    <section class="card hero">
      <div class="hero-row">
        <h2>服务正常</h2>
        <span class="pill ok">运行中</span>
      </div>
      <p>环境 {{ health?.env }} · 数据保存在本机，无需登录。</p>
      <ul class="meta">
        <li>数据目录：{{ bootstrap?.data_dir }}</li>
        <li>API：{{ bootstrap?.api_prefix }}</li>
      </ul>
      <RouterLink class="cta" to="/chat">打开私信 →</RouterLink>
      <div class="quick-links">
        <RouterLink to="/accounts">账号</RouterLink>
        <RouterLink to="/rules">规则</RouterLink>
        <RouterLink to="/logs">记录</RouterLink>
      </div>
    </section>
    <section class="card steps">
      <h3>三步开始</h3>
      <ol>
        <li>Chrome 加载扩展 <code>browser-extension/douyin-cred-extractor</code></li>
        <li>打开 creator.douyin.com 私信页，复制 DYCRED1 一键串</li>
        <li>在「我的抖音号」粘贴导入，开启自动回复</li>
      </ol>
    </section>
  </template>
</template>
<style scoped>
.card {
  background: rgba(15, 23, 42, 0.72);
  border: 1px solid rgba(148, 163, 184, 0.12);
  border-radius: 16px;
  padding: 20px 22px;
  margin-bottom: 16px;
}

.card.error {
  border-color: rgba(239, 68, 68, 0.35);
}

.hero-row {
  display: flex;
  align-items: center;
  gap: 12px;
}

.hero h2 {
  margin: 0;
}

.pill {
  padding: 4px 12px;
  border-radius: 999px;
  font-size: 0.8rem;
}

.pill.ok {
  background: rgba(34, 197, 94, 0.15);
  color: #86efac;
}

.meta {
  margin: 12px 0;
  padding-left: 18px;
  color: #94a3b8;
  font-size: 0.88rem;
}

.cta {
  display: inline-block;
  margin-top: 8px;
  padding: 10px 18px;
  background: linear-gradient(135deg, #fe2c55, #ff6b35);
  color: #fff;
  text-decoration: none;
  border-radius: 10px;
  font-weight: 600;
}

.quick-links {
  display: flex;
  gap: 10px;
  margin-top: 14px;
  flex-wrap: wrap;
}

.quick-links a {
  color: #94a3b8;
  text-decoration: none;
  font-size: 0.85rem;
  padding: 6px 12px;
  border-radius: 8px;
  background: rgba(148, 163, 184, 0.1);
}

.steps ol {
  margin: 0;
  padding-left: 20px;
  line-height: 1.8;
  color: #cbd5e1;
}

code {
  background: rgba(15, 23, 42, 0.9);
  padding: 2px 6px;
  border-radius: 6px;
  font-size: 0.85em;
}

.hint {
  color: #94a3b8;
}
</style>
