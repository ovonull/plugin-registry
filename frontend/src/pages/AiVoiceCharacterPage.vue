<script setup lang="ts">
import { onMounted, reactive, ref, watch } from 'vue'
import { Edit3, Filter, Headphones, Mic2, Plus, RefreshCw, Search, Trash2 } from 'lucide-vue-next'
import { aiVoiceCharacterApi, botApi } from '../api'
import type {
  AiVoiceCharacterOption,
  AiVoiceCharacterPayload,
  AiVoiceCharacterRule,
  BotAccount,
  BotGroup,
  Pagination,
} from '../types'
import BaseModal from '../components/common/BaseModal.vue'
import ConfirmDialog from '../components/common/ConfirmDialog.vue'
import EmptyState from '../components/common/EmptyState.vue'
import LoadingOverlay from '../components/common/LoadingOverlay.vue'
import { useAuth } from '../composables/useAuth'
import { useToast } from '../composables/useToast'

const auth = useAuth()
const toast = useToast()
const loading = ref(false)
const saving = ref(false)
const optionsLoading = ref(false)
const groupsLoading = ref(false)
const modalOpen = ref(false)
const confirmOpen = ref(false)
const editing = ref<AiVoiceCharacterRule | null>(null)
const deleting = ref<AiVoiceCharacterRule | null>(null)
const bots = ref<BotAccount[]>([])
const groups = ref<BotGroup[]>([])
const options = ref<AiVoiceCharacterOption[]>([])
let groupsRequestId = 0
let optionsRequestId = 0

const query = reactive({ bot_qq: '', group_id: '', enabled: '', page: 1, page_size: 20 })
const result = ref<Pagination<AiVoiceCharacterRule>>({ items: [], page: 1, page_size: 20, total: 0, pages: 1 })
const form = reactive<AiVoiceCharacterPayload>({ bot_qq: '', group_id: '', character_id: '', character_name: '', preview_url: '', is_enabled: true })
const isAdmin = () => auth.state.user?.role_code === 'admin'

function resetForm() {
  Object.assign(form, { bot_qq: '', group_id: '', character_id: '', character_name: '', preview_url: '', is_enabled: true })
  options.value = []
}

function openCreate() {
  editing.value = null
  resetForm()
  if (bots.value.length === 1) form.bot_qq = bots.value[0].bot_qq
  modalOpen.value = true
  if (form.bot_qq) void loadOptions(form.bot_qq, '')
}

function openEdit(rule: AiVoiceCharacterRule) {
  editing.value = rule
  Object.assign(form, { bot_qq: rule.bot_qq, group_id: rule.group_id, character_id: rule.character_id, character_name: rule.character_name, preview_url: rule.preview_url, is_enabled: rule.is_enabled })
  modalOpen.value = true
  void loadOptions(rule.bot_qq, rule.group_id)
}

async function loadGroups(botQq: string) {
  const requestId = ++groupsRequestId
  groups.value = []
  groupsLoading.value = false
  if (!botQq) return
  groupsLoading.value = true
  const response = await botApi.groups(botQq)
  if (requestId !== groupsRequestId) return
  groupsLoading.value = false
  if (!response.success) return toast.show(response.message, 'error')
  groups.value = response.data.filter((item) => item.is_enabled)
}

async function loadOptions(botQq: string, groupId: string) {
  const requestId = ++optionsRequestId
  options.value = []
  optionsLoading.value = false
  if (!botQq) return
  optionsLoading.value = true
  const response = await aiVoiceCharacterApi.options(botQq, groupId)
  if (requestId !== optionsRequestId) return
  optionsLoading.value = false
  if (!response.success) {
    toast.show(response.message, 'error')
    return
  }
  options.value = response.data
  if (editing.value && form.character_id && !options.value.some((item) => item.character_id === form.character_id)) {
    options.value.unshift({ character_id: form.character_id, character_name: form.character_name || form.character_id, preview_url: form.preview_url })
  }
}

function selectCharacter(option: AiVoiceCharacterOption) {
  form.character_id = option.character_id
  form.character_name = option.character_name
  form.preview_url = option.preview_url
}

function handleCharacterChange() {
  const option = options.value.find((item) => item.character_id === form.character_id)
  if (option) selectCharacter(option)
}

function handleBotChange() {
  form.group_id = ''
  form.character_id = ''
}

function handleGroupChange() {
  form.character_id = ''
  form.character_name = ''
  form.preview_url = ''
}

async function loadRules() {
  loading.value = true
  const response = await aiVoiceCharacterApi.list({ ...query, enabled: query.enabled === '' ? undefined : query.enabled === 'true' })
  loading.value = false
  if (!response.success) return toast.show(response.message, 'error')
  result.value = response.data
}

