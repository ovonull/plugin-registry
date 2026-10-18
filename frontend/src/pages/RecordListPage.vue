<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import {
  BrainCircuit,
  Download,
  Filter,
  MessageSquareText,
  RefreshCw,
  Search,
  Trash2,
} from 'lucide-vue-next'
import { useRoute } from 'vue-router'
import { chatRecordApi } from '../api'
import type { AiReplyRecord, ChatMessageRecord, Pagination } from '../types'
import ConfirmDialog from '../components/common/ConfirmDialog.vue'
import EmptyState from '../components/common/EmptyState.vue'
import LoadingOverlay from '../components/common/LoadingOverlay.vue'
import { useAuth } from '../composables/useAuth'
import { useToast } from '../composables/useToast'

// 后端批量删除单次最多接收 100 个 ID，与勾选数量上限保持一致。
const BATCH_DELETE_LIMIT = 100

const route = useRoute()
const auth = useAuth()
const toast = useToast()
const loading = ref(false)
const exporting = ref(false)
const deleting = ref(false)
const confirmOpen = ref(false)
const selectedIds = ref<number[]>([])
const query = reactive({
  bot_qq: '', group_id: '', keyword: '', post_type: '', ai_triggered: '', page: 1, page_size: 20,
})
const chatResult = ref<Pagination<ChatMessageRecord>>({ items: [], page: 1, page_size: 20, total: 0, pages: 1 })
const aiResult = ref<Pagination<AiReplyRecord>>({ items: [], page: 1, page_size: 20, total: 0, pages: 1 })
const isAi = computed(() => route.meta.recordType === 'ai')
const title = computed(() => isAi.value ? 'AI 回复记录' : '聊天记录')
const eyebrow = computed(() => isAi.value ? 'AI REPLY RECORDS' : 'CHAT RECORDS')
const result = computed(() => isAi.value ? aiResult.value : chatResult.value)
const isAdmin = computed(() => auth.state.user?.role_code === 'admin')
const allVisibleSelected = computed(() => (
  chatResult.value.items.length > 0
  && chatResult.value.items.every((item) => selectedIds.value.includes(item.id))
))

/** 组装聊天记录的筛选参数，列表查询和导出共用。 */
function chatFilters() {
  return {
    bot_qq: query.bot_qq.trim() || undefined,
    group_id: query.group_id.trim() || undefined,
    keyword: query.keyword.trim() || undefined,
    post_type: query.post_type.trim() || undefined,
    ai_triggered: query.ai_triggered === '' ? undefined : query.ai_triggered === 'true',
  }
}

async function loadRecords() {
  loading.value = true
  const response = isAi.value
    ? await chatRecordApi.aiReplies({
      page: query.page, page_size: query.page_size,
      bot_qq: query.bot_qq.trim() || undefined, group_id: query.group_id.trim() || undefined,
    })
    : await chatRecordApi.messages({
      page: query.page, page_size: query.page_size, ...chatFilters(),
    })
  loading.value = false
  if (!response.success) {
    toast.show(response.message, 'error')
    return
  }
  if (isAi.value) aiResult.value = response.data as Pagination<AiReplyRecord>
  else chatResult.value = response.data as Pagination<ChatMessageRecord>
  selectedIds.value = []
}

function search() { query.page = 1; void loadRecords() }

function resetFilters() {
  Object.assign(query, { bot_qq: '', group_id: '', keyword: '', post_type: '', ai_triggered: '', page: 1 })
  void loadRecords()
}

function changePage(page: number) {
  if (page < 1 || page > result.value.pages) return
  query.page = page
  void loadRecords()
}

function changePageSize() { query.page = 1; void loadRecords() }

function display(value: string | null | undefined) { return value || '—' }

function toggleSelected(recordId: number, event: Event) {
  const checked = (event.target as HTMLInputElement).checked
  if (checked) {
    if (!selectedIds.value.includes(recordId)) selectedIds.value.push(recordId)
    return
  }
  selectedIds.value = selectedIds.value.filter((id) => id !== recordId)
}

function toggleAllVisible(event: Event) {
  const checked = (event.target as HTMLInputElement).checked
  const visibleIds = chatResult.value.items.map((item) => item.id)
  selectedIds.value = checked
    ? Array.from(new Set([...selectedIds.value, ...visibleIds]))
    : selectedIds.value.filter((id) => !visibleIds.includes(id))
}

