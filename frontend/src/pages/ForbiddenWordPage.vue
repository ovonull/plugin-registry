<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { Edit3, Filter, Plus, RefreshCw, Search, ShieldAlert, Trash2 } from 'lucide-vue-next'
import { botApi, forbiddenWordApi } from '../api'
import type {
  BotAccount,
  BotGroup,
  ForbiddenWordCreatePayload,
  ForbiddenWordRule,
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
const editing = ref<ForbiddenWordRule | null>(null)
const deleting = ref<ForbiddenWordRule | null>(null)
const selectedIds = ref<number[]>([])
const batchDeleting = ref(false)
const bots = ref<BotAccount[]>([])
const groups = ref<BotGroup[]>([])
const groupsLoading = ref(false)
let groupsRequestId = 0
const query = reactive({ keyword: '', bot_qq: '', group_id: '', enabled: '', page: 1, page_size: 20 })
const result = ref<Pagination<ForbiddenWordRule>>({ items: [], page: 1, page_size: 20, total: 0, pages: 1 })
const form = reactive<ForbiddenWordCreatePayload>({
  bot_qq: '', group_id: '', keywords: '', mute_prompt: '', mute_duration: 300, mute_enabled: true, is_enabled: true,
})
const isAdmin = () => auth.state.user?.role_code === 'admin'
const allVisibleSelected = computed(() => (
  result.value.items.length > 0
  && result.value.items.every((item) => selectedIds.value.includes(item.id))
))

function resetForm() {
  Object.assign(form, { bot_qq: '', group_id: '', keywords: '', mute_prompt: '', mute_duration: 300, mute_enabled: true, is_enabled: true })
}

function openCreate() {
  editing.value = null
  resetForm()
  if (bots.value.length === 1) form.bot_qq = bots.value[0].bot_qq
  modalOpen.value = true
}

function openEdit(rule: ForbiddenWordRule) {
  editing.value = rule
  Object.assign(form, {
    bot_qq: rule.bot_qq,
    group_id: rule.group_id,
    keywords: rule.keyword,
    mute_prompt: rule.mute_prompt || '',
    mute_duration: rule.mute_duration,
    mute_enabled: rule.mute_enabled,
    is_enabled: rule.is_enabled,
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
  if (!response.success) {
    toast.show(response.message, 'error')
    return
  }
  groups.value = response.data.filter((item) => item.is_enabled)
}

async function loadRules() {
  loading.value = true
  const params = {
    ...query,
    enabled: query.enabled === '' ? undefined : query.enabled === 'true',
  }
  const response = await forbiddenWordApi.list(params)
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
  loadRules()
}

function resetFilters() {
  Object.assign(query, { keyword: '', bot_qq: '', group_id: '', enabled: '', page: 1 })
  loadRules()
}

function changePage(page: number) {
  if (page < 1 || page > result.value.pages) return
  query.page = page
  loadRules()
}

async function saveRule() {
  saving.value = true
  const response = editing.value
    ? await forbiddenWordApi.update(editing.value.id, {
      bot_qq: form.bot_qq,
      group_id: form.group_id,
      keyword: form.keywords,
      mute_prompt: form.mute_prompt,
      mute_duration: form.mute_duration,
      mute_enabled: form.mute_enabled,
      is_enabled: form.is_enabled,
    })
    : await forbiddenWordApi.create(form)
  saving.value = false
  if (!response.success) {
    toast.show(response.message, 'error')
    return
  }
  modalOpen.value = false
  toast.show(editing.value ? '违禁词规则已保存' : response.message, 'success')
  await loadRules()
}

function askDelete(rule: ForbiddenWordRule) {
  deleting.value = rule
  batchDeleting.value = false
  confirmOpen.value = true
}

function toggleSelected(ruleId: number, event: Event) {
  const checked = (event.target as HTMLInputElement).checked
  if (checked) {
    if (!selectedIds.value.includes(ruleId)) selectedIds.value.push(ruleId)
    return
  }
  selectedIds.value = selectedIds.value.filter((id) => id !== ruleId)
}

function toggleAllVisible(event: Event) {
  const checked = (event.target as HTMLInputElement).checked
  const visibleIds = result.value.items.map((item) => item.id)
  selectedIds.value = checked
    ? Array.from(new Set([...selectedIds.value, ...visibleIds]))
    : selectedIds.value.filter((id) => !visibleIds.includes(id))
}

function askBatchDelete() {
  if (!selectedIds.value.length) return
  deleting.value = null
  batchDeleting.value = true
  confirmOpen.value = true
}

async function confirmDelete() {
  if (!deleting.value && !batchDeleting.value) return
  saving.value = true
  const response = batchDeleting.value
    ? await forbiddenWordApi.batchRemove(selectedIds.value)
    : await forbiddenWordApi.remove(deleting.value!.id)
  saving.value = false
  if (!response.success) {
    toast.show(response.message, 'error')
    return
  }
  confirmOpen.value = false
  selectedIds.value = []
  deleting.value = null
  batchDeleting.value = false
  toast.show(response.message, 'success')
  await loadRules()
}

async function toggleRule(rule: ForbiddenWordRule) {
  const response = await forbiddenWordApi.update(rule.id, {
    bot_qq: rule.bot_qq,
    group_id: rule.group_id,
    keyword: rule.keyword,
    mute_prompt: rule.mute_prompt || '',
    mute_duration: rule.mute_duration,
    mute_enabled: rule.mute_enabled,
    is_enabled: !rule.is_enabled,
  })
  if (!response.success) {
    toast.show(response.message, 'error')
    return
  }
  rule.is_enabled = !rule.is_enabled
  toast.show(rule.is_enabled ? '违禁词已启用' : '违禁词已停用', 'success')
}

onMounted(() => {
  loadRules()
  loadBots()
})

watch(() => form.bot_qq, (botQq) => {
  void loadGroups(botQq)
})
</script>

<template>
  <div class="page-enter forbidden-word-page">
    <div class="page-heading">
      <div><p class="eyebrow">MODERATION</p><h1>违禁词列表</h1><p>按机器人和群配置违禁词，命中后撤回发言，并按规则同步禁言。</p></div>
      <button type="button" class="button primary" @click="openCreate"><Plus :size="18" />添加违禁词</button>
    </div>

    <section class="filter-panel">
      <div class="filter-title"><Filter :size="17" /><strong>筛选条件</strong></div>
      <div class="filter-fields">
        <label>违禁词<input v-model="query.keyword" placeholder="搜索关键词" @keyup.enter="search" /></label>
        <label>机器人 QQ<select v-model="query.bot_qq"><option value="">全部机器人</option><option v-for="bot in bots" :key="bot.id" :value="bot.bot_qq">{{ bot.nickname || bot.bot_qq }} · {{ bot.bot_qq }}</option></select></label>
        <label>群号<input v-model="query.group_id" inputmode="numeric" placeholder="留空查看全部" @keyup.enter="search" /></label>
        <label>状态<select v-model="query.enabled"><option value="">全部状态</option><option value="true">已启用</option><option value="false">已停用</option></select></label>
        <div class="filter-actions"><button type="button" class="button primary" @click="search"><Search :size="16" />查询</button><button type="button" class="button ghost" title="重置筛选" @click="resetFilters"><RefreshCw :size="16" />重置</button></div>
      </div>
    </section>

    <section class="content-panel rules-panel">
      <div class="panel-heading"><div><h2><ShieldAlert :size="17" />违禁词规则</h2><p>共 {{ result.total }} 条规则<span v-if="isAdmin()"> · 管理员视图</span></p></div><div class="panel-heading-actions"><button v-if="selectedIds.length" type="button" class="button danger" :disabled="saving" @click="askBatchDelete"><Trash2 :size="16" />删除已选（{{ selectedIds.length }}）</button><button type="button" class="icon-button" title="刷新列表" @click="loadRules"><RefreshCw :size="18" /></button></div></div>
      <div class="table-scroll rules-table forbidden-word-table">
        <table>
          <thead><tr><th class="checkbox-column"><input class="table-checkbox" type="checkbox" :checked="allVisibleSelected" :indeterminate="selectedIds.length > 0 && !allVisibleSelected" aria-label="全选当前页" @change="toggleAllVisible" /></th><th>违禁词列表</th><th>机器人 QQ</th><th>群范围</th><th>同步禁言</th><th>禁言时长</th><th>AI 禁言判断</th><th v-if="isAdmin()">所属用户</th><th>状态</th><th>更新时间</th><th class="action-column">操作</th></tr></thead>
          <tbody>
            <tr v-for="rule in result.items" :key="rule.id">
              <td class="checkbox-column"><input class="table-checkbox" type="checkbox" :checked="selectedIds.includes(rule.id)" :aria-label="`选择违禁词列表 ${rule.keyword}`" @change="toggleSelected(rule.id, $event)" /></td>
              <td><span class="keyword-chip" :title="rule.keyword">{{ rule.keyword }}</span></td>
              <td class="mono-text">{{ rule.bot_qq }}</td>
              <td class="mono-text">{{ rule.group_id || '全部群' }}</td>
              <td><span class="status-badge" :class="rule.mute_enabled ? 'enabled' : 'disabled'"><i />{{ rule.mute_enabled ? '已开启' : '已关闭' }}</span></td>
              <td>{{ rule.mute_duration }} 秒</td>
              <td class="record-content" :title="rule.mute_prompt || undefined">{{ rule.mute_prompt || '未配置' }}</td>
              <td v-if="isAdmin()" class="muted-text">{{ rule.owner_username }}</td>
              <td><button type="button" class="status-toggle" :class="{ active: rule.is_enabled }" :title="rule.is_enabled ? '点击停用' : '点击启用'" @click="toggleRule(rule)"><i />{{ rule.is_enabled ? '已启用' : '已停用' }}</button></td>
              <td class="muted-text">{{ rule.updated_at }}</td>
              <td class="action-buttons"><button type="button" class="icon-button" title="编辑违禁词" @click="openEdit(rule)"><Edit3 :size="16" /></button><button type="button" class="icon-button danger-hover" title="删除违禁词" @click="askDelete(rule)"><Trash2 :size="16" /></button></td>
            </tr>
          </tbody>
        </table>
        <EmptyState v-if="!loading && !result.items.length" text="还没有违禁词规则" />
      </div>
      <div class="pagination"><span>第 {{ result.page }} / {{ result.pages }} 页，共 {{ result.total }} 条</span><div><select v-model.number="query.page_size" title="每页条数" @change="query.page = 1; loadRules()"><option :value="10">10 条/页</option><option :value="20">20 条/页</option><option :value="50">50 条/页</option><option :value="100">100 条/页</option></select><button type="button" class="page-button" :disabled="result.page <= 1" @click="changePage(result.page - 1)">上一页</button><button type="button" class="page-button" :disabled="result.page >= result.pages" @click="changePage(result.page + 1)">下一页</button></div></div>
    </section>
    <LoadingOverlay :visible="loading" contained />

    <BaseModal :open="modalOpen" :title="editing ? '编辑违禁词' : '添加违禁词'" @close="modalOpen = false">
      <form class="rule-form" @submit.prevent="saveRule">
        <label><span class="field-label">机器人 QQ<span class="required">*</span></span><select v-model="form.bot_qq" required><option value="" disabled>请选择机器人</option><option v-for="bot in bots" :key="bot.id" :value="bot.bot_qq">{{ bot.nickname || bot.bot_qq }} · {{ bot.bot_qq }}</option></select></label>
        <label><span class="field-label">群号</span><select v-model="form.group_id" :disabled="!form.bot_qq || groupsLoading"><option value="">全部群</option><option v-if="editing && form.group_id && !groups.some((group) => group.group_id === form.group_id)" :value="form.group_id">{{ form.group_id }}（当前规则）</option><option v-for="group in groups" :key="group.id" :value="group.group_id">{{ group.group_name || group.group_id }} · {{ group.group_id }}</option></select><small v-if="groupsLoading">正在加载群列表...</small><small v-else-if="form.bot_qq && !groups.length">暂无已维护群，可先同步群列表；选择“全部群”仍可保存。</small></label>
        <p class="rule-scope-note">群号为空时，机器人在所有已允许的群中都会检查该违禁词。</p>
        <label><span class="field-label">违禁词列表（英文逗号分隔）<span class="required">*</span></span><textarea v-model="form.keywords" maxlength="5000" required rows="4" placeholder="例如：关键词1,关键词2,关键词3" /><small>每个机器人和群范围只保存一条列表，多个关键词使用英文逗号分隔，重复关键词会自动去重。</small></label>
        <label><span class="field-label">禁言提示词（可选）</span><textarea v-model="form.mute_prompt" maxlength="5000" rows="4" placeholder="例如：判断这条消息是否包含辱骂、诈骗或严重广告内容，需要时返回需要禁言。" /><small>填写后，未命中关键词的消息也会交给 AI 判断；AI 明确判定需要禁言时才会执行禁言。</small></label>
        <label><span class="field-label">禁言时长（秒）<span class="required">*</span></span><input v-model.number="form.mute_duration" type="number" min="1" max="2592000" required /></label>
        <label class="switch-field"><span>同步禁言</span><button type="button" class="switch" :class="{ active: form.mute_enabled }" role="switch" :aria-checked="form.mute_enabled" @click="form.mute_enabled = !form.mute_enabled"><i /></button></label>
        <label class="switch-field"><span>启用规则</span><button type="button" class="switch" :class="{ active: form.is_enabled }" role="switch" :aria-checked="form.is_enabled" @click="form.is_enabled = !form.is_enabled"><i /></button></label>
        <div class="modal-form-actions"><button type="button" class="button secondary" :disabled="saving" @click="modalOpen = false">取消</button><button class="button primary" type="submit" :disabled="saving"><span v-if="saving" class="button-spinner" />保存规则</button></div>
      </form>
    </BaseModal>
    <ConfirmDialog :open="confirmOpen" :title="batchDeleting ? '批量删除违禁词列表' : '删除违禁词列表'" :message="batchDeleting ? `确定删除已勾选的 ${selectedIds.length} 条违禁词列表吗？删除后不会再触发禁言。` : `确定删除违禁词列表“${deleting?.keyword || ''}”吗？删除后不会再触发禁言。`" :loading="saving" @close="confirmOpen = false" @confirm="confirmDelete" />
  </div>
</template>
