<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { KeyRound, Mail, UserRound } from 'lucide-vue-next'
import { authApi } from '../api'
import GeetestCaptcha from '../components/common/GeetestCaptcha.vue'
import LoadingOverlay from '../components/common/LoadingOverlay.vue'
import PasswordInput from '../components/common/PasswordInput.vue'
import { useAuth } from '../composables/useAuth'
import { useCountdown } from '../composables/useCountdown'
import { useSystemSettings } from '../composables/useSystemSettings'
import { useToast } from '../composables/useToast'
import type { GeetestResult } from '../types'

const router = useRouter()
const auth = useAuth()
const system = useSystemSettings()
const toast = useToast()
const username = ref('')
const nickname = ref('')
const email = ref('')
const password = ref('')
const confirmPassword = ref('')
const emailCode = ref('')
const geetest = ref<GeetestResult | null>(null)
const captchaKey = ref(0)
const loading = ref(false)
const sending = ref(false)
const { countdown, startCountdown } = useCountdown()

function resetCaptcha() {
  geetest.value = null
  captchaKey.value += 1
}

function validateBase(): boolean {
  if (!username.value || !email.value || !password.value) {
    toast.show('请完整填写注册信息', 'warning')
    return false
  }
  if (!/^[A-Za-z0-9_]{3,30}$/.test(username.value)) {
    toast.show('用户名只能包含字母、数字和下划线，至少3位', 'warning')
    return false
  }
  if (password.value.length < 8 || !/[A-Za-z]/.test(password.value) || !/\d/.test(password.value)) {
    toast.show('密码至少8位，并同时包含字母和数字', 'warning')
    return false
  }
  if (password.value !== confirmPassword.value) {
    toast.show('两次输入的密码不一致', 'warning')
    return false
  }
  return true
}

async function sendCode() {
  if (!email.value) return toast.show('请输入邮箱地址', 'warning')
  if (!geetest.value) return toast.show('请先完成滑动验证', 'warning')
  sending.value = true
  const result = await authApi.sendEmailCode(email.value, 'register', geetest.value.challenge)
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
  if (!validateBase()) return
  if (!emailCode.value) return toast.show('请输入邮箱验证码', 'warning')
  if (!geetest.value) return toast.show('请先完成滑动验证', 'warning')
  loading.value = true
  const result = await authApi.register({
    username: username.value,
    nickname: nickname.value || undefined,
    email: email.value,
    password: password.value,
    email_code: emailCode.value,
    geetest_challenge: geetest.value.challenge,
  })
  loading.value = false
  if (!result.success || !result.data) {
    toast.show(result.message, 'error')
    return
  }
  auth.setSession(result.data.token, result.data.user)
  toast.show('注册成功', 'success')
  router.replace('/dashboard')
}

system.loadPublic()
</script>

<template>
  <div class="auth-page">
    <section class="auth-brand">
      <div class="brand-mark"><img src="/napcat-logo.png" alt="PlugHub" /></div>
      <div>
        <p class="eyebrow">PLUGHUB</p>
        <h1>{{ system.state.introduction.name }}</h1>
        <p class="brand-description">{{ system.state.introduction.description }}</p>
      </div>
      <div class="brand-line" />
      <p class="brand-footnote">邮箱验证 · 安全注册 · 独立数据空间</p>
    </section>
    <section class="auth-panel register-panel">
      <div class="auth-form-wrap">
        <div class="mobile-auth-logo"><img src="/napcat-logo.png" alt="PlugHub" /></div>
        <p class="eyebrow">创建账号</p>
        <h2>注册普通用户</h2>
        <p class="auth-subtitle">完成邮箱验证后即可进入管理后台</p>
        <form class="auth-form compact-auth-form" @submit.prevent="submit">
          <div class="auth-field-grid">
            <label>用户名<div class="input-with-icon"><UserRound :size="17" /><input v-model.trim="username" required maxlength="30" autocomplete="username" placeholder="字母、数字或下划线" /></div></label>
            <label>昵称（选填）<input v-model.trim="nickname" maxlength="50" placeholder="默认使用用户名" /></label>
          </div>
          <label>邮箱地址<div class="input-with-icon"><Mail :size="17" /><input v-model.trim="email" type="email" required maxlength="255" autocomplete="email" placeholder="name@example.com" /></div></label>
          <div class="auth-field-grid">
            <label>登录密码<PasswordInput v-model="password" autocomplete="new-password" placeholder="至少8位，包含字母和数字" /></label>
            <label>确认密码<PasswordInput v-model="confirmPassword" autocomplete="new-password" placeholder="请再次输入密码" /></label>
          </div>
          <label>滑动验证<GeetestCaptcha :key="captchaKey" :disabled="loading" @success="geetest = $event" /></label>
          <label>邮箱验证码<div class="code-row"><div class="input-with-icon"><KeyRound :size="17" /><input v-model.trim="emailCode" required inputmode="numeric" maxlength="6" placeholder="6位数字验证码" /></div><button class="button secondary code-button" type="button" :disabled="sending || countdown > 0" @click="sendCode"><span v-if="sending" class="button-spinner" />{{ countdown > 0 ? `${countdown}秒` : '发送验证码' }}</button></div></label>
          <button class="button primary button-wide" type="submit" :disabled="loading"><span v-if="loading" class="button-spinner" />注册并登录</button>
        </form>
        <p class="auth-switch">已有账号？<RouterLink to="/login">返回登录</RouterLink></p>
      </div>
      <footer class="auth-footer">PlugHub 插件库 · 插件分发与审核平台</footer>
    </section>
    <LoadingOverlay :visible="loading" text="正在创建账号…" />
  </div>
</template>
