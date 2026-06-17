<script setup lang="ts">
defineProps<{
  open: boolean;
  title: string;
  dialogRole?: 'dialog' | 'alertdialog';
}>();

const emit = defineEmits<{
  close: [];
}>();
</script>

<template>
  <div v-if="open" class="app-modal-overlay">
    <div class="app-modal-backdrop" @click="emit('close')" />
    <div
      class="app-modal"
      :role="dialogRole || 'dialog'"
      aria-modal="true"
      @click.stop
    >
      <div class="app-modal-head">
        <h3>{{ title }}</h3>
        <button type="button" class="app-modal-close" aria-label="关闭" @click="emit('close')">
          ×
        </button>
      </div>
      <slot />
    </div>
  </div>
</template>

<style scoped>
.app-modal-overlay {
  position: fixed;
  inset: 0;
  z-index: 150;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
}

.app-modal-backdrop {
  position: absolute;
  inset: 0;
  background: rgba(0, 0, 0, 0.55);
}

.app-modal {
  position: relative;
  z-index: 1;
  width: min(560px, 100%);
  max-height: min(90vh, 720px);
  overflow-y: auto;
  background: #1e293b;
  border-radius: 16px;
  padding: 22px;
  border: 1px solid rgba(148, 163, 184, 0.2);
}

.app-modal-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 16px;
}

.app-modal-head h3 {
  margin: 0;
}

.app-modal-close {
  border: none;
  background: rgba(148, 163, 184, 0.15);
  color: #e2e8f0;
  width: 32px;
  height: 32px;
  border-radius: 8px;
  font-size: 1.2rem;
  line-height: 1;
  cursor: pointer;
  flex-shrink: 0;
}

.app-modal-close:hover {
  background: rgba(148, 163, 184, 0.28);
}
</style>
