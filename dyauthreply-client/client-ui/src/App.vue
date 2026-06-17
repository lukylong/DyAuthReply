<script setup lang="ts">
import { computed } from 'vue';
import { useRoute } from 'vue-router';
import AppShell from './layouts/AppShell.vue';

const route = useRoute();
const isStandalone = computed(() => route.name === 'splash');
</script>

<template>
  <RouterView v-if="isStandalone" />
  <AppShell v-else />
</template>
