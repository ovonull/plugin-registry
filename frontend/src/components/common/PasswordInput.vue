<script setup lang="ts">
import { ref } from 'vue'
import { Eye, EyeOff } from 'lucide-vue-next'

defineProps<{
  modelValue: string
  placeholder?: string
  inputClass?: string
  autocomplete?: string
  required?: boolean
  minlength?: number
  maxlength?: number
  disabled?: boolean
}>()

const emit = defineEmits<{ 'update:modelValue': [value: string] }>()
const visible = ref(false)
</script>

<template>
  <div class="password-input">
    <input
      :type="visible ? 'text' : 'password'"
      :value="modelValue"
      :placeholder="placeholder"
      :autocomplete="autocomplete"
      :class="inputClass"
      :required="required"
      :minlength="minlength"
      :maxlength="maxlength"
      :disabled="disabled"
      @input="emit('update:modelValue', ($event.target as HTMLInputElement).value)"
    />
    <button
      type="button"
      tabindex="-1"
      class="password-toggle icon-button"
      :title="visible ? '隐藏密码' : '显示密码'"
      :disabled="disabled"
      @click="visible = !visible"
    >
      <EyeOff v-if="visible" :size="17" />
      <Eye v-else :size="17" />
    </button>
  </div>
</template>