function askBatchDelete() {
  if (!selectedIds.value.length) return
  if (selectedIds.value.length > BATCH_DELETE_LIMIT) {
    toast.show(`单次最多删除 ${BATCH_DELETE_LIMIT} 条聊天记录，请减少勾选数量`, 'warning')
    return
  }
  confirmOpen.value = true
}

async function confirmDelete() {
  deleting.value = true
  const response = await chatRecordApi.batchRemoveMessages(selectedIds.value)
  deleting.value = false
  if (!response.success) {
    toast.show(response.message, 'error')
    return
  }
  confirmOpen.value = false
  toast.show(response.message, 'success')
  await loadRecords()
}

/** 把后端返回的 Base64 内容还原为文件并触发浏览器下载。 */
function saveFile(fileName: string, mediaType: string, base64: string) {
  const binary = atob(base64)
  const bytes = new Uint8Array(binary.length)
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index)
  const url = URL.createObjectURL(new Blob([bytes], { type: mediaType }))
  const link = document.createElement('a')
  link.href = url
  link.download = fileName
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  URL.revokeObjectURL(url)
}

/** 按当前筛选条件导出全部聊天记录为 CSV。 */
async function exportRecords() {
  exporting.value = true
  const response = await chatRecordApi.exportMessages(chatFilters())
  exporting.value = false
  if (!response.success) {
    toast.show(response.message, 'error')
    return
  }
  saveFile(response.data.file_name, response.data.media_type, response.data.content)
  toast.show(response.message, 'success')
}

onMounted(() => { void loadRecords() })
watch(() => route.meta.recordType, () => {
  Object.assign(query, { bot_qq: '', group_id: '', keyword: '', post_type: '', ai_triggered: '', page: 1 })
  selectedIds.value = []
  void loadRecords()
})
</script>

