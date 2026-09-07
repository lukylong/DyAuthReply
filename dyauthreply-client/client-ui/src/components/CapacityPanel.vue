<script setup lang="ts">
import { ref, onMounted, onUnmounted } from "vue";
import { Gauge, RefreshCw } from "lucide-vue-next";
import {
  getCapacity,
  runCapacityBenchmark,
  saveCapacity,
  type CapacityEstimate,
} from "../api/client";
const props = defineProps<{ editable?: boolean }>();
const data = ref<CapacityEstimate | null>(null);
const busy = ref(false);
const benchmarking = ref(false);
const error = ref("");
const feedback = ref("");
const conservative = ref(false);
const manual = ref(false);
const limit = ref(100);
let disposed = false;
async function load() {
  busy.value = true;
  error.value = "";
  try {
    const next = await getCapacity();
    if (disposed) return;
    data.value = next;
    conservative.value = next.policy.conservative;
    manual.value = next.policy.manual_limit !== null;
    limit.value = next.policy.manual_limit ?? Math.max(1, next.effective_limit);
  } catch (e) {
    if (!disposed) error.value = e instanceof Error ? e.message : String(e);
  } finally {
    if (!disposed) busy.value = false;
  }
}
async function runBenchmark() {
  if (!data.value || benchmarking.value) return;
  benchmarking.value = true;
  error.value = "";
  feedback.value = "";
  try {
    const result = await runCapacityBenchmark();
    if (!disposed && data.value) {
      data.value.benchmark = result.benchmark;
      feedback.value = "性能测试完成";
    }
  } catch (e) {
    if (!disposed) error.value = e instanceof Error ? e.message : String(e);
  } finally {
    if (!disposed) benchmarking.value = false;
  }
}
async function save() {
  if (
    manual.value &&
    (!Number.isInteger(limit.value) ||
      limit.value < 1 ||
      limit.value > (data.value?.validated_ceiling ?? 300))
  ) {
    error.value = "请填写有效的账号数量";
    return;
  }
  busy.value = true;
  error.value = "";
  feedback.value = "";
  try {
    const next = await saveCapacity({
      conservative: conservative.value,
      manual_limit: manual.value ? limit.value : null,
    });
    if (!disposed) {
      data.value = next;
      feedback.value = "设置已保存，请重新运行性能测试。";
    }
  } catch (e) {
    if (!disposed) error.value = e instanceof Error ? e.message : String(e);
  } finally {
    if (!disposed) busy.value = false;
  }
}
onMounted(load);
onUnmounted(() => {
  disposed = true;
});
</script>

<template>
  <section
    class="capacity-panel"
    :class="{ editable: props.editable }"
    aria-label="设备性能测试"
  >
    <header>
      <div class="title">
        <Gauge :size="19" />
        <h3>设备性能测试</h3>
        <span v-if="data?.benchmark" class="estimate-tag">已评估</span>
      </div>
    </header>
    <p v-if="error" class="error" role="alert">
      {{ error }} <button @click="runBenchmark">重试</button>
    </p>
    <template v-if="data">
      <div v-if="!data.benchmark" class="benchmark-empty">
        <h4>测试这台电脑适合托管多少账号</h4>
        <p>运行一次隔离的本地调度测试，默认不展示未经测试的承载数字。</p>
        <button class="btn-glass btn-primary-glass" :disabled="benchmarking" @click="runBenchmark">
          <RefreshCw :size="16" :class="{ spinning: benchmarking }" />
          {{ benchmarking ? "正在测试…" : "开始性能测试" }}
        </button>
      </div>
      <div v-else class="benchmark-result">
        <span>建议承载</span>
        <p><strong>{{ data.benchmark.estimated_accounts }}</strong> 个账号</p>
        <small>综合本次 CPU 空闲、内存余量和 Rust 调度吞吐；不连接抖音，也不会发送消息。</small>
        <small v-if="feedback" class="benchmark-feedback" role="status">{{ feedback }}</small>
        <button class="btn-glass" :disabled="benchmarking" @click="runBenchmark">
          <RefreshCw :size="15" :class="{ spinning: benchmarking }" />
          {{ benchmarking ? "正在重新测试…" : "重新测试" }}
        </button>
      </div>
      <template v-if="editable">
        <div class="policy">
          <label
            >资源策略<select v-model="conservative" :disabled="busy || benchmarking">
              <option :value="false">均衡 · 为其他应用保留资源</option>
              <option :value="true">节能 · 更低资源预算</option>
            </select></label
          >
          <label
            >承载上限<select v-model="manual" :disabled="busy || benchmarking">
              <option :value="false">按测试建议</option>
              <option :value="true">自定义较低上限</option>
            </select></label
          >
          <label v-if="manual"
            >账号数量<input
              v-model.number="limit"
              type="number"
              min="1"
              :max="data.validated_ceiling"
              :disabled="busy || benchmarking"
          /></label>
        </div>
        <details>
          <summary>测试说明</summary>
          <p>
            测试会在临时目录中模拟账号调度和消息任务，完成后立即清理。结果是建议值，
            实际使用还会受到消息频率、网络和平台状态影响。
          </p>
        </details>
        <footer>
          <span role="status">{{
            feedback || "配置保存在本机，覆盖安装后继续保留。"
          }}</span
          ><button
            class="btn-glass btn-primary-glass"
            :disabled="busy || benchmarking"
            @click="save"
          >
            {{ busy ? "处理中…" : "保存承载设置" }}
          </button>
        </footer>
      </template>
    </template>
    <p v-else-if="busy" class="context">正在读取性能测试状态…</p>
  </section>
