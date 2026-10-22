<script setup lang="ts">
import { ref } from 'vue'
import { Eye, EyeOff } from 'lucide-vue-next'

const props = defineProps<{
  modelValue: string
  placeholder?: string
  inputClass?: string
  autocomplete?: string
  required?: boolean
  minlength?: number
  maxlength?: number
  disabled?: boolean
  /** 当前值是掩码时，点小眼睛用它取回明文。 */
  reveal?: () => Promise<string>
}>()

const emit = defineEmits<{ 'update:modelValue': [value: string] }>()
const visible = ref(false)
const revealing = ref(false)

async function toggle() {
  // 掩码状态下点开：先向后端要一次明文，再切到可见。
  if (!visible.value && props.modelValue === '******' && props.reveal) {
    if (revealing.value) return
    revealing.value = true
    try {
      const plain = await props.reveal()
      if (plain) emit('update:modelValue', plain)
    } finally {
      revealing.value = false
    }
  }
  visible.value = !visible.value
}
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
      :disabled="disabled || revealing"
      @click="toggle"
    >
      <EyeOff v-if="visible" :size="17" />
      <Eye v-else :size="17" />
    </button>
  </div>
</template>
