<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { Check, LoaderCircle, RotateCcw, ShieldCheck } from 'lucide-vue-next'
import { publicApi } from '../../api'
import type { GeetestResult } from '../../types'

withDefaults(defineProps<{ disabled?: boolean }>(), { disabled: false })
const emit = defineEmits<{ success: [result: GeetestResult]; error: [message: string] }>()

const status = ref<'loading' | 'ready' | 'verified' | 'error'>('loading')
const errorMessage = ref('')
let captcha: GeetestObject | null = null
let mounted = true

function loadScript(): Promise<void> {
  if (window.initGeetest) return Promise.resolve()
  return new Promise((resolve, reject) => {
    const existing = document.querySelector<HTMLScriptElement>('script[src*="geetest.com/static/tools/gt.js"]')
    const waitForSdk = () => {
      let attempts = 0
      const timer = window.setInterval(() => {
        attempts += 1
        if (window.initGeetest) {
          window.clearInterval(timer)
          resolve()
        } else if (attempts >= 100) {
          window.clearInterval(timer)
          reject(new Error('极验组件加载超时'))
        }
      }, 100)
    }
    if (existing) return waitForSdk()
    const script = document.createElement('script')
    script.src = 'https://static.geetest.com/static/tools/gt.js'
    script.async = true
    script.onload = waitForSdk
    script.onerror = () => reject(new Error('极验组件加载失败'))
    document.head.appendChild(script)
  })
}

function setError(message: string) {
  if (!mounted) return
  status.value = 'error'
  errorMessage.value = message
  emit('error', message)
}

async function initialize() {
  status.value = 'loading'
  errorMessage.value = ''
  try {
    await loadScript()
    const response = await publicApi.geetestRegister()
    if (!mounted || !response.success || !response.data) throw new Error(response.message)
    const data = response.data as { gt?: string; challenge?: string; success?: number; new_captcha?: boolean }
    if (!data.gt || !data.challenge || !window.initGeetest) throw new Error('极验初始化参数不完整')
    window.initGeetest({
      gt: data.gt,
      challenge: data.challenge,
      offline: false,
      new_captcha: data.new_captcha,
      product: 'bind',
      width: '100%',
      lang: 'zh-cn',
    }, (instance) => {
      if (!mounted) return
      captcha = instance
      instance.onReady(() => { if (mounted) status.value = 'ready' })
      instance.onSuccess(async () => {
        const value = instance.getValidate()
        if (!value) return
        status.value = 'loading'
        const result: GeetestResult = {
          challenge: value.geetest_challenge,
          validate: value.geetest_validate,
          seccode: value.geetest_seccode,
        }
        const validated = await publicApi.geetestValidate(result)
        if (!mounted) return
        if (validated.success) {
          status.value = 'verified'
          emit('success', result)
        } else {
          status.value = 'error'
          errorMessage.value = validated.message
          instance.reset()
        }
      })
      instance.onError(() => setError('极验组件加载失败'))
      instance.onClose(() => undefined)
    })
  } catch (error) {
    setError(error instanceof Error ? error.message : '极验初始化失败')
  }
}

function verify() {
  if (status.value === 'error') initialize()
  else if (status.value === 'ready') captcha?.verify()
}

onMounted(initialize)
onBeforeUnmount(() => { mounted = false; captcha = null })
</script>

<template>
  <button
    type="button"
    class="geetest-button"
    :class="`geetest-${status}`"
    :disabled="disabled || status === 'loading' || status === 'verified'"
    @click="verify"
  >
    <LoaderCircle v-if="status === 'loading'" class="spin" :size="17" />
    <Check v-else-if="status === 'verified'" :size="18" />
    <RotateCcw v-else-if="status === 'error'" :size="17" />
    <ShieldCheck v-else :size="18" />
    <span v-if="status === 'loading'">验证中…</span>
    <span v-else-if="status === 'verified'">验证成功</span>
    <span v-else-if="status === 'error'">{{ errorMessage }}，点击重试</span>
    <span v-else>点击进行滑动验证</span>
  </button>
</template>