async function loadBots() {
  const response = await botApi.list()
  if (!response.success) return toast.show(response.message, 'error')
  bots.value = response.data.filter((item) => item.is_enabled)
}

function search() { query.page = 1; void loadRules() }
function resetFilters() { Object.assign(query, { bot_qq: '', group_id: '', enabled: '', page: 1 }); void loadRules() }
function changePage(page: number) { if (page >= 1 && page <= result.value.pages) { query.page = page; void loadRules() } }

async function saveRule() {
  if (!form.bot_qq || !form.character_id) return toast.show('请选择机器人和语音角色', 'warning')
  saving.value = true
  const response = editing.value ? await aiVoiceCharacterApi.update(editing.value.id, { ...form }) : await aiVoiceCharacterApi.create({ ...form })
  saving.value = false
  if (!response.success) return toast.show(response.message, 'error')
  modalOpen.value = false
  toast.show(editing.value ? 'AI 语音角色配置已保存' : 'AI 语音角色配置已添加', 'success')
  await loadRules()
}

function askDelete(rule: AiVoiceCharacterRule) { deleting.value = rule; confirmOpen.value = true }

async function confirmDelete() {
  if (!deleting.value) return
  saving.value = true
  const response = await aiVoiceCharacterApi.remove(deleting.value.id)
  saving.value = false
  if (!response.success) return toast.show(response.message, 'error')
  confirmOpen.value = false
  toast.show(response.message, 'success')
  await loadRules()
}

async function toggleRule(rule: AiVoiceCharacterRule) {
  const response = await aiVoiceCharacterApi.update(rule.id, { bot_qq: rule.bot_qq, group_id: rule.group_id, character_id: rule.character_id, character_name: rule.character_name, preview_url: rule.preview_url, is_enabled: !rule.is_enabled })
  if (!response.success) return toast.show(response.message, 'error')
  rule.is_enabled = !rule.is_enabled
  toast.show(rule.is_enabled ? '语音角色已启用' : '语音角色已停用', 'success')
}

watch(() => form.bot_qq, (botQq) => {
  void loadGroups(botQq)
  void loadOptions(botQq, form.group_id)
})
watch(() => form.group_id, (groupId) => {
  void loadOptions(form.bot_qq, groupId)
})

onMounted(() => { void loadRules(); void loadBots() })
</script>

