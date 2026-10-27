<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  Copy,
  KeyRound,
  KeySquare,
  Link2,
  Mail,
  Plus,
  Save,
  ShieldCheck,
  Trash2,
  UserRound,
} from 'lucide-vue-next'
import { authApi, profileApi } from '../api'
import BaseModal from '../components/common/BaseModal.vue'
import GeetestCaptcha from '../components/common/GeetestCaptcha.vue'
import PasswordInput from '../components/common/PasswordInput.vue'
import { useAuth } from '../composables/useAuth'
import { useClipboard } from '../composables/useClipboard'
import { useCountdown } from '../composables/useCountdown'
import { useToast } from '../composables/useToast'
import type { GeetestResult, RegistryTokenItem } from '../types'

type Tab = 'profile' | 'email' | 'token' | 'password'
const router = useRouter()
const auth = useAuth()
const toast = useToast()
const { copyText } = useClipboard()
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
  { value: 'token' as const, label: '接入令牌', icon: KeySquare },
  { value: 'password' as const, label: '修改密码', icon: KeyRound },
]

function resetCaptcha() {
  geetest.value = null
  captchaKey.value += 1
}

/* ------------------------------------------------------------ 接入令牌 */

const tokens = ref<RegistryTokenItem[]>([])
const tokensLoading = ref(false)
const tokenModalOpen = ref(false)
const tokenName = ref('')
const tokenCreating = ref(false)
/** 刚创建出来的明文令牌，只在弹窗里显示一次。 */
const createdToken = ref('')
/** 后端给出的对外服务基址；没配置 PUBLIC_SERVICE_URL 时可能为空。 */
const tokenEndpoint = ref('')

// 兜底用当前站点域名：前端 nginx 会把 /api 反代给后端，所以站点域名一定调得通。
const apiBase = computed(() => tokenEndpoint.value || window.location.origin)
const apiIndexUrl = computed(() => `${apiBase.value}/api/registry/index.json`)
const apiPackageUrl = computed(
  () => `${apiBase.value}/api/registry/packages/{plugin_id}/{version}.zip`,
)

function formatTokenTime(value: string): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleString('zh-CN', { hour12: false })
}

async function loadTokens() {
  tokensLoading.value = true
  const result = await profileApi.listRegistryTokens()
  tokensLoading.value = false
  if (!result.success) return toast.show(result.message, 'error')
  tokens.value = result.data?.items || []
  tokenEndpoint.value = result.data?.endpoint || ''
}

function switchTab(tab: Tab) {
  activeTab.value = tab
  // 令牌列表按需加载，别在打开个人资料页时就多发一次请求。
  if (tab === 'token') loadTokens()
}

function openTokenModal() {
  tokenName.value = ''
  createdToken.value = ''
  tokenModalOpen.value = true
}

async function createToken() {
  tokenCreating.value = true
  const result = await profileApi.createRegistryToken({ name: tokenName.value.trim() })
  tokenCreating.value = false
  if (!result.success) return toast.show(result.message, 'error')
  createdToken.value = result.data?.token || ''
  await loadTokens()
}

