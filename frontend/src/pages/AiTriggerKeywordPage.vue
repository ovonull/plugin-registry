<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { Edit3, Filter, KeyRound, Plus, RefreshCw, Search, Trash2 } from 'lucide-vue-next'
import { aiTriggerKeywordApi, botApi } from '../api'
import type {
  AiTriggerKeywordCreatePayload,
  AiTriggerKeywordRule,
  BotAccount,
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
const modalOpen = ref(false)
const confirmOpen = ref(false)
const editing = ref<AiTriggerKeywordRule | null>(null)
const deleting = ref<AiTriggerKeywordRule | null>(null)
const batchDeleting = ref(false)
const selectedIds = ref<number[]>([])
const bots = ref<BotAccount[]>([])
const query = reactive({ keyword: '', bot_qq: '', enabled: '', page: 1, page_size: 20 })
const result = ref<Pagination<AiTriggerKeywordRule>>({ items: [], page: 1, page_size: 20, total: 0, pages: 1 })
const form = reactive<AiTriggerKeywordCreatePayload>({ bot_qq: '', keywords: '', is_enabled: true, reply_all: false, random_reply: false, random_reply_probability: 50 })
const isAdmin = () => auth.state.user?.role_code === 'admin'
const allVisibleSelected = computed(() => (
  result.value.items.length > 0
  && result.value.items.every((item) => selectedIds.value.includes(item.id))
))

function resetForm() {
  Object.assign(form, { bot_qq: '', keywords: '', is_enabled: true, reply_all: false, random_reply: false, random_reply_probability: 50 })
}

function openCreate() {
  editing.value = null
  resetForm()
  if (bots.value.length === 1) form.bot_qq = bots.value[0].bot_qq
  modalOpen.value = true
}

function openEdit(rule: AiTriggerKeywordRule) {
  editing.value = rule
  Object.assign(form, { bot_qq: rule.bot_qq, keywords: rule.keyword, is_enabled: rule.is_enabled, reply_all: rule.reply_all, random_reply: rule.random_reply, random_reply_probability: rule.random_reply_probability ?? 50 })
  modalOpen.value = true
}

async function loadRules() {
  loading.value = true
  const response = await aiTriggerKeywordApi.list({
    ...query,
    enabled: query.enabled === '' ? undefined : query.enabled === 'true',
  })
  loading.value = false
  if (!response.success) {
    toast.show(response.message, 'error')
    return
  }
  result.value = response.data
  selectedIds.value = []
}

async function loadBots() {
  const response = await botApi.list()
  if (!response.success) {
    toast.show(response.message, 'error')
    return
  }
  bots.value = response.data.filter((item) => item.is_enabled)
}

function search() {
  query.page = 1
  void loadRules()
}

function resetFilters() {
  Object.assign(query, { keyword: '', bot_qq: '', enabled: '', page: 1 })
  void loadRules()
}

function changePage(page: number) {
  if (page < 1 || page > result.value.pages) return
  query.page = page
  void loadRules()
}

async function saveRule() {
  saving.value = true
  const response = editing.value
    ? await aiTriggerKeywordApi.update(editing.value.id, {
      bot_qq: form.bot_qq,
      keyword: form.keywords,
      is_enabled: form.is_enabled,
      reply_all: form.reply_all,
      random_reply: form.random_reply,
      random_reply_probability: form.random_reply_probability,
    })
    : await aiTriggerKeywordApi.create(form)
  saving.value = false
  if (!response.success) {
    toast.show(response.message, 'error')
    return
  }
  modalOpen.value = false
  toast.show(response.message, 'success')
  await loadRules()
}

function toggleSelected(ruleId: number, event: Event) {
  const checked = (event.target as HTMLInputElement).checked
  selectedIds.value = checked
    ? Array.from(new Set([...selectedIds.value, ruleId]))
    : selectedIds.value.filter((id) => id !== ruleId)
}

function setReplyAll(enabled: boolean) {
  form.reply_all = enabled
  if (enabled) form.random_reply = false
}

function setRandomReply(enabled: boolean) {
  form.random_reply = enabled
  if (enabled) form.reply_all = false
}

function toggleAllVisible(event: Event) {
  const checked = (event.target as HTMLInputElement).checked
  const visibleIds = result.value.items.map((item) => item.id)
  selectedIds.value = checked
    ? Array.from(new Set([...selectedIds.value, ...visibleIds]))
    : selectedIds.value.filter((id) => !visibleIds.includes(id))
}

function askDelete(rule: AiTriggerKeywordRule | null, batch = false) {
  deleting.value = rule
  batchDeleting.value = batch
  confirmOpen.value = true
}

async function confirmDelete() {
  if (!deleting.value && !batchDeleting.value) return
  saving.value = true
  const response = batchDeleting.value
    ? await aiTriggerKeywordApi.batchRemove(selectedIds.value)
    : await aiTriggerKeywordApi.remove(deleting.value!.id)
  saving.value = false
  if (!response.success) {
    toast.show(response.message, 'error')
    return
  }
  confirmOpen.value = false
  deleting.value = null
  batchDeleting.value = false
  selectedIds.value = []
  toast.show(response.message, 'success')
  await loadRules()
}

async function toggleRule(rule: AiTriggerKeywordRule) {
  const response = await aiTriggerKeywordApi.update(rule.id, {
    bot_qq: rule.bot_qq,
    keyword: rule.keyword,
    is_enabled: !rule.is_enabled,
    reply_all: rule.reply_all,
    random_reply: rule.random_reply,
    random_reply_probability: rule.random_reply_probability,
  })
  if (!response.success) {
    toast.show(response.message, 'error')
    return
  }
  rule.is_enabled = !rule.is_enabled
  toast.show(rule.is_enabled ? 'AI 触发关键词已启用' : 'AI 触发关键词已停用', 'success')
}

onMounted(() => {
  void loadRules()
  void loadBots()
})
</script>

<template>
  <div class="page-enter forbidden-word-page ai-trigger-keyword-page">
    <div class="page-heading">
      <div><p class="eyebrow">AI AUTOMATION</p><h1>AI 触发关键词</h1><p>消息命中关键词或开启回复全部后触发 AI 回复，并登记后续免触发次数。</p></div>
      <button type="button" class="button primary" @click="openCreate"><Plus :size="18" />添加关键词</button>
    </div>

    <section class="filter-panel">
      <div class="filter-title"><Filter :size="17" /><strong>筛选条件</strong></div>
      <div class="filter-fields">
        <label>关键词<input v-model="query.keyword" placeholder="搜索关键词" @keyup.enter="search" /></label>
        <label>机器人 QQ<select v-model="query.bot_qq"><option value="">全部机器人</option><option v-for="bot in bots" :key="bot.id" :value="bot.bot_qq">{{ bot.nickname || bot.bot_qq }} · {{ bot.bot_qq }}</option></select></label>
        <label>状态<select v-model="query.enabled"><option value="">全部状态</option><option value="true">已启用</option><option value="false">已停用</option></select></label>
        <div class="filter-actions"><button type="button" class="button primary" @click="search"><Search :size="16" />查询</button><button type="button" class="button ghost" title="重置筛选" @click="resetFilters"><RefreshCw :size="16" />重置</button></div>
      </div>
    </section>

    <section class="content-panel rules-panel">
      <div class="panel-heading"><div><h2><KeyRound :size="17" />关键词规则</h2><p>共 {{ result.total }} 条规则<span v-if="isAdmin()"> · 管理员视图</span></p></div><div class="panel-heading-actions"><button v-if="selectedIds.length" type="button" class="button danger" :disabled="saving" @click="askDelete(null, true)"><Trash2 :size="16" />删除已选（{{ selectedIds.length }}）</button><button type="button" class="icon-button" title="刷新列表" @click="loadRules"><RefreshCw :size="18" /></button></div></div>
      <div class="table-scroll rules-table forbidden-word-table">
        <table>
          <thead><tr><th class="checkbox-column"><input class="table-checkbox" type="checkbox" :checked="allVisibleSelected" :indeterminate="selectedIds.length > 0 && !allVisibleSelected" aria-label="全选当前页" @change="toggleAllVisible" /></th><th>关键词列表</th><th>机器人 QQ</th><th>回复模式</th><th v-if="isAdmin()">所属用户</th><th>状态</th><th>更新时间</th><th class="action-column">操作</th></tr></thead>
          <tbody>
            <tr v-for="rule in result.items" :key="rule.id">
              <td class="checkbox-column"><input class="table-checkbox" type="checkbox" :checked="selectedIds.includes(rule.id)" :aria-label="`选择关键词列表 ${rule.keyword}`" @change="toggleSelected(rule.id, $event)" /></td>
              <td><span class="keyword-chip" :title="rule.keyword">{{ rule.keyword }}</span></td>
              <td class="mono-text">{{ rule.bot_qq }}</td>
              <td><span class="scope-tag" :class="{ neutral: !rule.reply_all && !rule.random_reply }">{{ rule.reply_all ? '回复全部' : rule.random_reply ? `随机回复 ${rule.random_reply_probability}%` : '命中关键词' }}</span></td>
              <td v-if="isAdmin()" class="muted-text">{{ rule.owner_username }}</td>
              <td><button type="button" class="status-toggle" :class="{ active: rule.is_enabled }" :title="rule.is_enabled ? '点击停用' : '点击启用'" @click="toggleRule(rule)"><i />{{ rule.is_enabled ? '已启用' : '已停用' }}</button></td>
              <td class="muted-text">{{ rule.updated_at }}</td>
              <td class="action-buttons"><button type="button" class="icon-button" title="编辑关键词" @click="openEdit(rule)"><Edit3 :size="16" /></button><button type="button" class="icon-button danger-hover" title="删除关键词" @click="askDelete(rule)"><Trash2 :size="16" /></button></td>
            </tr>
          </tbody>
        </table>
        <EmptyState v-if="!loading && !result.items.length" text="还没有 AI 触发关键词" />
      </div>
      <div class="pagination"><span>第 {{ result.page }} / {{ result.pages }} 页，共 {{ result.total }} 条</span><div><select v-model.number="query.page_size" title="每页条数" @change="query.page = 1; loadRules()"><option :value="10">10 条/页</option><option :value="20">20 条/页</option><option :value="50">50 条/页</option><option :value="100">100 条/页</option></select><button type="button" class="page-button" :disabled="result.page <= 1" @click="changePage(result.page - 1)">上一页</button><button type="button" class="page-button" :disabled="result.page >= result.pages" @click="changePage(result.page + 1)">下一页</button></div></div>
    </section>
    <LoadingOverlay :visible="loading" contained />

    <BaseModal :open="modalOpen" :title="editing ? '编辑 AI 触发关键词' : '添加 AI 触发关键词'" @close="modalOpen = false">
      <form class="rule-form" @submit.prevent="saveRule">
        <label><span class="field-label">机器人 QQ<span class="required">*</span></span><select v-model="form.bot_qq" required><option value="" disabled>请选择机器人</option><option v-for="bot in bots" :key="bot.id" :value="bot.bot_qq">{{ bot.nickname || bot.bot_qq }} · {{ bot.bot_qq }}</option></select></label>
        <label><span class="field-label">关键词列表（英文逗号分隔）<span class="required">*</span></span><textarea v-model="form.keywords" maxlength="5000" required rows="4" placeholder="例如：价格,套餐,怎么购买" /><small>同一个机器人只保存一条列表，多个关键词使用英文逗号分隔，重复关键词会自动去重。</small></label>
        <label class="switch-field"><span>启用规则</span><button type="button" class="switch" :class="{ active: form.is_enabled }" role="switch" :aria-checked="form.is_enabled" @click="form.is_enabled = !form.is_enabled"><i /></button></label>
        <label class="switch-field"><span><strong>回复全部</strong><small>开启后，该机器人所在群的每条消息都会触发 AI 回复。</small></span><button type="button" class="switch" :class="{ active: form.reply_all }" role="switch" :aria-checked="form.reply_all" @click="setReplyAll(!form.reply_all)"><i /></button></label>
        <label class="switch-field"><span><strong>随机回复</strong><small>开启后，普通群消息按设置的概率触发 AI；与回复全部互斥。</small></span><button type="button" class="switch" :class="{ active: form.random_reply }" role="switch" :aria-checked="form.random_reply" @click="setRandomReply(!form.random_reply)"><i /></button></label>
        <label v-if="form.random_reply">随机回复概率<input v-model.number="form.random_reply_probability" type="number" min="0" max="100" step="1" required /><small>范围 0% 至 100%；设置为 0% 时不会自动回复普通消息。</small></label>
        <div class="modal-form-actions"><button type="button" class="button secondary" :disabled="saving" @click="modalOpen = false">取消</button><button class="button primary" type="submit" :disabled="saving"><span v-if="saving" class="button-spinner" />保存规则</button></div>
      </form>
    </BaseModal>
    <ConfirmDialog :open="confirmOpen" :title="batchDeleting ? '批量删除 AI 触发关键词' : '删除 AI 触发关键词'" :message="batchDeleting ? `确定删除已勾选的 ${selectedIds.length} 条关键词列表吗？` : `确定删除关键词列表“${deleting?.keyword || ''}”吗？`" :loading="saving" @close="confirmOpen = false" @confirm="confirmDelete" />
  </div>
</template>
