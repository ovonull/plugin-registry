<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { Copy, Mail, Palette, Save, TextCursorInput, Github, Plus, RefreshCcw, KeyRound, Trash2 } from 'lucide-vue-next'
import { pluginAdminApi, settingsApi } from '../api'
import BaseModal from '../components/common/BaseModal.vue'
import LoadingOverlay from '../components/common/LoadingOverlay.vue'
import PasswordInput from '../components/common/PasswordInput.vue'
import { useClipboard } from '../composables/useClipboard'
import { useSystemSettings } from '../composables/useSystemSettings'
import { themeColors, themeFonts, useTheme } from '../composables/useTheme'
import { useToast } from '../composables/useToast'
import type {
  AppearanceSettings,
  EmailSettings,
  GithubSettings,
  IntroductionSettings,
  PluginSyncSettings,
  RegistryTokenItem,
} from '../types'

type Tab = 'email' | 'appearance' | 'introduction' | 'github' | 'plugin_sync' | 'registry'
const activeTab = ref<Tab>('email')
const loaded = reactive<Record<Tab, boolean>>({
  email: false, appearance: false, introduction: false, github: false, plugin_sync: false, registry: false,
})
const loading = ref(false)
const saving = ref(false)
const toast = useToast()
const theme = useTheme()
const system = useSystemSettings()
const email = reactive<EmailSettings>({ host: '', port: 465, username: '', password: '', sender_name: 'PlugHub 插件库', use_ssl: true })
const appearance = reactive<AppearanceSettings>({ color: 'ocean', font: 'system', effect: 'solid' })
const introduction = reactive<IntroductionSettings>({ name: 'PlugHub 插件库', description: 'PlugHub 插件分发与审核平台' })
const github = reactive<GithubSettings>({ plugin_repo_token: '' })
const pluginSync = reactive<PluginSyncSettings>({ enabled: true, interval_seconds: 21600, batch: 20 })

// 界面上按分钟编辑更直观：后端硬下限是 300 秒，落库仍存秒。
const intervalMinutes = computed({
  get: () => Math.max(5, Math.round(pluginSync.interval_seconds / 60)),
  set: (value: number) => {
    const minutes = Math.min(10080, Math.max(5, Math.round(Number(value) || 5)))
    pluginSync.interval_seconds = minutes * 60
  },
})

// 手动触发一轮同步：改完间隔想立刻验证有没有新 tag 时用。
const syncing = ref(false)
const lastSyncAt = ref('')

function formatSyncTime(value: string): string {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleString('zh-CN', { hour12: false })
}

async function loadSyncStatus() {
  const response = await pluginAdminApi.syncStatus()
  if (response.success && response.data) {
    lastSyncAt.value = formatSyncTime(response.data.last_run_at)
  }
}

async function triggerSync() {
  syncing.value = true
  const response = await pluginAdminApi.syncRepos()
  syncing.value = false
  if (!response.success) return toast.show(response.message, 'error')
  toast.show(response.message, 'success')
  loadSyncStatus()
}
const tabs = [
  { value: 'email' as const, label: '发件人设置', icon: Mail },
  { value: 'appearance' as const, label: '主题与字体', icon: Palette },
  { value: 'introduction' as const, label: '系统介绍', icon: TextCursorInput },
  { value: 'plugin_sync' as const, label: '插件同步', icon: RefreshCcw },
  { value: 'registry' as const, label: '接入令牌', icon: KeyRound },
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
  if (group === 'plugin_sync') {
    Object.assign(pluginSync, result.data)
    loadSyncStatus()
  }
  if (group === 'registry') loadTokens()
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

// 小眼睛点开时才解密一次，避免明文随页面一起下发。
async function revealSecret(group: string, field: string): Promise<string> {
  const result = await settingsApi.revealGroup(group)
  if (!result.success) {
    toast.show(result.message, 'error')
    return ''
  }
  const value = (result.data as Record<string, unknown>)[field]
  return value === undefined || value === null ? '' : String(value)
}

async function saveGithub() {
  saving.value = true
  const result = await settingsApi.saveGithub(github)
  saving.value = false
  if (!result.success) return toast.show(result.message, 'error')
  Object.assign(github, result.data)
  toast.show(result.message, 'success')
}

async function savePluginSync() {
  saving.value = true
  const result = await settingsApi.savePluginSync(pluginSync)
  saving.value = false
  if (!result.success) return toast.show(result.message, 'error')
  Object.assign(pluginSync, result.data)
  toast.show(result.message, 'success')
}

/** 复制文本；clipboard API 需要安全上下文，http + IP 访问时退回 execCommand。 */
const { copyText } = useClipboard()

// ---------------------------------------------------------------- 接入令牌

const tokens = ref<RegistryTokenItem[]>([])
const tokensLoading = ref(false)
const createOpen = ref(false)
const createName = ref('')
const creating = ref(false)
/** 刚创建出来的明文令牌，只在这个弹窗里显示一次。 */
const createdToken = ref('')

function formatTokenTime(value: string): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleString('zh-CN', { hour12: false })
}

