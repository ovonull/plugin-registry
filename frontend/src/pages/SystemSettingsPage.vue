<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { Mail, Palette, Save, TextCursorInput, Github } from 'lucide-vue-next'
import { settingsApi } from '../api'
import LoadingOverlay from '../components/common/LoadingOverlay.vue'
import PasswordInput from '../components/common/PasswordInput.vue'
import { useSystemSettings } from '../composables/useSystemSettings'
import { themeColors, themeFonts, useTheme } from '../composables/useTheme'
import { useToast } from '../composables/useToast'
import type { AppearanceSettings, EmailSettings, GithubSettings, IntroductionSettings } from '../types'

type Tab = 'email' | 'appearance' | 'introduction' | 'github'
const activeTab = ref<Tab>('email')
const loaded = reactive<Record<Tab, boolean>>({ email: false, appearance: false, introduction: false, github: false })
const loading = ref(false)
const saving = ref(false)
const toast = useToast()
const theme = useTheme()
const system = useSystemSettings()
const email = reactive<EmailSettings>({ host: '', port: 465, username: '', password: '', sender_name: 'NapCatQQ 插件库', use_ssl: true })
const appearance = reactive<AppearanceSettings>({ color: 'ocean', font: 'system', effect: 'solid' })
const introduction = reactive<IntroductionSettings>({ name: 'NapCatQQ 插件库', description: 'NapCatQQ 插件分发与审核平台' })
const github = reactive<GithubSettings>({ plugin_repo_token: '' })
const tabs = [
  { value: 'email' as const, label: '发件人设置', icon: Mail },
  { value: 'appearance' as const, label: '主题与字体', icon: Palette },
  { value: 'introduction' as const, label: '系统介绍', icon: TextCursorInput },
  { value: 'github' as const, label: 'GitHub 接口', icon: Github },
]

async function loadGroup(group: Tab) {
  if (loaded[group]) return
  loading.value = true
  const result = await settingsApi.group<Record<string, unknown>>(group)
  loading.value = false
  if (!result.success || !result.data) {
    toast.show(result.message, 'error')
    return
  }
  if (group === 'email') Object.assign(email, result.data)
  if (group === 'appearance') Object.assign(appearance, result.data)
  if (group === 'introduction') Object.assign(introduction, result.data)
  if (group === 'github') Object.assign(github, result.data)
  loaded[group] = true
}

function switchTab(tab: Tab) {
  activeTab.value = tab
  loadGroup(tab)
}

async function saveEmail() {
  saving.value = true
  const result = await settingsApi.saveEmail(email)
  saving.value = false
  if (!result.success) return toast.show(result.message, 'error')
  Object.assign(email, result.data)
  toast.show(result.message, 'success')
}

async function saveAppearance() {
  saving.value = true
  const result = await settingsApi.saveAppearance(appearance)
  saving.value = false
  if (!result.success) return toast.show(result.message, 'error')
  Object.assign(appearance, result.data)
  Object.assign(system.state.appearance, result.data)
  theme.applySystemAppearance(appearance.color, appearance.font, appearance.effect)
  toast.show(result.message, 'success')
}

async function saveIntroduction() {
  saving.value = true
  const result = await settingsApi.saveIntroduction(introduction)
  saving.value = false
  if (!result.success) return toast.show(result.message, 'error')
  Object.assign(introduction, result.data)
  Object.assign(system.state.introduction, result.data)
  document.title = introduction.name
  toast.show(result.message, 'success')
}

async function saveGithub() {
  saving.value = true
  const result = await settingsApi.saveGithub(github)
  saving.value = false
  if (!result.success) return toast.show(result.message, 'error')
  Object.assign(github, result.data)
  toast.show(result.message, 'success')
}

onMounted(() => loadGroup('email'))
</script>