async function revokeToken(item: RegistryTokenItem) {
  const result = await profileApi.revokeRegistryToken(item.id)
  if (!result.success) return toast.show(result.message, 'error')
  toast.show(result.message, 'success')
  await loadTokens()
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
    <div class="page-heading"><div><p class="eyebrow">ACCOUNT</p><h1>个人设置</h1><p>分别管理个人资料、邮箱身份、接入令牌和登录密码。</p></div></div>
    <div class="inner-tabs" role="tablist">
      <button v-for="tab in tabs" :key="tab.value" type="button" :class="{ active: activeTab === tab.value }" @click="switchTab(tab.value)"><component :is="tab.icon" :size="17" />{{ tab.label }}</button>
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

    <div v-else-if="activeTab === 'token'" class="token-stack">
      <!-- 卡片一：服务地址。根地址与 QQ 框架「插件库对接」里填的格式一致 -->
      <section class="content-panel settings-module">
        <div class="panel-heading">
          <div><h2><Link2 :size="18" />服务地址</h2><p>下游程序接入插件库分发接口要用的地址，复制过去即可。</p></div>
        </div>
        <div class="panel-body">
          <div class="api-endpoint">
            <div class="api-endpoint-row">
              <span class="api-endpoint-label"><Link2 :size="14" />插件库地址</span>
              <code>{{ apiBase }}</code>
              <button type="button" class="api-endpoint-copy" title="复制插件库地址" @click="copyText(apiBase, '插件库地址已复制')"><Copy :size="15" /></button>
            </div>
            <div class="api-endpoint-row">
              <span class="api-endpoint-label">API 调用地址</span>
              <code>{{ apiIndexUrl }}</code>
              <button type="button" class="api-endpoint-copy" title="复制调用地址" @click="copyText(apiIndexUrl, '调用地址已复制')"><Copy :size="15" /></button>
            </div>
            <div class="api-endpoint-row">
              <span class="api-endpoint-label">插件包地址</span>
              <code>{{ apiPackageUrl }}</code>
              <button type="button" class="api-endpoint-copy" title="复制插件包地址" @click="copyText(apiPackageUrl, '插件包地址已复制')"><Copy :size="15" /></button>
            </div>
          </div>
        </div>
      </section>

      <!-- 卡片二：接入令牌 -->
      <section class="content-panel settings-module">
        <div class="panel-heading">
          <div><h2><KeySquare :size="18" />接入令牌</h2><p>每个账号只看得到自己创建的令牌，明文只在创建时显示一次。</p></div>
          <button type="button" class="button primary" @click="openTokenModal"><Plus :size="16" />创建令牌</button>
        </div>
        <div class="panel-body">
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
            <p v-else-if="!tokens.length" class="token-empty">还没有令牌，点右上角创建一枚。</p>
          </div>
          <p class="form-hint">
            请求时带上 <code>Authorization: Bearer &lt;令牌&gt;</code> 或 <code>X-Registry-Token</code> 请求头。
            明文只在创建时显示一次，库里只保存摘要；泄露了就吊销重新创建。
          </p>
        </div>
      </section>
    </div>

    <section v-else class="content-panel settings-module">
      <div class="panel-heading"><div><h2><KeyRound :size="18" />修改密码</h2><p>修改成功后会撤销全部登录会话。</p></div></div>
      <form class="settings-form" @submit.prevent="savePassword">
        <label>当前密码<PasswordInput v-model="password.current" autocomplete="current-password" placeholder="请输入当前密码" /></label>
        <div class="form-grid"><label>新密码<PasswordInput v-model="password.next" autocomplete="new-password" placeholder="至少8位，包含字母和数字" /></label><label>确认新密码<PasswordInput v-model="password.confirm" autocomplete="new-password" placeholder="请再次输入新密码" /></label></div>
        <div class="form-actions"><button class="button primary" type="submit" :disabled="loading"><span v-if="loading" class="button-spinner" /><Save :size="16" />更新密码</button></div>
      </form>
    </section>

    <BaseModal :open="tokenModalOpen" title="创建接入令牌" @close="tokenModalOpen = false">
      <div v-if="!createdToken" class="modal-form">
        <label class="field-label">用途标注
          <input v-model="tokenName" maxlength="100" placeholder="例如：我的插件市场" />
        </label>
        <p class="form-hint">标注只用于区分是谁在用，方便以后单独吊销。</p>
        <div class="modal-form-actions">
          <button type="button" class="button secondary" @click="tokenModalOpen = false">取消</button>
          <button type="button" class="button primary" :disabled="tokenCreating" @click="createToken">
            <span v-if="tokenCreating" class="button-spinner" />创建
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
          <button type="button" class="button primary" @click="tokenModalOpen = false">我已保存</button>
        </div>
      </div>
    </BaseModal>
  </div>
</template>