<template>
  <div class="page-enter record-page">
    <div class="page-heading">
      <div><p class="eyebrow">{{ eyebrow }}</p><h1>{{ title }}</h1><p>{{ isAi ? '查看 AI 主动回复已处理的消息，按处理时间倒序排列。' : '查看机器人收发的聊天消息，按事件时间倒序排列。' }}</p></div>
      <button type="button" class="button secondary" title="刷新记录" :disabled="loading" @click="loadRecords"><RefreshCw :size="17" :class="{ spin: loading }" />刷新</button>
    </div>

    <section class="filter-panel">
      <div class="filter-title"><Filter :size="17" /><strong>筛选条件</strong></div>
      <div class="filter-fields record-filter-fields">
        <label>机器人 QQ<input v-model="query.bot_qq" inputmode="numeric" placeholder="输入 QQ 号" @keyup.enter="search" /></label>
        <label>群号<input v-model="query.group_id" inputmode="numeric" placeholder="留空显示全部" @keyup.enter="search" /></label>
        <template v-if="!isAi">
          <label>关键字<input v-model="query.keyword" placeholder="消息、昵称或原始内容" @keyup.enter="search" /></label>
          <label>事件类型<select v-model="query.post_type"><option value="">全部类型</option><option value="message">收到消息</option><option value="message_sent">发送消息</option><option value="notice">通知事件</option><option value="request">请求事件</option><option value="meta_event">元事件</option></select></label>
          <label>AI 处理状态<select v-model="query.ai_triggered"><option value="">全部状态</option><option value="true">已处理</option><option value="false">未处理</option></select></label>
        </template>
        <div class="filter-actions"><button type="button" class="button primary" @click="search"><Search :size="16" />查询</button><button type="button" class="button ghost" title="重置筛选" @click="resetFilters"><RefreshCw :size="16" />重置</button></div>
      </div>
    </section>

    <section class="content-panel rules-panel record-panel">
      <div class="panel-heading"><div><h2><BrainCircuit v-if="isAi" :size="17" /><MessageSquareText v-else :size="17" />{{ title }}</h2><p>共 {{ result.total }} 条<span v-if="isAdmin"> · 管理员视图</span><span v-if="!isAi && selectedIds.length"> · 已勾选 {{ selectedIds.length }} 条</span></p></div><div class="panel-heading-actions"><button v-if="!isAi && selectedIds.length" type="button" class="button danger" :disabled="deleting" @click="askBatchDelete"><Trash2 :size="16" />删除已选（{{ selectedIds.length }}）</button><button v-if="!isAi" type="button" class="button secondary" title="按当前筛选条件导出全部记录" :disabled="exporting || !chatResult.total" @click="exportRecords"><Download :size="16" :class="{ spin: exporting }" />导出 CSV</button><button type="button" class="icon-button" title="刷新列表" :disabled="loading" @click="loadRecords"><RefreshCw :size="18" :class="{ spin: loading }" /></button></div></div>

      <div v-if="isAi" class="table-scroll record-table ai-record-table">
        <table><thead><tr><th>处理时间</th><th>机器人 QQ</th><th>群号</th><th>触发消息</th><th>发送者</th><th>消息 ID</th><th v-if="isAdmin">所属用户</th></tr></thead>
          <tbody><tr v-for="item in aiResult.items" :key="item.id"><td class="muted-text nowrap">{{ item.processed_at }}</td><td class="mono-text">{{ item.bot_qq }}</td><td class="mono-text">{{ display(item.group_id) }}</td><td class="record-content" :title="item.source_message">{{ display(item.source_message) }}</td><td>{{ display(item.source_sender) }}</td><td class="mono-text">{{ display(item.message_id) }}</td><td v-if="isAdmin" class="muted-text">{{ item.owner_username }}</td></tr></tbody>
        </table><EmptyState v-if="!loading && !aiResult.items.length" text="暂无 AI 回复记录" />
      </div>

      <div v-else class="table-scroll record-table chat-record-table">
        <table><thead><tr><th class="checkbox-column"><input class="table-checkbox" type="checkbox" :checked="allVisibleSelected" :indeterminate="selectedIds.length > 0 && !allVisibleSelected" aria-label="全选当前页" @change="toggleAllVisible" /></th><th>事件时间</th><th>方向</th><th>机器人 QQ</th><th>群号</th><th>发送者</th><th>消息内容</th><th>事件类型</th><th>AI 状态</th><th v-if="isAdmin">所属用户</th></tr></thead>
          <tbody><tr v-for="item in chatResult.items" :key="item.id"><td class="checkbox-column"><input class="table-checkbox" type="checkbox" :checked="selectedIds.includes(item.id)" :aria-label="`选择消息 ${item.message_id || item.id}`" @change="toggleSelected(item.id, $event)" /></td><td class="muted-text nowrap">{{ item.event_time }}</td><td><span class="direction-chip" :class="{ bot: item.direction === '机器人发送' }">{{ item.direction }}</span></td><td class="mono-text">{{ item.bot_qq }}</td><td class="mono-text">{{ display(item.group_id) }}</td><td>{{ display(item.sender_nickname) }}<small v-if="item.sender_user_id" class="record-subtext">{{ item.sender_user_id }}</small></td><td class="record-content" :title="item.message_text">{{ display(item.message_text) }}</td><td><span class="record-type">{{ item.post_type || '未知' }}</span></td><td><span class="status-badge" :class="item.ai_triggered ? 'enabled' : 'disabled'"><i />{{ item.ai_triggered ? '已处理' : '未处理' }}</span></td><td v-if="isAdmin" class="muted-text">{{ item.owner_username }}</td></tr></tbody>
        </table><EmptyState v-if="!loading && !chatResult.items.length" text="暂无聊天记录" />
      </div>

      <div class="pagination"><span>第 {{ result.page }} / {{ result.pages }} 页，共 {{ result.total }} 条</span><div><select v-model.number="query.page_size" title="每页条数" @change="changePageSize"><option :value="10">10 条/页</option><option :value="20">20 条/页</option><option :value="50">50 条/页</option><option :value="100">100 条/页</option></select><button type="button" class="page-button" :disabled="result.page <= 1" @click="changePage(result.page - 1)">上一页</button><button type="button" class="page-button" :disabled="result.page >= result.pages" @click="changePage(result.page + 1)">下一页</button></div></div>
    </section>
    <LoadingOverlay :visible="loading" contained />
    <ConfirmDialog
      :open="confirmOpen"
      title="删除已选聊天记录"
      :message="`确定删除已勾选的 ${selectedIds.length} 条聊天记录吗？记录只做软删除，删除后不再出现在列表和导出结果中。`"
      :loading="deleting"
      @close="confirmOpen = false"
      @confirm="confirmDelete"
    />
  </div>
</template>
