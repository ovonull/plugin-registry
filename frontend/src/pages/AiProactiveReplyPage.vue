<script setup lang="ts">
import { onMounted, reactive, ref, watch } from 'vue'
import { BrainCircuit, Edit3, Filter, Plus, RefreshCw, Search, Trash2 } from 'lucide-vue-next'
import { aiProactiveReplyApi, botApi } from '../api'
import type { AiProactiveReplyPayload, AiProactiveReplyRule, BotAccount, BotGroup, Pagination } from '../types'
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
const modalOpen = ref(false)
const confirmOpen = ref(false)
const editing = ref<AiProactiveReplyRule | null>(null)
const deleting = ref<AiProactiveReplyRule | null>(null)
const bots = ref<BotAccount[]>([])
const groups = ref<BotGroup[]>([])
const groupsLoading = ref(false)
let groupsRequestId = 0
const query = reactive({ bot_qq: '', group_id: '', enabled: '', page: 1, page_size: 20 })
const result = ref<Pagination<AiProactiveReplyRule>>({ items: [], page: 1, page_size: 20, total: 0, pages: 1 })
const form = reactive<AiProactiveReplyPayload>({
  bot_qq: '', group_id: '', is_enabled: true, min_interval_seconds: 60, max_interval_seconds: 300,
})
const isAdmin = () => auth.state.user?.role_code === 'admin'

function resetForm() {
  Object.assign(form, { bot_qq: '', group_id: '', is_enabled: true, min_interval_seconds: 60, max_interval_seconds: 300 })
}

function openCreate() {
  editing.value = null
  resetForm()
  if (bots.value.length === 1) form.bot_qq = bots.value[0].bot_qq
  modalOpen.value = true
}

