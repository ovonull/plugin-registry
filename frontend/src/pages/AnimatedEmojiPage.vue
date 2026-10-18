<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { Filter, RefreshCw, Search, Settings2, Sticker, Tags, Trash2 } from 'lucide-vue-next'
import { animatedEmojiApi, botApi, settingsApi } from '../api'
import type { AnimatedEmojiRecord, AnimatedEmojiSettings, BotAccount, Pagination } from '../types'
import BaseModal from '../components/common/BaseModal.vue'
import ConfirmDialog from '../components/common/ConfirmDialog.vue'
import EmptyState from '../components/common/EmptyState.vue'
import LoadingOverlay from '../components/common/LoadingOverlay.vue'
import { useAuth } from '../composables/useAuth'
import { useToast } from '../composables/useToast'

const auth = useAuth()
const toast = useToast()
const loading = ref(false)
const bots = ref<BotAccount[]>([])
const previewContainer = ref<HTMLElement | null>(null)
const previewUrls = ref<Map<number, string>>(new Map())
const previewLoadingIds = ref<Set<number>>(new Set())
const previewErrors = ref<Map<number, string>>(new Map())
const failedImageIds = ref<Set<number>>(new Set())
const selectedIds = ref<number[]>([])
const saving = ref(false)
const categoryModalOpen = ref(false)
const batchCategory = ref('')
const confirmOpen = ref(false)
const deleting = ref<AnimatedEmojiRecord | null>(null)
const batchDeleting = ref(false)
const promptModalOpen = ref(false)
const promptLoading = ref(false)
const promptSaving = ref(false)
const promptSettings = reactive<AnimatedEmojiSettings>({ classification_prompt: '' })
const query = reactive({ bot_qq: '', category: '', page: 1, page_size: 20 })
const result = ref<Pagination<AnimatedEmojiRecord>>({ items: [], page: 1, page_size: 20, total: 0, pages: 1 })
const allVisibleSelected = computed(() => (
  result.value.items.length > 0
  && result.value.items.every((item) => selectedIds.value.includes(item.id))
))
let previewObserver: IntersectionObserver | null = null
const isAdmin = () => auth.state.user?.role_code === 'admin'

function resetPreviewState() {
  previewObserver?.disconnect()
  previewUrls.value = new Map()
  previewLoadingIds.value = new Set()
  previewErrors.value = new Map()
  failedImageIds.value = new Set()
}

async function loadPreview(recordId: number) {
  if (
    previewUrls.value.has(recordId)
    || previewLoadingIds.value.has(recordId)
    || previewErrors.value.has(recordId)
  ) return
  previewLoadingIds.value = new Set([...previewLoadingIds.value, recordId])
  const response = await animatedEmojiApi.preview(recordId)
  const loadingIds = new Set(previewLoadingIds.value)
  loadingIds.delete(recordId)
  previewLoadingIds.value = loadingIds
  if (!response.success || !response.data?.data) {
    previewErrors.value = new Map([
      ...previewErrors.value,
      [recordId, response.message || '图片预览加载失败'],
    ])
    return
  }
  const mediaType = response.data.media_type?.startsWith('image/')
    ? response.data.media_type
    : 'image/jpeg'
  previewUrls.value = new Map([
    ...previewUrls.value,
    [recordId, `data:${mediaType};base64,${response.data.data}`],
  ])
}

function observePreviewSlots() {
  const container = previewContainer.value
  if (!container) return
  if (!previewObserver) {
    if (typeof IntersectionObserver === 'undefined') {
      result.value.items.forEach((item) => { void loadPreview(item.id) })
      return
    }
    previewObserver = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return
          const recordId = Number((entry.target as HTMLElement).dataset.previewId)
          if (!recordId) return
          previewObserver?.unobserve(entry.target)
          void loadPreview(recordId)
        })
      },
      { root: container, rootMargin: '120px' },
    )
  }
  container.querySelectorAll<HTMLElement>('[data-preview-id]').forEach((element) => {
    const recordId = Number(element.dataset.previewId)
    if (
      recordId
      && !previewUrls.value.has(recordId)
      && !previewLoadingIds.value.has(recordId)
      && !previewErrors.value.has(recordId)
    ) previewObserver?.observe(element)
  })
}