</template>

<style scoped>
.capacity-panel {
  background: var(--bg-card);
  border: 1px solid var(--border-subtle);
  border-radius: 16px;
  padding: 22px;
  color: var(--text-primary);
}
header,
.title,
footer,
.benchmark-empty button,
.benchmark-result button {
  display: flex;
  align-items: center;
  gap: 8px;
}
header,
footer {
  justify-content: space-between;
}
.title svg {
  color: var(--brand-primary);
}
h3,
h4,
.benchmark-result p {
  margin: 0;
}
h3 {
  font-size: 15px;
}
.estimate-tag {
  font-size: 11px;
  background: var(--success-soft);
  color: var(--success);
  padding: 3px 7px;
  border-radius: 5px;
}
.benchmark-empty,
.benchmark-result {
  margin-top: 18px;
  border: 1px solid var(--border-subtle);
  border-radius: 14px;
  padding: 28px;
  text-align: center;
}
.benchmark-empty {
  background: var(--bg-app);
}
.benchmark-empty h4 {
  font-size: 17px;
}
.benchmark-empty p,
.benchmark-result small {
  display: block;
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 1.7;
}
.benchmark-feedback {
  color: var(--success) !important;
  margin-top: 6px;
}
.benchmark-empty p {
  margin: 8px 0 18px;
}
.benchmark-empty button,
.benchmark-result button {
  justify-content: center;
  margin: 0 auto;
}
.benchmark-result {
  background: var(--brand-primary-soft);
  border-color: var(--glass-border-active);
}
.benchmark-result > span {
  color: var(--text-secondary);
  font-size: 13px;
}
.benchmark-result p {
  margin: 6px 0;
  font-size: 18px;
}
.benchmark-result strong {
  color: var(--brand-primary);
  font-size: 48px;
  letter-spacing: -2px;
}
.benchmark-result button {
  margin-top: 16px;
}
.policy {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
  margin: 22px 0;
}
.policy label {
  display: grid;
  gap: 8px;
  font-size: 13px;
}
.policy select,
.policy input {
  width: 100%;
  padding: 10px;
  border: 1px solid var(--border-subtle);
  border-radius: 8px;
  background: var(--bg-card);
  color: var(--text-primary);
}
details {
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 1.7;
}
summary {
  cursor: pointer;
}
footer {
  margin-top: 20px;
  flex-wrap: wrap;
}
footer span {
  font-size: 12px;
  color: var(--text-secondary);
  flex: 1;
  min-width: 200px;
}
.error {
  color: var(--danger);
  font-size: 13px;
}
.spinning {
  animation: spin 0.9s linear infinite;
}
@keyframes spin {
  to { transform: rotate(360deg); }
}
@media (max-width: 650px) {
  .policy {
    grid-template-columns: 1fr;
  }
  .capacity-panel {
    padding: 16px;
  }
  .benchmark-empty,
  .benchmark-result {
    padding: 20px 14px;
  }
}
</style>
