<script setup lang="ts">
import { TriangleAlert } from 'lucide-vue-next'
import BaseModal from './BaseModal.vue'

defineProps<{
  open: boolean
  title: string
  message: string
  loading?: boolean
  confirmText?: string
}>()
const emit = defineEmits<{ close: []; confirm: [] }>()
</script>

<template>
  <BaseModal :open="open" :title="title" width="420px" @close="emit('close')">
    <div class="confirm-content">
      <span class="confirm-icon"><TriangleAlert :size="24" /></span>
      <p>{{ message }}</p>
    </div>
    <template #footer>
      <button type="button" class="button secondary" :disabled="loading" @click="emit('close')">取消</button>
      <button type="button" class="button danger" :disabled="loading" @click="emit('confirm')">
        <span v-if="loading" class="button-spinner" />
        {{ confirmText || '确定删除' }}
      </button>
    </template>
  </BaseModal>
</template>
