<script setup lang="ts">
import { onMounted, reactive, ref, watch } from 'vue'
import {
  Bot,
  BrainCircuit,
  Edit3,
  FlaskConical,
  Image as ImageIcon,
  ListChecks,
  Mic2,
  Plus,
  RefreshCw,
  RotateCw,
  Trash2,
  UsersRound,
} from 'lucide-vue-next'
import { botApi } from '../api'
import type {
  AiModelOption,
  AiModelTestResult,
  AiProviderOption,
  BotAccount,
  BotAiConfigPayload,
  BotGroup,
  BotGroupPayload,
  BotPayload,
} from '../types'
import BaseModal from '../components/common/BaseModal.vue'
import ConfirmDialog from '../components/common/ConfirmDialog.vue'
import EmptyState from '../components/common/EmptyState.vue'
import LoadingOverlay from '../components/common/LoadingOverlay.vue'
import PasswordInput from '../components/common/PasswordInput.vue'
import { useToast } from '../composables/useToast'
import { useAuth } from '../composables/useAuth'

const toast = useToast()
const auth = useAuth()
type AiModelType = 'text' | 'image' | 'generation'
const loading = ref(false)
const saving = ref(false)
const syncing = ref(false)
const aiLoading = ref(false)
const aiSaving = ref(false)
const aiModelsLoadingModule = ref<AiModelType | ''>('')
const bots = ref<BotAccount[]>([])
const groups = ref<BotGroup[]>([])
const editing = ref<BotAccount | null>(null)
const selectedBot = ref<BotAccount | null>(null)
const aiBot = ref<BotAccount | null>(null)
const deletingBot = ref<BotAccount | null>(null)
const deletingGroup = ref<BotGroup | null>(null)
const botModalOpen = ref(false)
const groupModalOpen = ref(false)
const aiModalOpen = ref(false)
const confirmOpen = ref(false)
const groupConfirmOpen = ref(false)

const botForm = reactive<BotPayload>({
  bot_qq: '', nickname: '', admin_qq: '', connection_mode: 'auto', http_url: '', access_token: '', group_mode: 'all', is_enabled: true, welcome_enabled: false, auto_approve_friend: false, auto_approve_group_request: false,
})
const groupForm = reactive<BotGroupPayload>({
  group_id: '', group_name: '', is_enabled: true,
})
const aiForm = reactive<BotAiConfigPayload>({
  is_enabled: false,
  provider_type: 'openai_compatible',
  base_url: '',
  api_key: '',
  model_name: 'qwen-plus',
  image_model_name: '',
  text_provider_type: 'openai_compatible',
  text_base_url: '',
  text_api_key: '',
  text_model_name: 'qwen-plus',
  image_provider_type: '',
  image_base_url: '',
  image_api_key: '',
  image_generation_provider_type: '',
  image_generation_base_url: '',
  image_generation_api_key: '',
  image_generation_model_name: '',
  system_prompt: '',
  message_intent_prompt: '',
  temperature: 0.7,
  max_tokens: 1000,
  followup_count: 3,
  history_limit: 10,
  voice_reply_enabled: false,
  voice_reply_probability: 20,
  image_reply_probability: 100,
  voice_character: 'lucy-voice-f38',
})
const aiProviders = ref<AiProviderOption[]>([])
const aiModels = reactive<Record<AiModelType, AiModelOption[]>>({
  text: [],
  image: [],
  generation: [],
})
const aiHasApiKey = ref(false)
const aiHasImageApiKey = ref(false)
const aiHasImageGenerationApiKey = ref(false)
const aiTestingModule = ref<AiModelType | ''>('')
const aiTestReplies = reactive<Record<AiModelType, string>>({ text: '', image: '', generation: '' })
const aiTestImages = reactive<Record<AiModelType, AiModelTestResult['image']>>({ text: null, image: null, generation: null })
const aiSectionOpen = reactive({ text: false, image: false, generation: false, intent: false })
const isAdmin = () => auth.state.user?.role_code === 'admin'

function resetBotForm() {
  Object.assign(botForm, { bot_qq: '', nickname: '', admin_qq: '', connection_mode: 'auto', http_url: '', access_token: '', group_mode: 'all', is_enabled: true, welcome_enabled: false, auto_approve_friend: false, auto_approve_group_request: false })
}

function openCreate() {
  editing.value = null
  resetBotForm()
  botModalOpen.value = true
}

function openEdit(bot: BotAccount) {
  editing.value = bot
  Object.assign(botForm, {
    bot_qq: bot.bot_qq,
    nickname: bot.nickname,
    admin_qq: bot.admin_qq || '',
    connection_mode: 'auto',
    http_url: bot.http_url,
    access_token: '',
    group_mode: bot.group_mode,
    is_enabled: bot.is_enabled,
    welcome_enabled: bot.welcome_enabled,
    auto_approve_friend: bot.auto_approve_friend,
    auto_approve_group_request: bot.auto_approve_group_request,
  })
  botModalOpen.value = true
}