async function loadRecords() {
  loading.value = true
  const response = await animatedEmojiApi.list({
    page: query.page,
    page_size: query.page_size,
    bot_qq: query.bot_qq || undefined,
    category: query.category.trim() || undefined,
  })
  loading.value = false
  if (!response.success) return toast.show(response.message, 'error')
  result.value = response.data
  resetPreviewState()
  selectedIds.value = []
  await nextTick()
  observePreviewSlots()
}

async function loadBots() {
  const response = await botApi.list()
  if (!response.success) return toast.show(response.message, 'error')
  bots.value = response.data.filter((item) => item.is_enabled)
}

function search() {
  query.page = 1
  void loadRecords()
}

function resetFilters() {
  Object.assign(query, { bot_qq: '', category: '', page: 1 })
  void loadRecords()
}

function changePage(page: number) {
  if (page < 1 || page > result.value.pages) return
  query.page = page
  void loadRecords()
}

function changePageSize() {
  query.page = 1
  void loadRecords()
}

function display(value: string | null | undefined) {
  return value || '—'
}

function imageFailed(item: AnimatedEmojiRecord) {
  failedImageIds.value = new Set([...failedImageIds.value, item.id])
  previewErrors.value = new Map([...previewErrors.value, [item.id, '图片数据加载失败']])
}

function toggleSelected(recordId: number, event: Event) {
  const checked = (event.target as HTMLInputElement).checked
  selectedIds.value = checked
    ? Array.from(new Set([...selectedIds.value, recordId]))
    : selectedIds.value.filter((id) => id !== recordId)
}

function toggleAllVisible(event: Event) {
  const checked = (event.target as HTMLInputElement).checked
  const visibleIds = result.value.items.map((item) => item.id)
  selectedIds.value = checked
    ? Array.from(new Set([...selectedIds.value, ...visibleIds]))
    : selectedIds.value.filter((id) => !visibleIds.includes(id))
}

function askDelete(record: AnimatedEmojiRecord | null, batch = false) {
  if (!record && !selectedIds.value.length && batch) return
  deleting.value = record
  batchDeleting.value = batch
  confirmOpen.value = true
}

function openBatchCategory() {
  if (!selectedIds.value.length) return
  const selectedCategories = new Set(
    result.value.items
      .filter((item) => selectedIds.value.includes(item.id))
      .map((item) => item.category),
  )
  batchCategory.value = selectedCategories.size === 1
    ? Array.from(selectedCategories)[0]
    : ''
  categoryModalOpen.value = true
}

function closeBatchCategory() {
  if (!saving.value) categoryModalOpen.value = false
}

async function openPromptSettings() {
  promptModalOpen.value = true
  promptLoading.value = true
  const response = await settingsApi.group<AnimatedEmojiSettings>('animated_emoji')
  promptLoading.value = false
  if (!response.success || !response.data) {
    promptModalOpen.value = false
    return toast.show(response.message, 'error')
  }
  Object.assign(promptSettings, response.data)
}

function closePromptSettings() {
  if (!promptSaving.value) promptModalOpen.value = false
}

async function savePromptSettings() {
  const prompt = promptSettings.classification_prompt.trim()
  if (!prompt) return toast.show('请输入动画表情分类提示词', 'error')
  promptSaving.value = true
  const response = await settingsApi.saveAnimatedEmoji({ classification_prompt: prompt })
  promptSaving.value = false
  if (!response.success || !response.data) return toast.show(response.message, 'error')
  Object.assign(promptSettings, response.data)
  promptModalOpen.value = false
  toast.show(response.message, 'success')
}

async function saveBatchCategory() {
  const category = batchCategory.value.trim()
  if (!category) return toast.show('请填写动画表情分类', 'error')
  saving.value = true
  const response = await animatedEmojiApi.batchUpdateCategory({
    ids: selectedIds.value,
    category,
  })
  saving.value = false
  if (!response.success) return toast.show(response.message, 'error')
  categoryModalOpen.value = false
  toast.show(response.message, 'success')
  await loadRecords()
}

async function confirmDelete() {
  if (!deleting.value && !batchDeleting.value) return
  saving.value = true
  const response = batchDeleting.value
    ? await animatedEmojiApi.batchRemove(selectedIds.value)
    : await animatedEmojiApi.remove(deleting.value!.id)
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
  await loadRecords()
}