async function loadTokens() {
  tokensLoading.value = true
  const result = await settingsApi.listRegistryTokens()
  tokensLoading.value = false
  if (!result.success) return toast.show(result.message, 'error')
  tokens.value = result.data?.items || []
}

function openCreateToken() {
  createName.value = ''
  createdToken.value = ''
  createOpen.value = true
}

async function createToken() {
  creating.value = true
  const result = await settingsApi.createRegistryToken({ name: createName.value.trim() })
  creating.value = false
  if (!result.success) return toast.show(result.message, 'error')
  createdToken.value = result.data?.token || ''
  await loadTokens()
}

async function revokeToken(item: RegistryTokenItem) {
  const result = await settingsApi.revokeRegistryToken(item.id)
  if (!result.success) return toast.show(result.message, 'error')
  toast.show(result.message, 'success')
  await loadTokens()
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
        <div class="form-grid"><label>SMTP 账号<input v-model.trim="email.username" required maxlength="255" placeholder="name@example.com" /></label><label>SMTP 密码 / 授权码<PasswordInput v-model="email.password" placeholder="留空或保留掩码表示不修改" :reveal="() => revealSecret('email', 'password')" /></label></div>
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

    <section v-else-if="activeTab === 'plugin_sync'" class="content-panel settings-module">
      <div class="panel-heading"><div><h2><RefreshCcw :size="18" />插件自动同步</h2><p>定时检查代码仓库来源的插件，作者打新 tag 后自动收进插件库。</p></div></div>
      <form class="settings-form" @submit.prevent="savePluginSync">
        <label class="toggle-field"><span><strong>启用自动同步</strong><small>关闭后仍可在插件审核台手动点「同步仓库最新版」</small></span><button class="switch" :class="{ active: pluginSync.enabled }" type="button" role="switch" :aria-checked="pluginSync.enabled" @click="pluginSync.enabled = !pluginSync.enabled"><i /></button></label>
        <div class="form-grid">
          <label>同步间隔<input v-model.number="intervalMinutes" type="number" min="5" max="10080" step="5" /><small>单位分钟，最短 5 分钟，默认 360 分钟（6 小时），保存后 30 秒内生效</small></label>
          <label>单轮上限<input v-model.number="pluginSync.batch" type="number" min="1" max="100" /><small>每轮最多检查多少个仓库，超出会按轮转依次覆盖</small></label>
        </div>
        <div class="form-actions-inline">
          <button type="button" class="button secondary" :disabled="syncing" @click="triggerSync">
            <span v-if="syncing" class="button-spinner" /><RefreshCcw :size="15" />立即同步一次
          </button>
          <span class="muted-text">{{ lastSyncAt ? `上次同步：${lastSyncAt}` : '尚未执行过同步' }}</span>
        </div>
        <p class="form-hint">轮询间隔只影响「多久检查一轮」；单轮上限小于插件总数时，每个仓库实际是「间隔 × 轮数」才轮到一次。想立刻拉取刚推的 tag，用上面的「立即同步一次」。</p>
        <p class="form-hint">审核规则：插件首次入库需人工审核；已有审核通过的版本后，新版本自动免审上架；信任名单作者与管理员免审直接上架；被管理员下架过的插件只免审通过，不会自动重新上架。</p>
        <div class="form-actions"><button class="button primary" type="submit" :disabled="saving"><span v-if="saving" class="button-spinner" /><Save :size="16" />保存同步设置</button></div>
      </form>
      <LoadingOverlay :visible="loading" contained />
    </section>

    <section v-else-if="activeTab === 'registry'" class="content-panel settings-module">
      <div class="panel-heading">
        <div><h2><KeyRound :size="18" />插件库接入令牌</h2><p>索引、插件列表与插件包下载都需要鉴权，下游程序靠这里的令牌访问。</p></div>
        <button type="button" class="button primary" @click="openCreateToken"><Plus :size="16" />创建令牌</button>
      </div>

      <div class="token-table">
        <div class="token-table-head">
          <span>名称</span><span>Key</span><span>创建时间</span><span>最后使用</span><span>操作</span>
        </div>
        <div v-for="item in tokens" :key="item.id" class="token-table-row">
          <span class="token-name">{{ item.name || '未命名令牌' }}</span>
          <code>{{ item.prefix }}</code>
          <span>{{ formatTokenTime(item.created_at) }}</span>
          <span :class="{ 'token-idle': !item.last_used_at }">{{ item.last_used_at ? formatTokenTime(item.last_used_at) : '从未使用' }}</span>
          <button type="button" class="token-revoke" title="吊销令牌" @click="revokeToken(item)">
            <Trash2 :size="15" />
          </button>
        </div>
        <p v-if="tokensLoading" class="token-empty">加载中…</p>
        <p v-else-if="!tokens.length" class="token-empty">还没有令牌，点右上角创建一枚发给下游程序。</p>
      </div>

      <p class="form-hint">
        明文只在创建时显示一次，库里只保存摘要，之后无法再查看；泄露了就吊销重新创建。
        请求时带上 <code>Authorization: Bearer &lt;令牌&gt;</code> 或 <code>X-Registry-Token</code>。
        已登录用户访问首页走账号会话，不消耗令牌。
      </p>
      <LoadingOverlay :visible="loading" contained />
    </section>

    <section v-else-if="activeTab === 'github'" class="content-panel settings-module">
      <div class="panel-heading"><div><h2><Github :size="18" />GitHub 接口设置</h2><p>用于探测代码仓库版本，配置后可提升 GitHub API 调用额度。</p></div></div>
      <form class="settings-form" @submit.prevent="saveGithub">
        <label>GitHub Personal Access Token<PasswordInput v-model="github.plugin_repo_token" placeholder="github_pat_..." :reveal="() => revealSecret('github', 'plugin_repo_token')" /><small>建议使用 Fine-grained token，仅授予仓库 Metadata 和 Contents 的 Read-only 权限。留空保存表示保持原值不变，点右侧小眼睛可查看已保存的令牌。</small></label>
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

    <BaseModal :open="createOpen" title="创建接入令牌" @close="createOpen = false">
      <div v-if="!createdToken" class="modal-form">
        <label class="field-label">用途标注
          <input v-model="createName" maxlength="100" placeholder="例如：NapCatQQ Service 生产" />
        </label>
        <p class="form-hint">标注只用于区分是谁在用，方便以后单独吊销。</p>
        <div class="modal-form-actions">
          <button type="button" class="button secondary" @click="createOpen = false">取消</button>
          <button type="button" class="button primary" :disabled="creating" @click="createToken">
            <span v-if="creating" class="button-spinner" />创建
          </button>
        </div>
      </div>
      <div v-else class="modal-form">
        <p class="form-hint">这枚令牌只显示这一次，请立刻复制保存。</p>
        <div class="token-reveal">
          <code>{{ createdToken }}</code>
        </div>
        <div class="modal-form-actions">
          <button type="button" class="button secondary" @click="copyText(createdToken, '令牌已复制')"><Copy :size="15" />复制</button>
          <button type="button" class="button primary" @click="createOpen = false">我已保存</button>
        </div>
      </div>
    </BaseModal>
  </div>
</template>
