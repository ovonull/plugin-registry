<script setup lang="ts">
import { LoaderCircle } from 'lucide-vue-next'

withDefaults(defineProps<{ visible: boolean; text?: string; contained?: boolean }>(), {
  text: '加载中…',
  contained: false,
})
</script>

<template>
  <Transition name="fade">
    <div v-if="visible" class="loading-overlay" :class="{ contained }">
      <LoaderCircle class="spin" :size="26" />
      <span>{{ text }}</span>
    </div>
  </Transition>
</template>

