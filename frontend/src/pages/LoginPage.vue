<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { KeyRound, LockKeyhole, Mail, UserRound } from 'lucide-vue-next'
import { authApi, publicApi } from '../api'
import GeetestCaptcha from '../components/common/GeetestCaptcha.vue'
import LoadingOverlay from '../components/common/LoadingOverlay.vue'
import PasswordInput from '../components/common/PasswordInput.vue'
import { useAuth } from '../composables/useAuth'
import { useCountdown } from '../composables/useCountdown'
import { useSystemSettings } from '../composables/useSystemSettings'
import { useToast } from '../composables/useToast'
import type { GeetestResult, LoginPayload, LoginType } from '../types'

const router = useRouter()
const route = useRoute()
const auth = useAuth()
const system = useSystemSettings()
const toast = useToast()
const activeTab = ref<LoginType>('username_password')
const username = ref('')
const email = ref('')
const password = ref('')
const emailCode = ref('')
const geetest = ref<GeetestResult | null>(null)
const captchaKey = ref(0)
const loading = ref(false)
const sending = ref(false)
const { countdown, startCountdown } = useCountdown()
const tabs = ref([
  { value: 'username_password' as LoginType, label: '账号密码' },
  { value: 'email_password' as LoginType, label: '邮箱密码' },
  { value: 'email_code' as LoginType, label: '邮箱验证码' },
])
const needsUsername = computed(() => activeTab.value === 'username_password')
const needsPassword = computed(() => activeTab.value !== 'email_code')

function resetCaptcha() {
  geetest.value = null
  captchaKey.value += 1
}

function switchTab(tab: LoginType) {
  activeTab.value = tab
  resetCaptcha()
}

async function sendCode() {
  if (!email.value) return toast.show('请输入邮箱地址', 'warning')
  if (!geetest.value) return toast.show('请先完成滑动验证', 'warning')
  sending.value = true
  const result = await authApi.sendEmailCode(email.value, 'login', geetest.value.challenge)
  sending.value = false
  if (!result.success) {
    toast.show(result.message, 'error')
    resetCaptcha()
    return
  }
  toast.show(result.message, 'success')
  startCountdown(60)
}

async function submit() {
  if (!geetest.value) return toast.show('请先完成滑动验证', 'warning')
  if (needsUsername.value && !username.value) return toast.show('请输入用户名', 'warning')
  if (!needsUsername.value && !email.value) return toast.show('请输入邮箱地址', 'warning')
  if (needsPassword.value && !password.value) return toast.show('请输入密码', 'warning')
  if (!needsPassword.value && !emailCode.value) return toast.show('请输入邮箱验证码', 'warning')

  const payload: LoginPayload = {
    login_type: activeTab.value,
    geetest_challenge: geetest.value.challenge,
  }
  if (needsUsername.value) payload.username = username.value
  else payload.email = email.value
  if (needsPassword.value) payload.password = password.value
  else payload.email_code = emailCode.value

  loading.value = true
  const result = await authApi.login(payload)
  loading.value = false
  if (!result.success || !result.data) {
    toast.show(result.message, 'error')
    resetCaptcha()
    return
  }
  auth.setSession(result.data.token, result.data.user)
  toast.show('登录成功', 'success')
  router.replace(String(route.query.redirect || '/dashboard'))
}

onMounted(async () => {
  system.loadPublic()
  const result = await publicApi.dictionaries(['login_type'])
  if (result.success && result.data?.login_type?.length) {
    tabs.value = result.data.login_type.map((item) => ({
      value: item.value as LoginType,
      label: item.label,
    }))
  }
})
</script>

<template>
  <div class="auth-page">
    <section class="auth-brand">
      <div class="brand-mark"><img src="/napcat-logo.png" alt="PlugHub" /></div>
      <div>
        <p class="eyebrow">PLUGHUB</p>
        <h1>{{ system.state.introduction.name || 'PlugHub 插件库' }}</h1>
        <p class="brand-description">{{ system.state.introduction.description || 'PlugHub 插件分发与审核平台' }}</p>
      </div>
      <div class="brand-line" />
      <p class="brand-footnote">让每一次消息触达，都清晰可控。</p>
    </section>
    <section class="auth-panel">
      <div class="auth-form-wrap">
        <div class="mobile-auth-logo"><img src="/napcat-logo.png" alt="PlugHub" /></div>
        <p class="eyebrow">欢迎回来</p>
        <h2>登录管理后台</h2>
        <p class="auth-subtitle">选择适合的方式验证账号身份</p>
        <div class="auth-tabs">
          <button
            v-for="tab in tabs"
            :key="tab.value"
            type="button"
            :class="{ active: activeTab === tab.value }"
            @click="switchTab(tab.value)"
          >{{ tab.label }}</button>
        </div>
        <form class="auth-form" @submit.prevent="submit">
          <label v-if="needsUsername">用户名<div class="input-with-icon"><UserRound :size="17" /><input v-model.trim="username" required maxlength="30" autocomplete="username" placeholder="请输入用户名" /></div></label>
          <label v-else>邮箱地址<div class="input-with-icon"><Mail :size="17" /><input v-model.trim="email" type="email" required maxlength="255" autocomplete="email" placeholder="name@example.com" /></div></label>
          <label v-if="needsPassword">密码<div class="input-with-icon"><LockKeyhole :size="17" /><PasswordInput v-model="password" input-class="with-left-icon" required autocomplete="current-password" placeholder="请输入密码" /></div></label>
          <label v-else>邮箱验证码<div class="code-row"><div class="input-with-icon"><KeyRound :size="17" /><input v-model.trim="emailCode" required inputmode="numeric" maxlength="6" placeholder="6位数字验证码" /></div><button class="button secondary code-button" type="button" :disabled="sending || countdown > 0" @click="sendCode"><span v-if="sending" class="button-spinner" />{{ countdown > 0 ? `${countdown}秒` : '发送验证码' }}</button></div></label>
          <label>滑动验证<GeetestCaptcha :key="captchaKey" :disabled="loading" @success="geetest = $event" /></label>
          <button class="button primary button-wide" type="submit" :disabled="loading"><span v-if="loading" class="button-spinner" />登录</button>
        </form>
        <p class="auth-switch">还没有账号？<RouterLink to="/register">立即注册</RouterLink> · <RouterLink to="/">返回插件库首页</RouterLink></p>
      </div>
      <footer class="auth-footer">PlugHub 插件库 · 插件分发与审核平台</footer>
    </section>
    <LoadingOverlay :visible="loading" text="正在登录…" />
  </div>
</template>
