<script setup lang="ts">
import { X } from 'lucide-vue-next'

withDefaults(defineProps<{ open: boolean; title: string; width?: string }>(), {
  width: '520px',
})
const emit = defineEmits<{ close: [] }>()
</script>

<template>
  <Teleport to="body">
    <Transition name="modal">
      <div v-if="open" class="modal-backdrop" role="presentation">
        <section class="modal" :style="{ maxWidth: width }" role="dialog" aria-modal="true">
          <header class="modal-header">
            <h2>{{ title }}</h2>
            <button type="button" class="icon-button" title="关闭" @click="emit('close')">
              <X :size="19" />
            </button>
          </header>
          <div class="modal-body"><slot /></div>
          <footer v-if="$slots.footer" class="modal-footer"><slot name="footer" /></footer>
        </section>
      </div>
    </Transition>
  </Teleport>
</template>