onMounted(() => {
  void Promise.all([loadBots(), loadRecords()])
})

onBeforeUnmount(() => {
  previewObserver?.disconnect()
  previewObserver = null
})
</script>

<template>
  <div class="page-enter animated-emoji-page">
    <div class="page-heading">
      <div><p class="eyebrow">ANIMATED EMOJI</p><h1>动画表情分类</h1><p>查看收到的 QQ 动画表情，分类任务只处理“未分类”记录。</p></div>
      <div class="panel-heading-actions"><button v-if="isAdmin()" type="button" class="button secondary" :disabled="promptLoading || promptSaving" title="设置动画表情分类提示词" @click="openPromptSettings"><Settings2 :size="17" />提示词设置</button><button type="button" class="button secondary" :disabled="loading" title="刷新动画表情" @click="loadRecords"><RefreshCw :size="17" :class="{ spin: loading }" />刷新</button></div>
    </div>

    <section class="filter-panel">
      <div class="filter-title"><Filter :size="17" /><strong>筛选条件</strong></div>
      <div class="filter-fields animated-emoji-filters">
        <label>机器人 QQ<select v-model="query.bot_qq"><option value="">全部机器人</option><option v-for="bot in bots" :key="bot.id" :value="bot.bot_qq">{{ bot.nickname || bot.bot_qq }} · {{ bot.bot_qq }}</option></select></label>
        <label>分类<input v-model="query.category" placeholder="例如：搞笑、疑惑" @keyup.enter="search" /></label>
        <div class="filter-actions"><button type="button" class="button primary" @click="search"><Search :size="16" />查询</button><button type="button" class="button ghost" title="重置筛选" @click="resetFilters"><RefreshCw :size="16" />重置</button></div>
      </div>
    </section>

    <section class="content-panel rules-panel">
      <div class="panel-heading"><div><h2><Sticker :size="17" />动画表情列表</h2><p>按接收时间倒序排列 · 共 {{ result.total }} 条</p></div><div class="panel-heading-actions"><button v-if="selectedIds.length" type="button" class="button secondary" :disabled="saving" @click="openBatchCategory"><Tags :size="16" />修改分类（{{ selectedIds.length }}）</button><button v-if="selectedIds.length" type="button" class="button danger" :disabled="saving" @click="askDelete(null, true)"><Trash2 :size="16" />删除已选（{{ selectedIds.length }}）</button><button type="button" class="icon-button" title="刷新列表" :disabled="loading" @click="loadRecords"><RefreshCw :size="18" :class="{ spin: loading }" /></button></div></div>
      <div ref="previewContainer" class="table-scroll rules-table animated-emoji-table">
        <table>
          <thead><tr><th class="checkbox-column"><input class="table-checkbox" type="checkbox" :checked="allVisibleSelected" :indeterminate="selectedIds.length > 0 && !allVisibleSelected" aria-label="全选当前页" @change="toggleAllVisible" /></th><th>图片</th><th>分类</th><th>机器人 QQ</th><th>群组</th><th>发送者</th><th>文件</th><th>接收时间</th><th>分类状态</th><th class="action-column">操作</th></tr></thead>
          <tbody>
            <tr v-for="item in result.items" :key="item.id">
              <td class="checkbox-column"><input class="table-checkbox" type="checkbox" :checked="selectedIds.includes(item.id)" :aria-label="`选择动画表情 ${item.file_name || item.id}`" @change="toggleSelected(item.id, $event)" /></td>
              <td><div class="animated-emoji-preview-slot" :data-preview-id="item.id"><img v-if="previewUrls.get(item.id) && !failedImageIds.has(item.id)" class="animated-emoji-preview" :src="previewUrls.get(item.id)" :alt="item.summary || '动画表情'" loading="lazy" @error="imageFailed(item)" /><span v-else-if="previewLoadingIds.has(item.id)" class="muted-text">加载中…</span><span v-else-if="failedImageIds.has(item.id) || previewErrors.has(item.id)" class="task-error" :title="previewErrors.get(item.id) || undefined">{{ previewErrors.get(item.id) || '图片地址已失效' }}</span><span v-else class="muted-text">—</span></div></td>
              <td><span class="keyword-chip" :class="{ 'uncategorized-chip': item.category === '未分类' }">{{ item.category }}</span></td>
              <td class="mono-text">{{ item.bot_qq }}</td>
              <td><span>{{ display(item.group_name || item.group_id) }}</span><small v-if="item.group_id" class="record-subtext">{{ item.group_id }}</small></td>
              <td><span>{{ display(item.sender_nickname) }}</span><small v-if="item.user_id" class="record-subtext">{{ item.user_id }}</small></td>
              <td class="record-content" :title="item.file_name">{{ display(item.file_name) }}</td>
              <td class="muted-text nowrap">{{ item.received_at }}</td>
              <td class="classification-status"><span v-if="item.classification_error" class="task-error" :title="item.classification_error">待重试：{{ item.classification_attempts }} 次</span><span v-else class="muted-text">{{ item.classified_at || '等待 AI 分类' }}</span></td>
              <td class="animated-emoji-actions"><div class="action-buttons"><button type="button" class="icon-button danger-hover" title="删除动画表情" @click="askDelete(item)"><Trash2 :size="16" /></button></div></td>
            </tr>
          </tbody>
        </table>
        <EmptyState v-if="!loading && !result.items.length" text="暂无动画表情记录" />
      </div>
      <div class="pagination"><span>第 {{ result.page }} / {{ result.pages }} 页，共 {{ result.total }} 条</span><div><select v-model.number="query.page_size" title="每页条数" @change="changePageSize"><option :value="10">10 条/页</option><option :value="20">20 条/页</option><option :value="50">50 条/页</option><option :value="100">100 条/页</option></select><button class="page-button" type="button" :disabled="result.page <= 1" @click="changePage(result.page - 1)">上一页</button><button class="page-button" type="button" :disabled="result.page >= result.pages" @click="changePage(result.page + 1)">下一页</button></div></div>
    </section>
    <LoadingOverlay :visible="loading" contained />
    <BaseModal :open="categoryModalOpen" title="批量修改分类" width="460px" @close="closeBatchCategory">
      <form class="rule-form" @submit.prevent="saveBatchCategory">
        <label><span class="field-label">目标分类<span class="required">*</span></span><input v-model="batchCategory" maxlength="100" required autofocus placeholder="例如：搞笑、疑惑" /><small>将同时修改已勾选的 {{ selectedIds.length }} 条动画表情；填写“未分类”后会重新进入自动分类队列。</small></label>
        <div class="modal-form-actions"><button type="button" class="button secondary" :disabled="saving" @click="closeBatchCategory">取消</button><button class="button primary" type="submit" :disabled="saving || !batchCategory.trim()"><span v-if="saving" class="button-spinner" />保存分类</button></div>
      </form>
    </BaseModal>
    <BaseModal :open="promptModalOpen" title="动画表情分类提示词设置" width="720px" @close="closePromptSettings">
      <div class="prompt-settings-body">
        <form class="rule-form" @submit.prevent="savePromptSettings">
          <label><span class="field-label">分类提示词<span class="required">*</span></span><textarea v-model="promptSettings.classification_prompt" required maxlength="10000" rows="12" placeholder="请输入图片分类提示词" /><small>该提示词会在动画表情自动分类时发送给 AI；请保留明确的分类要求和返回格式。</small></label>
          <div class="modal-form-actions"><button type="button" class="button secondary" :disabled="promptSaving" @click="closePromptSettings">取消</button><button class="button primary" type="submit" :disabled="promptSaving || !promptSettings.classification_prompt.trim()"><span v-if="promptSaving" class="button-spinner" />保存提示词</button></div>
        </form>
        <LoadingOverlay :visible="promptLoading" contained />
      </div>
    </BaseModal>
    <ConfirmDialog :open="confirmOpen" :title="batchDeleting ? '批量删除动画表情' : '删除动画表情'" :message="batchDeleting ? `确定删除已勾选的 ${selectedIds.length} 条动画表情记录吗？删除后不会再显示。` : `确定删除动画表情记录“${deleting?.file_name || deleting?.id || ''}”吗？删除后不会再显示。`" :loading="saving" @close="confirmOpen = false" @confirm="confirmDelete" />
  </div>
</template>