async function loadBots() {
  loading.value = true
  const response = await botApi.list()
  loading.value = false
  if (!response.success) return toast.show(response.message, 'error')
  bots.value = response.data
}

async function saveBot() {
  saving.value = true
  const payload: BotPayload = { ...botForm, connection_mode: 'auto' }
  const response = editing.value
    ? await botApi.update(editing.value.bot_qq, payload)
    : await botApi.create(payload)
  saving.value = false
  if (!response.success) return toast.show(response.message, 'error')
  botModalOpen.value = false
  toast.show(response.message, 'success')
  await loadBots()
}

function askDelete(bot: BotAccount) {
  deletingBot.value = bot
  confirmOpen.value = true
}

async function confirmDelete() {
  if (!deletingBot.value) return
  saving.value = true
  const response = await botApi.remove(deletingBot.value.bot_qq)
  saving.value = false
  if (!response.success) return toast.show(response.message, 'error')
  confirmOpen.value = false
  toast.show(response.message, 'success')
  await loadBots()
}

async function openGroups(bot: BotAccount) {
  selectedBot.value = bot
  groupModalOpen.value = true
  groupForm.group_id = ''
  groupForm.group_name = ''
  groupForm.is_enabled = true
  await loadGroups()
}

async function loadGroups() {
  if (!selectedBot.value) return
  loading.value = true
  const response = await botApi.groups(selectedBot.value.bot_qq)
  loading.value = false
  if (!response.success) return toast.show(response.message, 'error')
  groups.value = response.data
}

async function saveGroup() {
  if (!selectedBot.value) return
  saving.value = true
  const response = await botApi.saveGroup(selectedBot.value.bot_qq, groupForm)
  saving.value = false
  if (!response.success) return toast.show(response.message, 'error')
  Object.assign(groupForm, { group_id: '', group_name: '', is_enabled: true })
  toast.show(response.message, 'success')
  await loadGroups()
}

async function toggleGroup(group: BotGroup) {
  if (!selectedBot.value) return
  const response = await botApi.saveGroup(selectedBot.value.bot_qq, {
    group_id: group.group_id,
    group_name: group.group_name,
    is_enabled: !group.is_enabled,
  })
  if (!response.success) return toast.show(response.message, 'error')
  group.is_enabled = !group.is_enabled
}

function askRemoveGroup(group: BotGroup) {
  deletingGroup.value = group
  groupConfirmOpen.value = true
}

async function confirmRemoveGroup() {
  if (!selectedBot.value || !deletingGroup.value) return
  saving.value = true
  const response = await botApi.removeGroup(selectedBot.value.bot_qq, deletingGroup.value.group_id)
  saving.value = false
  if (!response.success) return toast.show(response.message, 'error')
  groupConfirmOpen.value = false
  toast.show(response.message, 'success')
  await loadGroups()
}

async function syncGroups() {
  if (!selectedBot.value) return
  syncing.value = true
  const response = await botApi.syncGroups(selectedBot.value.bot_qq)
  syncing.value = false
  if (!response.success) return toast.show(response.message, 'error')
  toast.show(response.message, 'success')
  await loadGroups()
}

async function toggleWelcome(bot: BotAccount) {
  const response = await botApi.update(bot.bot_qq, {
    bot_qq: bot.bot_qq,
    nickname: bot.nickname,
    admin_qq: bot.admin_qq || '',
    connection_mode: 'auto',
    http_url: bot.http_url,
    access_token: '',
    group_mode: bot.group_mode,
    is_enabled: bot.is_enabled,
    welcome_enabled: !bot.welcome_enabled,
    auto_approve_friend: bot.auto_approve_friend,
    auto_approve_group_request: bot.auto_approve_group_request,
  })
  if (!response.success) return toast.show(response.message, 'error')
  bot.welcome_enabled = response.data.welcome_enabled
  toast.show(bot.welcome_enabled ? '进群欢迎词已开启' : '进群欢迎词已关闭', 'success')
}

async function toggleAutoApproveFriend(bot: BotAccount) {
  const response = await botApi.update(bot.bot_qq, {
    bot_qq: bot.bot_qq,
    nickname: bot.nickname,
    admin_qq: bot.admin_qq || '',
    connection_mode: 'auto',
    http_url: bot.http_url,
    access_token: '',
    group_mode: bot.group_mode,
    is_enabled: bot.is_enabled,
    welcome_enabled: bot.welcome_enabled,
    auto_approve_friend: !bot.auto_approve_friend,
    auto_approve_group_request: bot.auto_approve_group_request,
  })
  if (!response.success) return toast.show(response.message, 'error')
  bot.auto_approve_friend = response.data.auto_approve_friend
  toast.show(bot.auto_approve_friend ? '自动同意好友已开启' : '自动同意好友已关闭', 'success')
}

