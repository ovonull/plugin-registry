<script setup lang="ts">
import { CircleAlert, CircleCheck, CircleX, Info, X } from 'lucide-vue-next'
import { useToast } from '../../composables/useToast'

const { toasts, remove } = useToast()
const icons = { success: CircleCheck, error: CircleX, warning: CircleAlert, info: Info }
</script>

<template>
  <div class="toast-host" aria-live="polite">
    <TransitionGroup name="toast">
      <div v-for="item in toasts" :key="item.id" class="toast" :class="`toast-${item.kind}`">
        <component :is="icons[item.kind]" :size="19" />
        <span>{{ item.message }}</span>
        <button class="toast-close" type="button" title="关闭" @click="remove(item.id)">
          <X :size="15" />
        </button>
      </div>
    </TransitionGroup>
  </div>
</template>