<template>
  <div class="page-enter settings-page">
    <div class="page-heading"><div><p class="eyebrow">SYSTEM</p><h1>系统设置</h1><p>管理系统发信、默认外观和对外展示内容。</p></div></div>
    <div class="inner-tabs" role="tablist">
      <button v-for="tab in tabs" :key="tab.value" type="button" :class="{ active: activeTab === tab.value }" @click="switchTab(tab.value)"><component :is="tab.icon" :size="17" />{{ tab.label }}</button>
    </div>

    <section v-if="activeTab === 'email'" class="content-panel settings-module">
      <div class="panel-heading"><div><h2><Mail :size="18" />SMTP 发件人设置</h2><p>用于发送注册、登录和邮箱绑定验证码。</p></div></div>
      <form class="settings-form" @submit.prevent="saveEmail">
        <div class="form-grid"><label>SMTP 服务器<input v-model.trim="email.host" required maxlength="255" placeholder="smtp.example.com" /></label><label>SMTP 端口<input v-model.number="email.port" type="number" required min="1" max="65535" /></label></div>
        <div class="form-grid"><label>SMTP 账号<input v-model.trim="email.username" required maxlength="255" placeholder="name@example.com" /></label><label>SMTP 密码 / 授权码<PasswordInput v-model="email.password" placeholder="留空或保留掩码表示不修改" /></label></div>
        <div class="form-grid"><label>发件人名称<input v-model.trim="email.sender_name" required maxlength="128" /></label><label class="toggle-field"><span><strong>SSL 加密</strong><small>关闭时使用 STARTTLS</small></span><button class="switch" :class="{ active: email.use_ssl }" type="button" role="switch" :aria-checked="email.use_ssl" @click="email.use_ssl = !email.use_ssl"><i /></button></label></div>
        <div class="form-actions"><button class="button primary" type="submit" :disabled="saving"><span v-if="saving" class="button-spinner" /><Save :size="16" />保存发件人设置</button></div>
      </form>
      <LoadingOverlay :visible="loading" contained />
    </section>

    <section v-else-if="activeTab === 'appearance'" class="content-panel settings-module">
      <div class="panel-heading"><div><h2><Palette :size="18" />默认主题与字体</h2><p>设置所有访问者加载系统时使用的默认界面风格。</p></div></div>
      <form class="settings-form" @submit.prevent="saveAppearance">
        <label>主题颜色<div class="color-options"><button v-for="color in themeColors" :key="color.code" type="button" :class="{ active: appearance.color === color.code }" :title="color.name" @click="appearance.color = color.code"><i :style="{ background: color.value }" /><span>{{ color.name }}</span></button></div></label>
        <div class="form-grid"><label>系统字体<select v-model="appearance.font"><option v-for="font in themeFonts" :key="font.value" :value="font.value">{{ font.name }}</option></select></label><label>顶栏效果<select v-model="appearance.effect"><option value="solid">纯色</option><option value="gradient">轻渐变</option></select></label></div>
        <div class="form-actions"><button class="button primary" type="submit" :disabled="saving"><span v-if="saving" class="button-spinner" /><Save :size="16" />保存界面设置</button></div>
      </form>
      <LoadingOverlay :visible="loading" contained />
    </section>

    <section v-else-if="activeTab === 'github'" class="content-panel settings-module">
      <div class="panel-heading"><div><h2><Github :size="18" />GitHub 接口设置</h2><p>用于探测代码仓库版本，配置后可提升 GitHub API 调用额度。</p></div></div>
      <form class="settings-form" @submit.prevent="saveGithub">
        <label>GitHub Personal Access Token<PasswordInput v-model="github.plugin_repo_token" placeholder="github_pat_..." /><small>建议使用 Fine-grained token，仅授予仓库 Metadata 和 Contents 的 Read-only 权限。留空并保存可清除配置。</small></label>
        <div class="form-actions"><button class="button primary" type="submit" :disabled="saving"><span v-if="saving" class="button-spinner" /><Save :size="16" />保存 GitHub 设置</button></div>
      </form>
      <LoadingOverlay :visible="loading" contained />
    </section>

    <section v-else class="content-panel settings-module">
      <div class="panel-heading"><div><h2><TextCursorInput :size="18" />系统介绍</h2><p>内容会显示在登录与注册页面。</p></div></div>
      <form class="settings-form" @submit.prevent="saveIntroduction">
        <label>系统名称<input v-model.trim="introduction.name" required maxlength="64" /></label>
        <label>系统说明<textarea v-model.trim="introduction.description" required maxlength="500" rows="5" /></label>
        <div class="form-actions"><button class="button primary" type="submit" :disabled="saving"><span v-if="saving" class="button-spinner" /><Save :size="16" />保存系统介绍</button></div>
      </form>
      <LoadingOverlay :visible="loading" contained />
    </section>
  </div>
</template>
