<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { KeyRound, Mail, Save, ShieldCheck, UserRound } from 'lucide-vue-next'
import { authApi } from '../api'
import GeetestCaptcha from '../components/common/GeetestCaptcha.vue'
import PasswordInput from '../components/common/PasswordInput.vue'
import { useAuth } from '../composables/useAuth'
import { useCountdown } from '../composables/useCountdown'
import { useToast } from '../composables/useToast'
import type { GeetestResult } from '../types'

type Tab = 'profile' | 'email' | 'password'
const router = useRouter()
const auth = useAuth()
const toast = useToast()
const activeTab = ref<Tab>('profile')
const profile = reactive({ nickname: auth.state.user?.nickname || '' })
const password = reactive({ current: '', next: '', confirm: '' })
const email = ref(auth.state.user?.email || '')
const emailCode = ref('')
const geetest = ref<GeetestResult | null>(null)
const captchaKey = ref(0)
const loading = ref(false)
const sending = ref(false)
const { countdown, startCountdown } = useCountdown()
const tabs = [
  { value: 'profile' as const, label: '个人资料', icon: UserRound },
  { value: 'email' as const, label: '邮箱设置', icon: Mail },
  { value: 'password' as const, label: '修改密码', icon: KeyRound },
]

function resetCaptcha() {
  geetest.value = null
  captchaKey.value += 1
}

async function saveProfile() {
  loading.value = true
  const result = await authApi.updateProfile(profile.nickname)
  loading.value = false
  if (!result.success) return toast.show(result.message, 'error')
  auth.updateUser(result.data)
  toast.show(result.message, 'success')
}

async function sendEmailCode() {
  if (!email.value) return toast.show('请输入邮箱地址', 'warning')
  if (!geetest.value) return toast.show('请先完成滑动验证', 'warning')
  sending.value = true
  const result = await authApi.sendEmailCode(email.value, 'bind', geetest.value.challenge)
  sending.value = false
  if (!result.success) {
    toast.show(result.message, 'error')
    resetCaptcha()
    return
  }
  toast.show(result.message, 'success')
  startCountdown(60)
}

async function bindEmail() {
  if (!geetest.value || !emailCode.value) return toast.show('请完成滑动验证并输入邮箱验证码', 'warning')
  loading.value = true
  const result = await authApi.bindEmail(email.value, emailCode.value, geetest.value.challenge)
  loading.value = false
  if (!result.success) return toast.show(result.message, 'error')
  auth.updateUser(result.data)
  emailCode.value = ''
  toast.show(result.message, 'success')
}

async function savePassword() {
  if (password.next !== password.confirm) return toast.show('两次输入的新密码不一致', 'warning')
  loading.value = true
  const result = await authApi.updatePassword(password.current, password.next)
  loading.value = false
  if (!result.success) return toast.show(result.message, 'error')
  auth.clearSession()
  toast.show('密码修改成功，请重新登录', 'success')
  router.replace('/login')
}
</script>

<template>
  <div class="page-enter profile-page">
    <div class="page-heading"><div><p class="eyebrow">ACCOUNT</p><h1>个人设置</h1><p>分别管理个人资料、邮箱身份和登录密码。</p></div></div>
    <div class="inner-tabs" role="tablist">
      <button v-for="tab in tabs" :key="tab.value" type="button" :class="{ active: activeTab === tab.value }" @click="activeTab = tab.value"><component :is="tab.icon" :size="17" />{{ tab.label }}</button>
    </div>

    <section v-if="activeTab === 'profile'" class="content-panel settings-module">
      <div class="panel-heading"><div><h2><UserRound :size="18" />个人资料</h2><p>昵称更新后会立即显示在顶部用户菜单。</p></div></div>
      <form class="settings-form" @submit.prevent="saveProfile">
        <div class="form-grid"><label>用户名<input :value="auth.state.user?.username" disabled /></label><label>用户角色<input :value="auth.state.user?.role_name" disabled /></label></div>
        <label>昵称<input v-model.trim="profile.nickname" required maxlength="50" /></label>
        <div class="account-meta"><span>注册时间：{{ auth.state.user?.created_at }}</span><span>最近登录：{{ auth.state.user?.last_login_at || '暂无记录' }}</span></div>
        <div class="form-actions"><button class="button primary" type="submit" :disabled="loading"><span v-if="loading" class="button-spinner" /><Save :size="16" />保存资料</button></div>
      </form>
    </section>

    <section v-else-if="activeTab === 'email'" class="content-panel settings-module">
      <div class="panel-heading"><div><h2><Mail :size="18" />邮箱设置</h2><p>绑定后可使用邮箱密码或邮箱验证码登录。</p></div><span v-if="auth.state.user?.email_verified" class="status-badge enabled"><ShieldCheck :size="13" />已验证</span></div>
      <form class="settings-form" @submit.prevent="bindEmail">
        <label>邮箱地址<input v-model.trim="email" type="email" required maxlength="255" placeholder="name@example.com" /></label>
        <label>滑动验证<GeetestCaptcha :key="captchaKey" :disabled="loading" @success="geetest = $event" /></label>
        <label>邮箱验证码<div class="code-row"><div class="input-with-icon"><KeyRound :size="17" /><input v-model.trim="emailCode" required inputmode="numeric" maxlength="6" placeholder="6位数字验证码" /></div><button class="button secondary code-button" type="button" :disabled="sending || countdown > 0" @click="sendEmailCode"><span v-if="sending" class="button-spinner" />{{ countdown > 0 ? `${countdown}秒` : '发送验证码' }}</button></div></label>
        <div class="form-actions"><button class="button primary" type="submit" :disabled="loading"><span v-if="loading" class="button-spinner" /><Save :size="16" />绑定邮箱</button></div>
      </form>
    </section>

    <section v-else class="content-panel settings-module">
      <div class="panel-heading"><div><h2><KeyRound :size="18" />修改密码</h2><p>修改成功后会撤销全部登录会话。</p></div></div>
      <form class="settings-form" @submit.prevent="savePassword">
        <label>当前密码<PasswordInput v-model="password.current" autocomplete="current-password" placeholder="请输入当前密码" /></label>
        <div class="form-grid"><label>新密码<PasswordInput v-model="password.next" autocomplete="new-password" placeholder="至少8位，包含字母和数字" /></label><label>确认新密码<PasswordInput v-model="password.confirm" autocomplete="new-password" placeholder="请再次输入新密码" /></label></div>
        <div class="form-actions"><button class="button primary" type="submit" :disabled="loading"><span v-if="loading" class="button-spinner" /><Save :size="16" />更新密码</button></div>
      </form>
    </section>
  </div>
</template>
