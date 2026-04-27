<script lang="ts" setup>
import type { DouyinRule, DouyinRuleCreateInput, DouyinTemplate } from '#/api/core/douyin';

import { computed, reactive, ref } from 'vue';

import {
  ElForm,
  ElFormItem,
  ElInput,
  ElInputNumber,
  ElMessage,
  ElOption,
  ElSelect,
  ElSwitch,
  ElTag,
  ElTimePicker,
} from 'element-plus';

import {
  createDouyinRuleApi,
  getAllTemplate,
  getSimpleDouyinAccountListApi,
  updateDouyinRuleApi,
} from '#/api/core/douyin';
import { ZqDialog } from '#/components/zq-dialog';

const emit = defineEmits<{ success: [] }>();

const visible = ref(false);
const confirmLoading = ref(false);
const formRef = ref<InstanceType<typeof ElForm>>();
const editingId = ref<null | string>(null);
const templates = ref<DouyinTemplate[]>([]);
const accounts = ref<Array<{ id: string; nickname: string }>>([]);

const form = reactive({
  account_id: '',
  name: '',
  match_type: 'default' as 'contains' | 'default' | 'regex',
  keywords_text: '',
  regex_pattern: '',
  template_id: '' as '' | string,
  weekday_values: ['1', '2', '3', '4', '5', '6', '7'] as string[],
  time_window_start: '',
  time_window_end: '',
  cooldown_seconds: 60,
  priority: 0,
  status: true,
  remark: '',
});

const title = computed(() => (editingId.value ? '编辑规则' : '新增规则'));
const selectedTemplate = computed(() =>
  templates.value.find((item) => item.id === form.template_id),
);

function maskToWeekdays(mask?: string): string[] {
  const source = mask && mask.length >= 7 ? mask : '1111111';
  return source
    .slice(0, 7)
    .split('')
    .map((v, idx) => (v === '1' ? String(idx + 1) : ''))
    .filter(Boolean);
}

function weekdaysToMask(values?: string[]): string {
  const selected = new Set(values || []);
  return Array.from({ length: 7 }, (_, idx) =>
    selected.has(String(idx + 1)) ? '1' : '0',
  ).join('');
}

function splitKeywords(text?: string): string[] {
  if (!text) return [];
  return text
    .split(/\n|,|，/)
    .map((s) => s.trim())
    .filter(Boolean);
}

function resetForm() {
  Object.assign(form, {
    account_id: '',
    name: '',
    match_type: 'default',
    keywords_text: '',
    regex_pattern: '',
    template_id: '',
    weekday_values: ['1', '2', '3', '4', '5', '6', '7'],
    time_window_start: '',
    time_window_end: '',
    cooldown_seconds: 60,
    priority: 0,
    status: true,
    remark: '',
  });
}

async function ensureOptionsLoaded() {
  if (accounts.value.length === 0) {
    const res = await getSimpleDouyinAccountListApi();
    accounts.value = res.map((item) => ({ id: item.id, nickname: item.nickname }));
  }
  if (templates.value.length === 0) {
    templates.value = await getAllTemplate();
  }
}

function fillForm(row: DouyinRule) {
  Object.assign(form, {
    account_id: row.account_id,
    name: row.name,
    match_type: row.match_type,
    keywords_text: (row.keywords || []).join('\n'),
    regex_pattern: row.regex_pattern || '',
    template_id: row.template_id || '',
    weekday_values: maskToWeekdays(row.weekday_mask),
    time_window_start: row.time_window_start || '',
    time_window_end: row.time_window_end || '',
    cooldown_seconds: row.cooldown_seconds ?? 60,
    priority: row.priority ?? 0,
    status: row.status !== false,
    remark: row.remark || '',
  });
}

function buildPayload(): DouyinRuleCreateInput {
  return {
    account_id: form.account_id,
    name: form.name,
    match_type: form.match_type,
    keywords: form.match_type === 'contains' ? splitKeywords(form.keywords_text) : [],
    regex_pattern: form.match_type === 'regex' ? form.regex_pattern || null : null,
    template_id: form.template_id || null,
    // 当前产品形态下，自动回复统一走模板内容；未选模板时才允许旧字段兜底为空。
    reply_text: '',
    links: [],
    // sender 中模板优先后，rule.send_mode 仅作为兼容值
    send_mode: 'merged',
    // 当前先固定私信渠道，避免规则页过于复杂
    channel: 'dm',
    weekday_mask: weekdaysToMask(form.weekday_values),
    time_window_start: form.time_window_start || null,
    time_window_end: form.time_window_end || null,
    priority: form.priority ?? 0,
    status: form.status !== false,
    cooldown_seconds: form.cooldown_seconds ?? 60,
    remark: form.remark || null,
  };
}