async function toggleAutoApproveGroupRequest(bot: BotAccount) {
  const response = await botApi.update(bot.bot_qq, {
    bot_qq: bot.bot_qq,
    nickname: bot.nickname,
    admin_qq: bot.admin_qq || '',
    connection_mode: 'auto',
    http_url: bot.http_url,
    access_token: '',
    group_mode: bot.group_mode,
    is_enabled: bot.is_enabled,
    welcome_enabled: bot.welcome_enabled,
    auto_approve_friend: bot.auto_approve_friend,
    auto_approve_group_request: !bot.auto_approve_group_request,
  })
  if (!response.success) return toast.show(response.message, 'error')
  bot.auto_approve_group_request = response.data.auto_approve_group_request
  toast.show(bot.auto_approve_group_request ? '自动同意加群申请已开启' : '自动同意加群申请已关闭', 'success')
}

async function loadAiProviders() {
  if (aiProviders.value.length) return true
  const response = await botApi.aiProviders()
  if (!response.success) {
    toast.show(response.message, 'error')
    return false
  }
  aiProviders.value = response.data
  return true
}

async function openAiConfig(bot: BotAccount) {
  aiBot.value = bot
  aiModalOpen.value = true
  aiLoading.value = true
  aiModels.text = []
  aiModels.image = []
  aiModels.generation = []
  aiModelsLoadingModule.value = ''
  aiTestingModule.value = ''
  aiTestReplies.text = ''
  aiTestReplies.image = ''
  aiTestReplies.generation = ''
  aiTestImages.text = null
  aiTestImages.image = null
  aiTestImages.generation = null
  const [providersLoaded, response] = await Promise.all([
    loadAiProviders(),
    botApi.aiConfig(bot.bot_qq),
  ])
  aiLoading.value = false
  if (!providersLoaded) return
  if (!response.success) return toast.show(response.message, 'error')
  Object.assign(aiForm, {
    is_enabled: response.data.is_enabled,
    provider_type: response.data.provider_type,
    base_url: response.data.base_url,
    api_key: response.data.api_key,
    model_name: response.data.model_name,
    image_model_name: response.data.image_model_name || '',
    text_provider_type: response.data.text_provider_type || response.data.provider_type,
    text_base_url: response.data.text_base_url || response.data.base_url,
    text_api_key: response.data.text_api_key || response.data.api_key || '',
    text_model_name: response.data.text_model_name || response.data.model_name,
    image_provider_type: response.data.image_provider_type || '',
    image_base_url: response.data.image_base_url || '',
    image_api_key: response.data.image_api_key || '',
    image_generation_provider_type: response.data.image_generation_provider_type || '',
    image_generation_base_url: response.data.image_generation_base_url || '',
    image_generation_api_key: response.data.image_generation_api_key || '',
    image_generation_model_name: response.data.image_generation_model_name || '',
    system_prompt: response.data.system_prompt,
    message_intent_prompt: response.data.message_intent_prompt || '',
    temperature: response.data.temperature,
    max_tokens: response.data.max_tokens,
    followup_count: response.data.followup_count,
    history_limit: response.data.history_limit ?? 10,
    voice_reply_enabled: response.data.voice_reply_enabled,
    voice_reply_probability: response.data.voice_reply_probability,
    image_reply_probability: response.data.image_reply_probability ?? 100,
    voice_character: response.data.voice_character,
  })
  aiHasApiKey.value = response.data.has_api_key
  aiHasImageApiKey.value = response.data.has_image_api_key
  aiHasImageGenerationApiKey.value = response.data.has_image_generation_api_key
  aiSectionOpen.text = false
  aiSectionOpen.image = false
  aiSectionOpen.generation = false
  aiSectionOpen.intent = false
}

function changeAiProvider() {
  const provider = aiProviders.value.find((item) => item.value === aiForm.text_provider_type)
  if (!provider) return
  aiForm.text_base_url = provider.default_base_url
  aiForm.text_model_name = provider.requires_model ? '' : aiForm.text_model_name
  aiForm.provider_type = aiForm.text_provider_type
  aiForm.base_url = aiForm.text_base_url
  aiForm.model_name = aiForm.text_model_name
}

function changeAiModuleProvider(modelType: 'image' | 'generation') {
  const providerType = modelType === 'image'
    ? aiForm.image_provider_type
    : aiForm.image_generation_provider_type
  if (!providerType) {
    if (modelType === 'image') aiForm.image_base_url = ''
    else aiForm.image_generation_base_url = ''
    return
  }
  const provider = aiProviders.value.find((item) => item.value === providerType)
  if (!provider) return
  if (modelType === 'image') {
    aiForm.image_base_url = provider.default_base_url
    if (provider.requires_model) aiForm.image_model_name = ''
  } else {
    aiForm.image_generation_base_url = provider.default_base_url
    if (provider.requires_model) aiForm.image_generation_model_name = ''
  }
}

