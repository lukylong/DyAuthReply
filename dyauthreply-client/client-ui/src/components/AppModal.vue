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
  <transition name="modal-fade">
    <div v-if="open" class="app-modal-overlay">
      <div class="app-modal-backdrop" @click="emit('close')" />
      <div
        class="app-modal glass-panel"
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
        <div class="app-modal-content">
          <slot />
        </div>
      </div>
    </div>
  </transition>
</template>

<style scoped>
.app-modal-overlay {
  position: fixed;
  inset: 0;
  z-index: 999;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
}

.app-modal-backdrop {
  position: absolute;
  inset: 0;
  background-color: rgba(241, 243, 247, 0.35);
  backdrop-filter: blur(16px) saturate(120%);
  -webkit-backdrop-filter: blur(16px) saturate(120%);
  will-change: background-color;
  transform: translate3d(0, 0, 0);
  -webkit-transform: translate3d(0, 0, 0);
  backface-visibility: hidden;
  -webkit-backface-visibility: hidden;
}

.app-modal {
  position: relative;
  z-index: 1000;
  width: min(560px, 100%);
  max-height: min(90vh, 720px);
  overflow: hidden;
  background: rgba(255, 255, 255, 0.88);
  backdrop-filter: blur(40px) saturate(140%);
  -webkit-backdrop-filter: blur(40px) saturate(140%);
  border: 1px solid rgba(0, 0, 0, 0.06);
  border-radius: 20px;
  padding: 24px;
  box-shadow: 
    0 24px 60px rgba(0, 0, 0, 0.12),
    inset 0 1px 1px rgba(255, 255, 255, 0.8);
  display: flex;
  flex-direction: column;
  will-change: transform, opacity;
  transform: translate3d(0, 0, 0);
  -webkit-transform: translate3d(0, 0, 0);
  backface-visibility: hidden;
  -webkit-backface-visibility: hidden;
}

.app-modal-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 20px;
  flex-shrink: 0;
}

.app-modal-head h3 {
  margin: 0;
  font-size: 1.15rem;
  font-weight: 700;
  color: var(--text-primary);
}

.app-modal-close {
  border: none;
  background: rgba(0, 0, 0, 0.03);
  border: 1px solid rgba(0, 0, 0, 0.03);
  color: var(--text-secondary);
  width: 30px;
  height: 30px;
  border-radius: 50%;
  font-size: 1.1rem;
  line-height: 1;
  cursor: pointer;
  flex-shrink: 0;
  display: grid;
  place-items: center;
  transition: var(--transition-quick);
}

.app-modal-close:hover {
  background: rgba(0, 0, 0, 0.08);
  color: var(--text-primary);
  border-color: rgba(0, 0, 0, 0.08);
}

.app-modal-content {
  overflow-y: auto;
  flex: 1;
  min-height: 0;
}

/* Transition animations */
.modal-fade-enter-active,
.modal-fade-leave-active {
  transition: none; /* No transition on root container */
}

/* Transition backdrop and modal separately */
.modal-fade-enter-active .app-modal-backdrop,
.modal-fade-leave-active .app-modal-backdrop {
  transition: background-color 0.25s ease;
}

.modal-fade-enter-active .app-modal,
.modal-fade-leave-active .app-modal {
  transition: transform 0.3s cubic-bezier(0.16, 1, 0.3, 1), opacity 0.3s cubic-bezier(0.16, 1, 0.3, 1);
}

.modal-fade-enter-from .app-modal-backdrop,
.modal-fade-leave-to .app-modal-backdrop {
  background-color: rgba(241, 243, 247, 0) !important;
}

.modal-fade-enter-from .app-modal,
.modal-fade-leave-to .app-modal {
  opacity: 0;
  transform: scale(0.96) translateY(10px);
}
</style>
