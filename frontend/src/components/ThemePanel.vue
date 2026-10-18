<script setup lang="ts">
import { Check } from 'lucide-vue-next'
import { themeColors, themeFonts, useTheme } from '../composables/useTheme'
import BaseModal from './common/BaseModal.vue'

defineProps<{ open: boolean }>()
const emit = defineEmits<{ close: [] }>()
const { state, setColor, setEffect, setFont } = useTheme()
</script>

<template>
  <BaseModal :open="open" title="界面设置" width="500px" @close="emit('close')">
    <div class="theme-section">
      <h3>主题颜色</h3>
      <div class="color-grid">
        <button
          v-for="color in themeColors"
          :key="color.value"
          type="button"
          class="color-option"
          :class="{ active: state.color === color.value }"
          :title="color.name"
          @click="setColor(color.value, color.soft)"
        >
          <span class="color-swatch" :style="{ background: color.value }">
            <Check v-if="state.color === color.value" :size="15" />
          </span>
          <span>{{ color.name }}</span>
        </button>
      </div>
    </div>
    <div class="theme-section">
      <h3>显示效果</h3>
      <div class="segmented-control">
        <button type="button" :class="{ active: state.effect === 'solid' }" @click="setEffect('solid')">纯色</button>
        <button type="button" :class="{ active: state.effect === 'gradient' }" @click="setEffect('gradient')">炫彩</button>
      </div>
    </div>
    <div class="theme-section">
      <h3>界面字体</h3>
      <select :value="state.font" @change="setFont(($event.target as HTMLSelectElement).value)">
        <option v-for="font in themeFonts" :key="font.value" :value="font.value">{{ font.name }}</option>
      </select>
    </div>
    <template #footer>
      <button type="button" class="button primary" @click="emit('close')">完成</button>
    </template>
  </BaseModal>
</template>