async function saveAiConfig() {
  if (!aiBot.value) return
  aiSaving.value = true
  const response = await botApi.saveAiConfig(aiBot.value.bot_qq, { ...aiForm })
  aiSaving.value = false
  if (!response.success) return toast.show(response.message, 'error')
  aiHasApiKey.value = response.data.has_api_key
  aiHasImageApiKey.value = response.data.has_image_api_key
  aiHasImageGenerationApiKey.value = response.data.has_image_generation_api_key
  aiForm.api_key = ''
  aiForm.text_api_key = ''
  aiForm.image_api_key = ''
  aiForm.image_generation_api_key = ''
  toast.show(response.message, 'success')
  aiModalOpen.value = false
}

async function testAiConfig(modelType: AiModelType) {
  if (!aiBot.value) return
  aiTestingModule.value = modelType
  aiTestReplies[modelType] = ''
  aiTestImages[modelType] = null
  const response = await botApi.testAiConfig(aiBot.value.bot_qq, { ...aiForm }, modelType)
  aiTestingModule.value = ''
  if (!response.success) return toast.show(response.message, 'error')
  aiTestReplies[modelType] = response.data.reply
  aiTestImages[modelType] = response.data.image
  toast.show(response.message, 'success')
}

function testImageSource(modelType: AiModelType): string {
  const image = aiTestImages[modelType]
  return image ? `data:${image.media_type};base64,${image.data}` : ''
}

async function loadAiModels(modelType: AiModelType) {
  if (!aiBot.value) return
  aiModelsLoadingModule.value = modelType
  const payload = { ...aiForm }
  const response = await botApi.aiModels(aiBot.value.bot_qq, payload, modelType)
  aiModelsLoadingModule.value = ''
  if (!response.success) return toast.show(response.message, 'error')
  aiModels[modelType] = response.data
  toast.show(response.message, 'success')
}

function chooseAiModel(modelType: AiModelType, event: Event) {
  const value = (event.target as HTMLSelectElement).value
  if (!value) return
  if (modelType === 'image') aiForm.image_model_name = value
  else if (modelType === 'generation') aiForm.image_generation_model_name = value
  else aiForm.text_model_name = value
}

function currentAiProvider() {
  return aiProviders.value.find((item) => item.value === aiForm.text_provider_type)
}

watch(
  () => [aiForm.text_provider_type, aiForm.text_base_url, aiForm.text_api_key],
  () => {
    aiModels.text = []
    aiTestReplies.text = ''
    aiTestImages.text = null
    aiModels.image = []
    aiTestReplies.image = ''
    aiTestImages.image = null
    aiModels.generation = []
    aiTestReplies.generation = ''
    aiTestImages.generation = null
  },
)
watch(
  () => [aiForm.image_provider_type, aiForm.image_base_url, aiForm.image_api_key],
  () => {
    aiModels.image = []
    aiTestReplies.image = ''
    aiTestImages.image = null
  },
)
watch(
  () => [aiForm.image_generation_provider_type, aiForm.image_generation_base_url, aiForm.image_generation_api_key],
  () => {
    aiModels.generation = []
    aiTestReplies.generation = ''
    aiTestImages.generation = null
  },
)
watch(() => aiForm.text_model_name, () => { aiTestReplies.text = ''; aiTestImages.text = null })
watch(() => aiForm.image_model_name, () => { aiTestReplies.image = ''; aiTestImages.image = null })
watch(() => aiForm.image_generation_model_name, () => { aiTestReplies.generation = ''; aiTestImages.generation = null })

onMounted(loadBots)
</script>