<template>
  <div class="page-enter ai-voice-character-page">
    <div class="page-heading">
      <div><p class="eyebrow">AI VOICE</p><h1>群 AI 语音角色配置</h1><p>为机器人设置默认或指定群的 AI 语音角色，群级配置优先。</p></div>
      <button type="button" class="button primary" @click="openCreate"><Plus :size="18" />添加角色配置</button>
    </div>

    <section class="filter-panel">
      <div class="filter-title"><Filter :size="17" /><strong>筛选条件</strong></div>
      <div class="filter-fields">
        <label>机器人 QQ<select v-model="query.bot_qq"><option value="">全部机器人</option><option v-for="bot in bots" :key="bot.id" :value="bot.bot_qq">{{ bot.nickname || bot.bot_qq }} · {{ bot.bot_qq }}</option></select></label>
        <label>群号<input v-model="query.group_id" inputmode="numeric" placeholder="留空查看全部" @keyup.enter="search" /></label>
        <label>状态<select v-model="query.enabled"><option value="">全部状态</option><option value="true">已启用</option><option value="false">已停用</option></select></label>
        <div class="filter-actions"><button type="button" class="button primary" @click="search"><Search :size="16" />查询</button><button type="button" class="button ghost" title="重置筛选" @click="resetFilters"><RefreshCw :size="16" />重置</button></div>
      </div>
    </section>

    <section class="content-panel rules-panel">
      <div class="panel-heading"><div><h2><Mic2 :size="17" />语音角色配置</h2><p>共 {{ result.total }} 条配置<span v-if="isAdmin()"> · 管理员视图</span></p></div><button type="button" class="icon-button" title="刷新列表" @click="loadRules"><RefreshCw :size="18" /></button></div>
      <div class="table-scroll rules-table ai-voice-character-table"><table><thead><tr><th>机器人 QQ</th><th>群范围</th><th>角色</th><th>试听</th><th v-if="isAdmin()">所属用户</th><th>状态</th><th>更新时间</th><th class="action-column">操作</th></tr></thead><tbody><tr v-for="rule in result.items" :key="rule.id"><td class="mono-text">{{ rule.bot_qq }}</td><td><span class="scope-chip">{{ rule.group_id || '所有群/默认' }}</span></td><td><strong>{{ rule.character_name || rule.character_id }}</strong><small class="muted-text character-id">{{ rule.character_id }}</small></td><td><a v-if="rule.preview_url" :href="rule.preview_url" target="_blank" rel="noreferrer" class="icon-button" title="试听角色"><Headphones :size="16" /></a><span v-else class="muted-text">无</span></td><td v-if="isAdmin()" class="muted-text">{{ rule.owner_username }}</td><td><button type="button" class="status-toggle" :class="{ active: rule.is_enabled }" :title="rule.is_enabled ? '点击停用' : '点击启用'" @click="toggleRule(rule)"><i />{{ rule.is_enabled ? '已启用' : '已停用' }}</button></td><td class="muted-text">{{ rule.updated_at }}</td><td class="action-buttons"><button type="button" class="icon-button" title="编辑角色配置" @click="openEdit(rule)"><Edit3 :size="16" /></button><button type="button" class="icon-button danger-hover" title="删除角色配置" @click="askDelete(rule)"><Trash2 :size="16" /></button></td></tr></tbody></table><EmptyState v-if="!loading && !result.items.length" text="还没有 AI 语音角色配置" /></div>
      <div class="pagination"><span>第 {{ result.page }} / {{ result.pages }} 页，共 {{ result.total }} 条</span><div><select v-model.number="query.page_size" title="每页条数" @change="query.page = 1; loadRules()"><option :value="10">10 条/页</option><option :value="20">20 条/页</option><option :value="50">50 条/页</option><option :value="100">100 条/页</option></select><button type="button" class="page-button" :disabled="result.page <= 1" @click="changePage(result.page - 1)">上一页</button><button type="button" class="page-button" :disabled="result.page >= result.pages" @click="changePage(result.page + 1)">下一页</button></div></div>
    </section>
    <LoadingOverlay :visible="loading" contained />

    <BaseModal :open="modalOpen" :title="editing ? '编辑 AI 语音角色' : '添加 AI 语音角色'" @close="modalOpen = false"><form class="rule-form" @submit.prevent="saveRule">
      <label><span class="field-label">QQ 机器人<span class="required">*</span></span><select v-model="form.bot_qq" required @change="handleBotChange"><option value="" disabled>请选择机器人</option><option v-for="bot in bots" :key="bot.id" :value="bot.bot_qq">{{ bot.nickname || bot.bot_qq }} · {{ bot.bot_qq }}</option></select></label>
      <label><span class="field-label">群</span><select v-model="form.group_id" :disabled="!form.bot_qq || groupsLoading" @change="handleGroupChange"><option value="">全部群/机器人默认</option><option v-if="editing && form.group_id && !groups.some((group) => group.group_id === form.group_id)" :value="form.group_id">{{ form.group_id }}（当前配置）</option><option v-for="group in groups" :key="group.id" :value="group.group_id">{{ group.group_name || group.group_id }} · {{ group.group_id }}</option></select><small v-if="groupsLoading">正在加载群列表...</small><small v-else>选择群后会自动查询该群可用角色；不选群时使用内置角色。</small></label>
      <label><span class="field-label">语音角色<span class="required">*</span></span><select v-model="form.character_id" required :disabled="optionsLoading || !form.bot_qq" @change="handleCharacterChange"><option value="" disabled>{{ optionsLoading ? '正在查询角色...' : '请选择语音角色' }}</option><option v-for="option in options" :key="option.character_id" :value="option.character_id">{{ option.character_name }} · {{ option.character_id }}</option></select><small v-if="form.character_id">已选择：{{ form.character_name }}（{{ form.character_id }}）</small></label>
      <label class="switch-field"><span>启用角色配置</span><button type="button" class="switch" :class="{ active: form.is_enabled }" role="switch" :aria-checked="form.is_enabled" @click="form.is_enabled = !form.is_enabled"><i /></button></label>
      <div class="modal-form-actions"><button type="button" class="button secondary" :disabled="saving" @click="modalOpen = false">取消</button><button class="button primary" type="submit" :disabled="saving || !bots.length || !form.character_id"><span v-if="saving" class="button-spinner" />保存配置</button></div>
    </form></BaseModal>
    <ConfirmDialog :open="confirmOpen" title="删除 AI 语音角色配置" :message="`确定删除“${deleting?.character_name || deleting?.character_id || ''}”的配置吗？`" :loading="saving" @close="confirmOpen = false" @confirm="confirmDelete" />
  </div>
</template>