async function onSubmit() {
  await formRef.value?.validate();
  if (!form.account_id) {
    ElMessage.warning('请选择所属账号');
    return;
  }
  if (!form.name) {
    ElMessage.warning('请输入规则名称');
    return;
  }
  if (form.match_type === 'contains' && splitKeywords(form.keywords_text).length === 0) {
    ElMessage.warning('关键词回复至少需要一个关键词');
    return;
  }
  if (form.match_type === 'regex' && !form.regex_pattern) {
    ElMessage.warning('正则匹配需要填写正则表达式');
    return;
  }
  if (!form.template_id) {
    ElMessage.warning('请选择引用模板');
    return;
  }

  confirmLoading.value = true;
  try {
    const payload = buildPayload();
    if (editingId.value) {
      await updateDouyinRuleApi(editingId.value, payload);
      ElMessage.success('已更新');
    } else {
      await createDouyinRuleApi(payload);
      ElMessage.success('已创建');
    }
    visible.value = false;
    emit('success');
  } finally {
    confirmLoading.value = false;
  }
}

async function open(data?: DouyinRule) {
  await ensureOptionsLoaded();
  resetForm();
  editingId.value = data?.id || null;
  if (data) fillForm(data);
  visible.value = true;
}

defineExpose({ open });
</script>

<template>
  <ZqDialog
    v-model="visible"
    :title="title"
    :confirm-loading="confirmLoading"
    width="760px"
    @confirm="onSubmit"
  >
    <ElForm ref="formRef" :model="form" label-width="100px" class="mx-4">
      <ElFormItem label="所属账号" required>
        <ElSelect v-model="form.account_id" placeholder="请选择账号" filterable>
          <ElOption
            v-for="item in accounts"
            :key="item.id"
            :label="item.nickname"
            :value="item.id"
          />
        </ElSelect>
      </ElFormItem>

      <ElFormItem label="规则名称" required>
        <ElInput v-model="form.name" placeholder="例如：陌生人默认回复" />
      </ElFormItem>

      <ElFormItem label="匹配方式" required>
        <ElSelect v-model="form.match_type">
          <ElOption label="默认兜底" value="default" />
          <ElOption label="关键词回复" value="contains" />
          <ElOption label="正则表达式" value="regex" />
        </ElSelect>
      </ElFormItem>

      <ElFormItem v-if="form.match_type === 'contains'" label="关键词" required>
        <ElInput
          v-model="form.keywords_text"
          type="textarea"
          :rows="3"
          placeholder="每行一个关键词；用户消息命中其中任意一个就触发回复"
        />
      </ElFormItem>

      <ElFormItem v-if="form.match_type === 'regex'" label="正则表达式" required>
        <ElInput
          v-model="form.regex_pattern"
          placeholder="预留高级匹配能力，例如 (发货|物流|什么时候)"
        />
      </ElFormItem>

      <ElFormItem label="引用模板" required>
        <ElSelect v-model="form.template_id" clearable placeholder="请选择回复模板" filterable>
          <ElOption
            v-for="item in templates"
            :key="item.id"
            :label="item.name"
            :value="item.id"
          />
        </ElSelect>
      </ElFormItem>

      <ElFormItem v-if="selectedTemplate" label="模板内容">
        <div class="template-summary">
          <div class="template-content">{{ selectedTemplate.content }}</div>
          <div class="template-meta">
            <ElTag size="small" type="success">发送模式：{{ selectedTemplate.send_mode }}</ElTag>
            <ElTag size="small" type="info">规则默认作用于私信</ElTag>
          </div>
        </div>
      </ElFormItem>

      <ElFormItem label="生效星期">
        <div class="weekday-group">
          <label v-for="item in ['1','2','3','4','5','6','7']" :key="item" class="weekday-item">
            <input v-model="form.weekday_values" :value="item" type="checkbox" />
            <span>{{ ['一','二','三','四','五','六','日'][Number(item) - 1] }}</span>
          </label>
        </div>
      </ElFormItem>

      <ElFormItem label="生效时段">
        <div class="time-range">
          <ElTimePicker
            v-model="form.time_window_start"
            value-format="HH:mm:ss"
            format="HH:mm"
            clearable
            placeholder="开始"
          />
          <span>~</span>
          <ElTimePicker
            v-model="form.time_window_end"
            value-format="HH:mm:ss"
            format="HH:mm"
            clearable
            placeholder="结束"
          />
        </div>
      </ElFormItem>

      <ElFormItem label="冷却秒数">
        <ElInputNumber v-model="form.cooldown_seconds" :min="0" :max="86400" />
      </ElFormItem>

      <ElFormItem label="优先级">
        <ElInputNumber v-model="form.priority" :min="0" :max="9999" />
      </ElFormItem>

      <ElFormItem label="备注">
        <ElInput v-model="form.remark" placeholder="可选，记录这条规则用途" />
      </ElFormItem>

      <ElFormItem label="启用">
        <ElSwitch v-model="form.status" />
      </ElFormItem>
    </ElForm>
  </ZqDialog>
</template>

<style scoped>
.template-summary {
  width: 100%;
  padding: 10px 12px;
  background: #f8fafc;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
}

.template-content {
  margin-bottom: 8px;
  color: #111827;
  white-space: pre-wrap;
  word-break: break-word;
}

.template-meta {
  display: flex;
  gap: 8px;
}

.weekday-group {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}

.weekday-item {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.time-range {
  display: flex;
  align-items: center;
  gap: 8px;
}
</style>