<template>
  <div class="page-enter bot-page">
    <div class="page-heading">
      <div>
        <p class="eyebrow">BOT SCOPE</p>
        <h1>机器人管理</h1>
        <p>维护 QQ 机器人及其群启用范围。</p>
      </div>
      <button type="button" class="button primary" @click="openCreate">
        <Plus :size="18" />添加机器人
      </button>
    </div>

    <section class="content-panel bot-list-panel">
      <div class="panel-heading">
        <div><h2><Bot :size="17" />机器人账号</h2><p>共 {{ bots.length }} 个已配置账号</p></div>
        <button type="button" class="icon-button" title="刷新" @click="loadBots"><RefreshCw :size="18" /></button>
      </div>
      <div class="table-scroll">
        <table>
          <thead><tr><th>机器人 QQ</th><th>名称</th><th v-if="isAdmin()">所属用户</th><th>连接</th><th>群策略</th><th>进群欢迎</th><th>自动同意好友</th><th>自动同意加群</th><th>状态</th><th>更新时间</th><th class="action-column">操作</th></tr></thead>
          <tbody>
            <tr v-for="item in bots" :key="item.id">
              <td class="mono-text"><strong>{{ item.bot_qq }}</strong></td>
              <td>{{ item.nickname || '未命名' }}</td>
              <td v-if="isAdmin()" class="muted-text">{{ item.owner_username || '-' }}</td>
              <td><span class="status-badge" :class="item.connected ? 'enabled' : 'disabled'"><i />{{ item.connected ? 'WebSocket 在线' : item.http_url && item.connection_mode !== 'websocket' ? 'HTTP 已配置' : '等待连接' }}</span></td>
              <td>{{ item.group_mode === 'all' ? '全部群' : '仅白名单群' }}</td>
              <td><button type="button" class="status-toggle" :class="{ active: item.welcome_enabled }" :title="item.welcome_enabled ? '点击关闭' : '点击开启'" @click="toggleWelcome(item)"><i />{{ item.welcome_enabled ? '已开启' : '已关闭' }}</button></td>
              <td><button type="button" class="status-toggle" :class="{ active: item.auto_approve_friend }" :title="item.auto_approve_friend ? '点击关闭' : '点击开启'" @click="toggleAutoApproveFriend(item)"><i />{{ item.auto_approve_friend ? '已开启' : '已关闭' }}</button></td>
              <td><button type="button" class="status-toggle" :class="{ active: item.auto_approve_group_request }" :title="item.auto_approve_group_request ? '点击关闭' : '点击开启'" @click="toggleAutoApproveGroupRequest(item)"><i />{{ item.auto_approve_group_request ? '已开启' : '已关闭' }}</button></td>
              <td><span class="status-badge" :class="item.is_enabled ? 'enabled' : 'disabled'"><i />{{ item.is_enabled ? '已启用' : '已停用' }}</span></td>
              <td class="muted-text">{{ item.updated_at }}</td>
              <td class="action-buttons">
                <button type="button" class="icon-button" title="群启用范围" @click="openGroups(item)"><UsersRound :size="16" /></button>
                <button type="button" class="icon-button" title="AI 配置" @click="openAiConfig(item)"><BrainCircuit :size="16" /></button>
                <button type="button" class="icon-button" title="编辑机器人" @click="openEdit(item)"><Edit3 :size="16" /></button>
                <button type="button" class="icon-button danger-hover" title="删除机器人" @click="askDelete(item)"><Trash2 :size="16" /></button>
              </td>
            </tr>
          </tbody>
        </table>
        <EmptyState v-if="!loading && !bots.length" text="还没有配置 QQ 机器人" />
      </div>
    </section>
    <LoadingOverlay :visible="loading" contained />

    <BaseModal :open="botModalOpen" :title="editing ? '编辑机器人' : '添加机器人'" @close="botModalOpen = false">
      <form class="rule-form" @submit.prevent="saveBot">
        <label><span class="field-label">机器人 QQ<span class="required">*</span></span><input v-model="botForm.bot_qq" inputmode="numeric" maxlength="20" required placeholder="输入 NapCat 登录的 QQ 号" /></label>
        <label>显示名称<input v-model="botForm.nickname" maxlength="100" placeholder="例如：客服机器人" /></label>
        <label>管理员 QQ<input v-model="botForm.admin_qq" inputmode="numeric" maxlength="20" placeholder="可选，填写 QQ 号" /></label>
        <label><span class="field-label">NapCat HTTP 地址<span class="required">*</span></span><input v-model="botForm.http_url" maxlength="500" required placeholder="例如：http://NapCat地址:3000" /></label>
        <label>NapCat Token<PasswordInput v-model="botForm.access_token" :placeholder="editing?.has_access_token ? '留空保持原 Token' : '可选'" autocomplete="new-password" /></label>
        <label>群消息启用范围
          <select v-model="botForm.group_mode">
            <option value="all">全部群开启</option>
            <option value="allowlist">仅白名单群开启</option>
          </select>
        </label>
        <label class="switch-field"><span>开启进群欢迎词</span><button type="button" class="switch" :class="{ active: botForm.welcome_enabled }" role="switch" :aria-checked="botForm.welcome_enabled" @click="botForm.welcome_enabled = !botForm.welcome_enabled"><i /></button></label>
        <label class="switch-field"><span>启用机器人</span><button type="button" class="switch" :class="{ active: botForm.is_enabled }" role="switch" :aria-checked="botForm.is_enabled" @click="botForm.is_enabled = !botForm.is_enabled"><i /></button></label>
        <div class="modal-form-actions"><button type="button" class="button secondary" @click="botModalOpen = false">取消</button><button type="submit" class="button primary" :disabled="saving"><span v-if="saving" class="button-spinner" />保存</button></div>
      </form>
    </BaseModal>

    <BaseModal :open="groupModalOpen" :title="`${selectedBot?.nickname || selectedBot?.bot_qq || ''} · 群启用范围`" width="820px" @close="groupModalOpen = false">
      <div class="group-toolbar">
        <div><ListChecks :size="18" /><span>{{ selectedBot?.group_mode === 'allowlist' ? '只有启用的群可以触发群消息功能' : '当前为全部群开启，列表用于预先维护范围' }}</span></div>
        <button type="button" class="button secondary" :disabled="syncing" @click="syncGroups"><RotateCw :size="16" :class="{ spin: syncing }" />从 NapCat 同步</button>
      </div>
      <form class="inline-group-form" @submit.prevent="saveGroup">
        <label>群号<input v-model="groupForm.group_id" inputmode="numeric" required placeholder="QQ群号" /></label>
        <label>群名称<input v-model="groupForm.group_name" placeholder="可选" /></label>
        <button type="submit" class="button primary" :disabled="saving"><Plus :size="16" />保存群</button>
      </form>
      <div class="table-scroll group-table">
        <table>
          <thead><tr><th>群号</th><th>群名称</th><th>状态</th><th class="action-column">操作</th></tr></thead>
          <tbody><tr v-for="group in groups" :key="group.id"><td class="mono-text">{{ group.group_id }}</td><td>{{ group.group_name || '未命名群' }}</td><td><button type="button" class="status-toggle" :class="{ active: group.is_enabled }" @click="toggleGroup(group)"><i />{{ group.is_enabled ? '已开启' : '已关闭' }}</button></td><td class="action-buttons"><button type="button" class="icon-button danger-hover" title="删除群配置" @click="askRemoveGroup(group)"><Trash2 :size="16" /></button></td></tr></tbody>
        </table>
        <EmptyState v-if="!groups.length" text="还没有群配置，可手动添加或从 NapCat 同步" />
      </div>
    </BaseModal>

    <BaseModal :open="aiModalOpen" :title="`${aiBot?.nickname || aiBot?.bot_qq || ''} · AI 配置`" width="720px" @close="aiModalOpen = false">
      <div class="ai-config-wrap">
        <form class="rule-form ai-config-form" @submit.prevent="saveAiConfig">
          <label class="switch-field"><span>启用 AI 回复</span><button type="button" class="switch" :class="{ active: aiForm.is_enabled }" role="switch" :aria-checked="aiForm.is_enabled" @click="aiForm.is_enabled = !aiForm.is_enabled"><i /></button></label>
          <section class="ai-config-section" :class="{ expanded: aiSectionOpen.text }">
            <button type="button" class="ai-config-section-toggle" @click="aiSectionOpen.text = !aiSectionOpen.text"><strong>文字模型</strong><span>{{ aiSectionOpen.text ? '收起' : '展开' }}</span></button>
            <div v-if="aiSectionOpen.text" class="ai-config-section-body">
              <div class="form-grid">
                <label>AI 平台<select v-model="aiForm.text_provider_type" required @change="changeAiProvider"><option v-for="provider in aiProviders" :key="provider.value" :value="provider.value">{{ provider.label }}</option></select></label>
                <label>AI 地址<input v-model.trim="aiForm.text_base_url" maxlength="1000" required placeholder="https://api.example.com/v1" /></label>
              </div>
              <label>AI 秘钥<PasswordInput v-model="aiForm.text_api_key" :placeholder="aiHasApiKey ? '已配置，留空保持不变' : '请输入平台 AI 秘钥'" autocomplete="new-password" /></label>
              <div class="ai-model-row"><label>模型<input v-model.trim="aiForm.text_model_name" :required="currentAiProvider()?.requires_model !== false" maxlength="255" placeholder="输入或获取文字模型" /><select v-if="aiModels.text.length" class="ai-model-picker" aria-label="选择文字模型" @change="chooseAiModel('text', $event)"><option value="">从已获取的模型中选择</option><option v-for="model in aiModels.text" :key="model.value" :value="model.value">{{ model.label }}</option></select></label><button type="button" class="button secondary" :disabled="aiModelsLoadingModule !== '' || currentAiProvider()?.supports_models === false" @click="loadAiModels('text')"><RefreshCw :size="16" :class="{ spin: aiModelsLoadingModule === 'text' }" />获取模型</button></div>
              <div class="ai-model-test"><button type="button" class="button secondary" :disabled="aiTestingModule !== ''" @click="testAiConfig('text')"><span v-if="aiTestingModule === 'text'" class="button-spinner" /><FlaskConical v-else :size="16" />测试文字模型</button><div v-if="aiTestReplies.text" class="ai-test-result"><FlaskConical :size="16" /><span>{{ aiTestReplies.text }}</span></div></div>
            </div>
          </section>
          <section class="ai-config-section" :class="{ expanded: aiSectionOpen.image }">
            <button type="button" class="ai-config-section-toggle" @click="aiSectionOpen.image = !aiSectionOpen.image"><strong>图片模型</strong><span>{{ aiSectionOpen.image ? '收起' : '展开' }}</span></button>
            <div v-if="aiSectionOpen.image" class="ai-config-section-body">
              <div class="form-grid"><label>AI 平台<select v-model="aiForm.image_provider_type" @change="changeAiModuleProvider('image')"><option value="">跟随文字模型</option><option v-for="provider in aiProviders" :key="provider.value" :value="provider.value">{{ provider.label }}</option></select></label><label>AI 地址<input v-model.trim="aiForm.image_base_url" maxlength="1000" placeholder="留空跟随文字模型" /></label></div>
              <label>AI 秘钥<PasswordInput v-model="aiForm.image_api_key" :placeholder="aiHasImageApiKey ? '已配置，留空保持不变' : '留空跟随文字模型'" autocomplete="new-password" /></label>
              <div class="ai-model-row"><label>模型<input v-model.trim="aiForm.image_model_name" maxlength="255" placeholder="留空使用文字模型" /><select v-if="aiModels.image.length" class="ai-model-picker" aria-label="选择图片模型" @change="chooseAiModel('image', $event)"><option value="">从已获取的模型中选择</option><option v-for="model in aiModels.image" :key="model.value" :value="model.value">{{ model.label }}</option></select></label><button type="button" class="button secondary" :disabled="aiModelsLoadingModule !== ''" @click="loadAiModels('image')"><RefreshCw :size="16" :class="{ spin: aiModelsLoadingModule === 'image' }" />获取模型</button></div>
              <div class="ai-model-test"><button type="button" class="button secondary" :disabled="aiTestingModule !== ''" @click="testAiConfig('image')"><span v-if="aiTestingModule === 'image'" class="button-spinner" /><FlaskConical v-else :size="16" />测试图片模型</button><div v-if="aiTestReplies.image" class="ai-test-result"><FlaskConical :size="16" /><span>{{ aiTestReplies.image }}</span></div></div>
            </div>
          </section>
          <section class="ai-config-section" :class="{ expanded: aiSectionOpen.generation }">
            <button type="button" class="ai-config-section-toggle" @click="aiSectionOpen.generation = !aiSectionOpen.generation"><strong>文生图模型</strong><span>{{ aiSectionOpen.generation ? '收起' : '展开' }}</span></button>
            <div v-if="aiSectionOpen.generation" class="ai-config-section-body">
              <div class="form-grid"><label>AI 平台<select v-model="aiForm.image_generation_provider_type" @change="changeAiModuleProvider('generation')"><option value="">请选择平台</option><option v-for="provider in aiProviders" :key="provider.value" :value="provider.value">{{ provider.label }}</option></select></label><label>AI 地址<input v-model.trim="aiForm.image_generation_base_url" maxlength="1000" placeholder="请输入文生图接口地址" /></label></div>
              <label>AI 秘钥<PasswordInput v-model="aiForm.image_generation_api_key" :placeholder="aiHasImageGenerationApiKey ? '已配置，留空保持不变' : '请输入文生图平台 AI 秘钥'" autocomplete="new-password" /></label>
              <div class="ai-model-row"><label>模型<input v-model.trim="aiForm.image_generation_model_name" maxlength="255" placeholder="输入文生图模型" /><select v-if="aiModels.generation.length" class="ai-model-picker" aria-label="选择文生图模型" @change="chooseAiModel('generation', $event)"><option value="">从已获取的模型中选择</option><option v-for="model in aiModels.generation" :key="model.value" :value="model.value">{{ model.label }}</option></select></label><button type="button" class="button secondary" :disabled="aiModelsLoadingModule !== ''" @click="loadAiModels('generation')"><RefreshCw :size="16" :class="{ spin: aiModelsLoadingModule === 'generation' }" />获取模型</button></div>
              <div class="ai-model-test"><button type="button" class="button secondary" :disabled="aiTestingModule !== ''" @click="testAiConfig('generation')"><span v-if="aiTestingModule === 'generation'" class="button-spinner" /><FlaskConical v-else :size="16" />测试文生图模型</button><div v-if="aiTestReplies.generation" class="ai-test-result"><FlaskConical :size="16" /><div><span>{{ aiTestReplies.generation }}</span><img v-if="aiTestImages.generation" class="ai-test-image" :src="testImageSource('generation')" alt="文生图测试结果" /></div></div></div>
            </div>
          </section>
          <section class="ai-config-section" :class="{ expanded: aiSectionOpen.intent }">
            <button type="button" class="ai-config-section-toggle" @click="aiSectionOpen.intent = !aiSectionOpen.intent"><strong>消息分类</strong><span>{{ aiSectionOpen.intent ? '收起' : '展开' }}</span></button>
            <div v-if="aiSectionOpen.intent" class="ai-config-section-body">
              <label>分类提示词<textarea v-model="aiForm.message_intent_prompt" maxlength="10000" rows="9" placeholder="设置语音、表情、文生图、不回复和普通回复的判断规则" /><small class="field-hint">用于结合当前消息和聊天记录判断回复形式；判定为不回复时系统会跳过本次回复且不发送任何内容。严格 JSON 字段、禁言规则和不可用能力限制由系统固定。</small></label>
            </div>
          </section>
          <label>系统提示词<textarea v-model="aiForm.system_prompt" maxlength="10000" rows="5" placeholder="设置机器人的身份、语气和回答边界" /><small class="field-hint">可选，用于设定机器人的身份、语气、回答范围和禁止事项；最多 10000 字符。</small></label>
          <div class="form-grid">
            <label>随机性<input v-model.number="aiForm.temperature" type="number" min="0" max="2" step="0.1" required /><small class="field-hint">范围 0 到 2；越低越稳定，越高越灵活，建议填写 0.7。</small></label>
            <label>最大回复长度<input v-model.number="aiForm.max_tokens" type="number" min="1" max="32768" step="1" required /><small class="field-hint">限制单次回复长度，数值越大可回复越长，建议填写 1000。</small></label>
          </div>
          <label>免触发回复次数<input v-model.number="aiForm.followup_count" type="number" min="0" max="5000" step="1" required /><small class="field-hint">首次触发 AI 后，同一群同一用户可免 @/引用继续对话的次数；支持 0 至 5000 次，填写 0 可关闭。</small></label>
          <label>携带聊天记录条数<input v-model.number="aiForm.history_limit" type="number" min="0" max="5000" step="1" required /><small class="field-hint">发送给 AI 时携带的近期聊天记录条数；填写 0 表示不携带，未配置时默认 10 条。</small></label>
          <section class="ai-voice-panel ai-image-panel">
            <div class="ai-voice-heading">
              <div class="ai-voice-title">
                <span class="ai-voice-icon"><ImageIcon :size="17" /></span>
                <div><strong>随机图片回复</strong><small>群聊 AI 回复可按概率发送动画表情</small></div>
              </div>
            </div>
            <div class="ai-voice-probability">
              <div class="ai-voice-probability-head"><span>图片回复概率</span><output>{{ aiForm.image_reply_probability }}%</output></div>
              <div class="ai-voice-slider-row">
                <input v-model.number="aiForm.image_reply_probability" class="ai-voice-slider" type="range" min="0" max="100" step="1" aria-label="图片回复概率" />
                <input v-model.number="aiForm.image_reply_probability" class="ai-voice-number" type="number" min="0" max="100" step="1" required aria-label="图片回复概率数值" />
              </div>
              <small>设置为 0% 时不发送图片；发送前会根据 AI 回复语义随机选择已分类的动画表情。</small>
            </div>
          </section>
          <section class="ai-voice-panel" :class="{ disabled: !aiForm.voice_reply_enabled }">
            <div class="ai-voice-heading">
              <div class="ai-voice-title">
                <span class="ai-voice-icon"><Mic2 :size="17" /></span>
                <div><strong>随机语音回复</strong><small>群聊 AI 回复可按概率发送语音消息</small></div>
              </div>
              <button type="button" class="switch" :class="{ active: aiForm.voice_reply_enabled }" role="switch" :aria-checked="aiForm.voice_reply_enabled" aria-label="启用随机语音回复" @click="aiForm.voice_reply_enabled = !aiForm.voice_reply_enabled"><i /></button>
            </div>
            <div class="ai-voice-probability">
              <div class="ai-voice-probability-head"><span>语音回复概率</span><output>{{ aiForm.voice_reply_probability }}%</output></div>
              <div class="ai-voice-slider-row">
                <input v-model.number="aiForm.voice_reply_probability" class="ai-voice-slider" type="range" min="0" max="100" step="1" :disabled="!aiForm.voice_reply_enabled" aria-label="语音回复概率" />
                <input v-model.number="aiForm.voice_reply_probability" class="ai-voice-number" type="number" min="0" max="100" step="1" :disabled="!aiForm.voice_reply_enabled" required aria-label="语音回复概率数值" />
              </div>
              <small>设置为 0% 时不发送语音；私聊始终发送文本。</small>
            </div>
          </section>
          <div class="modal-form-actions ai-actions">
            <button type="button" class="button secondary" @click="aiModalOpen = false">取消</button>
            <button type="submit" class="button primary" :disabled="aiSaving"><span v-if="aiSaving" class="button-spinner" />保存</button>
          </div>
        </form>
        <LoadingOverlay :visible="aiLoading" contained />
      </div>
    </BaseModal>

    <ConfirmDialog :open="confirmOpen" title="删除机器人" :message="`确定删除机器人 ${deletingBot?.bot_qq || ''} 吗？`" :loading="saving" @close="confirmOpen = false" @confirm="confirmDelete" />
    <ConfirmDialog :open="groupConfirmOpen" title="删除群配置" :message="`确定删除群 ${deletingGroup?.group_name || deletingGroup?.group_id || ''} 的配置吗？`" :loading="saving" @close="groupConfirmOpen = false" @confirm="confirmRemoveGroup" />
  </div>
</template>
