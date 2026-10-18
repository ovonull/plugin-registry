<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { Edit3, Filter, Plus, RefreshCw, Search, Trash2, X } from 'lucide-vue-next'
import { autoReplyApi, botApi } from '../api'
import type { AutoReplyRule, BotAccount, Pagination, RulePayload } from '../types'
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
const editing = ref<AutoReplyRule | null>(null)
const deleting = ref<AutoReplyRule | null>(null)
const query = reactive({ keyword: '', bot_qq: '', enabled: '', page: 1, page_size: 20 })
const result = ref<Pagination<AutoReplyRule>>({ items: [], page: 1, page_size: 20, total: 0, pages: 1 })
const bots = ref<BotAccount[]>([])
const form = reactive<RulePayload>({ keyword: '', bot_qq: '', reply_content: '', is_enabled: true })
const isAdmin = () => auth.state.user?.role_code === 'admin'

function resetForm() { form.keyword = ''; form.bot_qq = ''; form.reply_content = ''; form.is_enabled = true }
function openCreate() { editing.value = null; resetForm(); modalOpen.value = true }
function openEdit(rule: AutoReplyRule) { editing.value = rule; form.keyword = rule.keyword; form.bot_qq = rule.bot_qq; form.reply_content = rule.reply_content; form.is_enabled = rule.is_enabled; modalOpen.value = true }

async function loadRules() {
  loading.value = true
  const params = { ...query, enabled: query.enabled === '' ? undefined : query.enabled === 'true' }
  const response = await autoReplyApi.list(params)
  loading.value = false
  if (!response.success) { toast.show(response.message, 'error'); return }
  result.value = response.data
}

async function loadBots() {
  const response = await botApi.list()
  if (response.success) bots.value = response.data.filter((item) => item.is_enabled)
}

function search() { query.page = 1; loadRules() }
function resetFilters() { query.keyword = ''; query.bot_qq = ''; query.enabled = ''; query.page = 1; loadRules() }
function changePage(page: number) { if (page < 1 || page > result.value.pages) return; query.page = page; loadRules() }

async function saveRule() {
  saving.value = true
  const response = editing.value ? await autoReplyApi.update(editing.value.id, form) : await autoReplyApi.create(form)
  saving.value = false
  if (!response.success) { toast.show(response.message, 'error'); return }
  modalOpen.value = false
  toast.show(editing.value ? '规则已保存' : '规则已添加', 'success')
  loadRules()
}

function askDelete(rule: AutoReplyRule) { deleting.value = rule; confirmOpen.value = true }
async function confirmDelete() {
  if (!deleting.value) return
  saving.value = true
  const response = await autoReplyApi.remove(deleting.value.id)
  saving.value = false
  if (!response.success) { toast.show(response.message, 'error'); return }
  confirmOpen.value = false; toast.show('规则已删除', 'success'); loadRules()
}

async function toggleRule(rule: AutoReplyRule) {
  const response = await autoReplyApi.update(rule.id, { keyword: rule.keyword, bot_qq: rule.bot_qq, reply_content: rule.reply_content, is_enabled: !rule.is_enabled })
  if (!response.success) { toast.show(response.message, 'error'); return }
  rule.is_enabled = !rule.is_enabled
  toast.show(rule.is_enabled ? '规则已启用' : '规则已停用', 'success')
}

onMounted(() => { loadRules(); loadBots() })
</script>