function openEdit(rule: AiProactiveReplyRule) {
  editing.value = rule
  Object.assign(form, {
    bot_qq: rule.bot_qq,
    group_id: rule.group_id,
    is_enabled: rule.is_enabled,
    min_interval_seconds: rule.min_interval_seconds,
    max_interval_seconds: rule.max_interval_seconds,
  })
  modalOpen.value = true
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

async function loadRules() {
  loading.value = true
  const response = await aiProactiveReplyApi.list({
    ...query,
    enabled: query.enabled === '' ? undefined : query.enabled === 'true',
  })
  loading.value = false
  if (!response.success) return toast.show(response.message, 'error')
  result.value = response.data
}

async function loadBots() {
  const response = await botApi.list()
  if (!response.success) return toast.show(response.message, 'error')
  bots.value = response.data.filter((item) => item.is_enabled)
}

function search() {
  query.page = 1
  void loadRules()
}

function resetFilters() {
  Object.assign(query, { bot_qq: '', group_id: '', enabled: '', page: 1 })
  void loadRules()
}

function changePage(page: number) {
  if (page < 1 || page > result.value.pages) return
  query.page = page
  void loadRules()
}

async function saveRule() {
  if (form.max_interval_seconds < form.min_interval_seconds) {
    toast.show('最大间隔不能小于最小间隔', 'warning')
    return
  }
  saving.value = true
  const response = editing.value
    ? await aiProactiveReplyApi.update(editing.value.id, { ...form })
    : await aiProactiveReplyApi.create({ ...form })
  saving.value = false
  if (!response.success) return toast.show(response.message, 'error')
  modalOpen.value = false
  toast.show(editing.value ? 'AI 主动回复规则已保存' : 'AI 主动回复规则已添加', 'success')
  await loadRules()
}

function askDelete(rule: AiProactiveReplyRule) {
  deleting.value = rule
  confirmOpen.value = true
}

async function confirmDelete() {
  if (!deleting.value) return
  saving.value = true
  const response = await aiProactiveReplyApi.remove(deleting.value.id)
  saving.value = false
  if (!response.success) return toast.show(response.message, 'error')
  confirmOpen.value = false
  toast.show(response.message, 'success')
  await loadRules()
}

async function toggleRule(rule: AiProactiveReplyRule) {
  const response = await aiProactiveReplyApi.update(rule.id, {
    bot_qq: rule.bot_qq,
    group_id: rule.group_id,
    is_enabled: !rule.is_enabled,
    min_interval_seconds: rule.min_interval_seconds,
    max_interval_seconds: rule.max_interval_seconds,
  })
  if (!response.success) return toast.show(response.message, 'error')
  rule.is_enabled = !rule.is_enabled
  toast.show(rule.is_enabled ? 'AI 主动回复已启用' : 'AI 主动回复已停用', 'success')
}

function formatInterval(seconds: number) {
  return seconds % 60 === 0 ? `${seconds / 60} 分钟` : `${seconds} 秒`
}

onMounted(() => {
  void loadRules()
  void loadBots()
})

watch(() => form.bot_qq, (botQq) => {
  void loadGroups(botQq)
})
</script>

<template>
  <div class="page-enter proactive-reply-page">
    <div class="page-heading">
      <div><p class="eyebrow">AI PROACTIVE</p><h1>AI 主动回复</h1><p>机器人会在随机时间从近期群消息中选择一条，自然参与对话，不引用原消息。</p></div>
      <button type="button" class="button primary" @click="openCreate"><Plus :size="18" />添加主动回复</button>
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
      <div class="panel-heading"><div><h2><BrainCircuit :size="17" />主动回复规则</h2><p>共 {{ result.total }} 条规则<span v-if="isAdmin()"> · 管理员视图</span></p></div><button type="button" class="icon-button" title="刷新列表" @click="loadRules"><RefreshCw :size="18" /></button></div>
      <div class="table-scroll rules-table proactive-table">
        <table>
          <thead><tr><th>机器人 QQ</th><th>群范围</th><th>随机间隔</th><th>下次执行</th><th v-if="isAdmin()">所属用户</th><th>状态</th><th>更新时间</th><th class="action-column">操作</th></tr></thead>
          <tbody>
            <tr v-for="rule in result.items" :key="rule.id">
              <td class="mono-text">{{ rule.bot_qq }}</td>
              <td><span class="scope-chip">{{ rule.group_id || '所有群' }}</span></td>
              <td>{{ formatInterval(rule.min_interval_seconds) }} ~ {{ formatInterval(rule.max_interval_seconds) }}</td>
              <td class="muted-text">{{ rule.is_enabled ? (rule.next_run_at || '等待调度') : '已停用' }}</td>
              <td v-if="isAdmin()" class="muted-text">{{ rule.owner_username }}</td>
              <td><button type="button" class="status-toggle" :class="{ active: rule.is_enabled }" :title="rule.is_enabled ? '点击停用' : '点击启用'" @click="toggleRule(rule)"><i />{{ rule.is_enabled ? '已启用' : '已停用' }}</button></td>
              <td class="muted-text">{{ rule.updated_at }}</td>
              <td class="action-buttons"><button type="button" class="icon-button" title="编辑主动回复" @click="openEdit(rule)"><Edit3 :size="16" /></button><button type="button" class="icon-button danger-hover" title="删除主动回复" @click="askDelete(rule)"><Trash2 :size="16" /></button></td>
            </tr>
          </tbody>
        </table>
        <EmptyState v-if="!loading && !result.items.length" text="还没有 AI 主动回复规则" />
      </div>
      <div class="pagination"><span>第 {{ result.page }} / {{ result.pages }} 页，共 {{ result.total }} 条</span><div><select v-model.number="query.page_size" title="每页条数" @change="query.page = 1; loadRules()"><option :value="10">10 条/页</option><option :value="20">20 条/页</option><option :value="50">50 条/页</option><option :value="100">100 条/页</option></select><button type="button" class="page-button" :disabled="result.page <= 1" @click="changePage(result.page - 1)">上一页</button><button type="button" class="page-button" :disabled="result.page >= result.pages" @click="changePage(result.page + 1)">下一页</button></div></div>
    </section>
    <LoadingOverlay :visible="loading" contained />

    <BaseModal :open="modalOpen" :title="editing ? '编辑 AI 主动回复' : '添加 AI 主动回复'" @close="modalOpen = false">
      <form class="rule-form" @submit.prevent="saveRule">
        <label><span class="field-label">机器人 QQ<span class="required">*</span></span><select v-model="form.bot_qq" required><option value="" disabled>请选择机器人</option><option v-for="bot in bots" :key="bot.id" :value="bot.bot_qq">{{ bot.nickname || bot.bot_qq }} · {{ bot.bot_qq }}</option></select></label>
        <label><span class="field-label">群号</span><select v-model="form.group_id" :disabled="!form.bot_qq || groupsLoading"><option value="">全部群（默认规则）</option><option v-if="editing && form.group_id && !groups.some((group) => group.group_id === form.group_id)" :value="form.group_id">{{ form.group_id }}（当前规则）</option><option v-for="group in groups" :key="group.id" :value="group.group_id">{{ group.group_name || group.group_id }} · {{ group.group_id }}</option></select><small v-if="groupsLoading">正在加载群列表...</small><small v-else-if="form.bot_qq && !groups.length">暂无已维护群，选择“全部群”仍可保存。</small><small v-else>指定群规则优先于该机器人的默认规则。</small></label>
        <div class="form-grid">
          <label>最短间隔（秒）<input v-model.number="form.min_interval_seconds" type="number" min="60" max="86400" required /><small class="field-hint">默认 60 秒（1 分钟）。</small></label>
          <label>最长间隔（秒）<input v-model.number="form.max_interval_seconds" type="number" :min="form.min_interval_seconds" max="86400" required /><small class="field-hint">默认 300 秒（5 分钟）。</small></label>
        </div>
        <label class="switch-field"><span>启用主动回复</span><button type="button" class="switch" :class="{ active: form.is_enabled }" role="switch" :aria-checked="form.is_enabled" @click="form.is_enabled = !form.is_enabled"><i /></button></label>
        <p class="rule-scope-note">需要先启用该机器人的 AI 配置；系统只会从最近 10 分钟的普通群消息中随机选择内容。</p>
        <div class="modal-form-actions"><button type="button" class="button secondary" :disabled="saving" @click="modalOpen = false">取消</button><button class="button primary" type="submit" :disabled="saving || !bots.length"><span v-if="saving" class="button-spinner" />保存规则</button></div>
      </form>
    </BaseModal>
    <ConfirmDialog :open="confirmOpen" title="删除 AI 主动回复" :message="`确定删除“${deleting?.group_id || '所有群'}”的主动回复规则吗？`" :loading="saving" @close="confirmOpen = false" @confirm="confirmDelete" />
  </div>
</template>
