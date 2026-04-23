<script lang="ts" setup>
import type { FormulaInputEmits, FormulaInputProps } from './types';

import { computed, watch } from 'vue';

import { ElInput, ElTooltip } from 'element-plus';

defineOptions({
  name: 'FormulaInput',
});

const props = withDefaults(defineProps<FormulaInputProps>(), {
  precision: 2,
  disabled: true,
  placeholder: '自动计算',
  showFormula: true,
});

const emit = defineEmits<FormulaInputEmits>();

const parseFormula = (formula: string): string[] => {
  const regex = /\{([^}]+)\}/g;
  const fields: string[] = [];
  let match;

  while ((match = regex.exec(formula)) !== null) {
    if (match[1]) {
      fields.push(match[1]);
    }
  }

  return fields;
};

const evaluateFormula = (
  formula: string,
  data: Record<string, any>,
): null | number => {
  if (!formula || !data) return null;

  try {
    let expression = formula;

    const fields = parseFormula(formula);

    for (const field of fields) {
      const value = data[field];
      const numValue =
        typeof value === 'number' ? value : Number.parseFloat(value);

      if (Number.isNaN(numValue)) {
        return null;
      }

      expression = expression.replaceAll(
        new RegExp(`\\{${field}\\}`, 'g'),
        numValue.toString(),
      );
    }

    expression = expression.replaceAll(/[^0-9+\-*/().]/g, '');

    if (!expression) return null;

    const result = new Function(`return ${expression}`)();

    if (
      typeof result === 'number' &&
      !Number.isNaN(result) &&
      Number.isFinite(result)
    ) {
      return result;
    }

    return null;
  } catch {
    console.warn('Formula evaluation failed:', formula);
    return null;
  }
};

const calculatedValue = computed(() => {
  if (!props.formula || !props.formData) return null;

  const result = evaluateFormula(props.formula, props.formData);

  if (result !== null) {
    return Number(result.toFixed(props.precision));
  }

  return null;
});

const displayValue = computed(() => {
  if (calculatedValue.value !== null) {
    return calculatedValue.value.toFixed(props.precision);
  }
  return '';
});

const formulaDisplay = computed(() => {
  if (!props.formula) return '';

  let display = props.formula;
  const fields = parseFormula(props.formula);

  for (const field of fields) {
    const value = props.formData?.[field];
    if (value !== undefined && value !== null && value !== '') {
      display = display.replace(`{${field}}`, `[${value}]`);
    }
  }

  return display;
});

watch(
  calculatedValue,
  (newVal) => {
    if (newVal !== null && newVal !== props.modelValue) {
      emit('update:modelValue', newVal);
      emit('change', newVal);
    }
  },
  { immediate: true },
);
</script>

<template>
  <div class="formula-input-wrapper">
    <ElInput
      :model-value="displayValue"
      :placeholder="placeholder"
      :disabled="true"
      readonly
      class="formula-input"
    >
      <template #suffix>
        <ElTooltip
          v-if="showFormula && formula"
          :content="`公式: ${formulaDisplay}`"
          placement="top"
        >
          <span
            class="cursor-help text-xs text-[var(--el-text-color-placeholder)]"
            >fx</span
          >
        </ElTooltip>
      </template>
    </ElInput>
  </div>
</template>

<style scoped>
.formula-input :deep(.el-input__inner) {
  cursor: default;
}
</style>