<template>
  <div class="page-enter auto-reply-page">
    <div class="page-heading"><div><p class="eyebrow">AUTOMATION</p><h1>自动回复</h1><p>按关键字为不同机器人配置消息回复规则。</p></div><button type="button" class="button primary" @click="openCreate"><Plus :size="18" />添加规则</button></div>
    <section class="filter-panel"><div class="filter-title"><Filter :size="17" /><strong>筛选条件</strong></div><div class="filter-fields"><label>关键字<input v-model="query.keyword" placeholder="搜索关键字" @keyup.enter="search" /></label><label>机器人 QQ<input v-model="query.bot_qq" inputmode="numeric" placeholder="输入 QQ 号" @keyup.enter="search" /></label><label>状态<select v-model="query.enabled"><option value="">全部状态</option><option value="true">已启用</option><option value="false">已停用</option></select></label><div class="filter-actions"><button type="button" class="button primary" @click="search"><Search :size="16" />查询</button><button type="button" class="button ghost" title="重置筛选" @click="resetFilters"><RefreshCw :size="16" />重置</button></div></div></section>
    <section class="content-panel rules-panel"><div class="panel-heading"><div><h2>规则列表</h2><p>共 {{ result.total }} 条规则<span v-if="isAdmin()"> · 管理员视图</span></p></div><button type="button" class="icon-button" title="刷新列表" @click="loadRules"><RefreshCw :size="18" /></button></div><div class="table-scroll rules-table"><table><thead><tr><th>关键字</th><th>机器人 QQ</th><th>回复内容</th><th v-if="isAdmin()">所属用户</th><th>状态</th><th>更新时间</th><th class="action-column">操作</th></tr></thead><tbody><tr v-for="rule in result.items" :key="rule.id"><td><span class="keyword-chip">{{ rule.keyword }}</span></td><td class="mono-text">{{ rule.bot_qq }}</td><td class="reply-cell" :title="rule.reply_content">{{ rule.reply_content }}</td><td v-if="isAdmin()" class="muted-text">{{ rule.owner_username }}</td><td><button type="button" class="status-toggle" :class="{ active: rule.is_enabled }" :title="rule.is_enabled ? '点击停用' : '点击启用'" @click="toggleRule(rule)"><i />{{ rule.is_enabled ? '已启用' : '已停用' }}</button></td><td class="muted-text">{{ rule.updated_at }}</td><td class="action-buttons"><button type="button" class="icon-button" title="编辑规则" @click="openEdit(rule)"><Edit3 :size="16" /></button><button type="button" class="icon-button danger-hover" title="删除规则" @click="askDelete(rule)"><Trash2 :size="16" /></button></td></tr></tbody></table><EmptyState v-if="!loading && !result.items.length" text="还没有匹配的自动回复规则" /></div><div class="pagination"><span>第 {{ result.page }} / {{ result.pages }} 页，共 {{ result.total }} 条</span><div><select v-model.number="query.page_size" title="每页条数" @change="query.page = 1; loadRules()"><option :value="10">10 条/页</option><option :value="20">20 条/页</option><option :value="50">50 条/页</option><option :value="100">100 条/页</option></select><button type="button" class="page-button" :disabled="result.page <= 1" @click="changePage(result.page - 1)">上一页</button><button type="button" class="page-button" :disabled="result.page >= result.pages" @click="changePage(result.page + 1)">下一页</button></div></div></section>
    <LoadingOverlay :visible="loading" contained />

    <BaseModal :open="modalOpen" :title="editing ? '编辑自动回复' : '添加自动回复'" @close="modalOpen = false">
      <form class="rule-form" @submit.prevent="saveRule">
        <label><span class="field-label">关键字<span class="required">*</span></span><input v-model="form.keyword" maxlength="100" required placeholder="例如：帮助" /></label>
        <label><span class="field-label">机器人 QQ<span class="required">*</span></span>
          <select v-model="form.bot_qq" required>
            <option value="" disabled>请选择机器人</option>
            <option v-for="bot in bots" :key="bot.id" :value="bot.bot_qq">{{ bot.nickname || bot.bot_qq }} · {{ bot.bot_qq }}</option>
          </select>
        </label>
        <p class="rule-scope-note">群消息范围由“机器人管理”中的群策略统一控制。</p>
        <label><span class="field-label">回复内容<span class="required">*</span></span><textarea v-model="form.reply_content" maxlength="2000" required rows="5" placeholder="输入命中关键字后发送的内容" /><small>{{ form.reply_content.length }} / 2000</small></label>
        <label class="switch-field"><span>启用规则</span><button type="button" class="switch" :class="{ active: form.is_enabled }" role="switch" :aria-checked="form.is_enabled" @click="form.is_enabled = !form.is_enabled"><i /></button></label>
        <div class="modal-form-actions"><button type="button" class="button secondary" :disabled="saving" @click="modalOpen = false">取消</button><button class="button primary" type="submit" :disabled="saving"><span v-if="saving" class="button-spinner" />保存规则</button></div>
      </form>
    </BaseModal>
    <ConfirmDialog :open="confirmOpen" title="删除自动回复" :message="`确定删除关键字“${deleting?.keyword || ''}”的规则吗？删除后不可在列表中恢复。`" :loading="saving" @close="confirmOpen = false" @confirm="confirmDelete" />
  </div>
</template>
