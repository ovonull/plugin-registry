<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import {
  Braces,
  CheckCircle2,
  Copy,
  ExternalLink,
  Play,
  RefreshCw,
  Search,
  Send,
  TerminalSquare,
} from 'lucide-vue-next'
import { botApi } from '../api'
import type { ActionCatalogItem, BotAccount, BotGroup } from '../types'
import ConfirmDialog from '../components/common/ConfirmDialog.vue'
import EmptyState from '../components/common/EmptyState.vue'
import LoadingOverlay from '../components/common/LoadingOverlay.vue'
import { useToast } from '../composables/useToast'

const toast = useToast()
const loading = ref(false)
const executing = ref(false)
const bots = ref<BotAccount[]>([])
const groups = ref<BotGroup[]>([])
const catalog = ref<ActionCatalogItem[]>([])
const selectedBotQq = ref('')
const selectedGroupId = ref('')
const selected = ref<ActionCatalogItem | null>(null)
const searchText = ref('')
const category = ref('全部')
const paramsText = ref('{}')
const resultText = ref('')
const confirmOpen = ref(false)

const categories = computed(() => ['全部', ...new Set(catalog.value.map((item) => item.category))])
const filteredCatalog = computed(() => catalog.value.filter((item) => {
  const categoryMatch = category.value === '全部' || item.category === category.value
  const needle = searchText.value.trim().toLowerCase()
  return categoryMatch && (!needle || item.name.toLowerCase().includes(needle) || item.action.toLowerCase().includes(needle))
}))

async function loadAll() {
  loading.value = true
  const [botResponse, catalogResponse] = await Promise.all([botApi.list(), botApi.catalog()])
  loading.value = false
  if (!botResponse.success) return toast.show(botResponse.message, 'error')
  if (!catalogResponse.success) return toast.show(catalogResponse.message, 'error')
  bots.value = botResponse.data.filter((item) => item.is_enabled)
  catalog.value = catalogResponse.data
  if (!selectedBotQq.value && bots.value.length) selectedBotQq.value = bots.value[0].bot_qq
  if (!selected.value && catalog.value.length) selectAction(catalog.value[0])
}

async function loadGroups() {
  groups.value = []
  selectedGroupId.value = ''
  if (!selectedBotQq.value) return
  const response = await botApi.groups(selectedBotQq.value)
  if (!response.success) return toast.show(response.message, 'error')
  groups.value = response.data.filter((item) => item.is_enabled)
}

function selectAction(item: ActionCatalogItem) {
  selected.value = item
  paramsText.value = JSON.stringify(item.example || {}, null, 2)
  resultText.value = ''
  syncSelectedGroupParam()
}

function syncSelectedGroupParam() {
  if (!selected.value?.requires_group) return
  try {
    const parsed = JSON.parse(paramsText.value || '{}')
    if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') return
    if (selectedGroupId.value) {
      parsed.group_id = selectedGroupId.value
    } else {
      delete parsed.group_id
    }
    paramsText.value = JSON.stringify(parsed, null, 2)
  } catch {
    // 保留用户当前的非法 JSON，避免选择群时覆盖编辑内容。
  }
}

function buildParams(): Record<string, unknown> | null {
  if (!selected.value || !selectedBotQq.value) return null
  try {
    const parsed = JSON.parse(paramsText.value || '{}')
    if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') throw new Error()
    if (selected.value.requires_group) {
      if (!selectedGroupId.value) {
        toast.show('请选择已启用的群', 'warning')
        return null
      }
      parsed.group_id = selectedGroupId.value
    }
    return parsed
  } catch {
    toast.show('参数必须是 JSON 对象', 'error')
    return null
  }
}

function requestExecute() {
  if (!buildParams()) return
  if (selected.value?.dangerous) {
    confirmOpen.value = true
    return
  }
  void execute()
}

async function execute() {
  if (!selected.value || !selectedBotQq.value) return
  const params = buildParams()
  if (!params) return
  confirmOpen.value = false
  executing.value = true
  const response = await botApi.execute(selectedBotQq.value, selected.value.action, params)
  executing.value = false
  resultText.value = JSON.stringify(response, null, 2)
  toast.show(response.message, response.success ? 'success' : 'error')
}

function copyResult() {
  if (!resultText.value) return
  navigator.clipboard.writeText(resultText.value).then(
    () => toast.show('执行结果已复制', 'success'),
    () => toast.show('复制失败，请手动选择结果文本', 'error'),
  )
}

watch(selectedBotQq, loadGroups)
watch(selectedGroupId, syncSelectedGroupParam)
onMounted(loadAll)
</script>

<template>
  <div class="page-enter api-center-page">
    <div class="page-heading">
      <div><p class="eyebrow">NAPCAT ACTIONS</p><h1>接口中心</h1><p>按机器人账号调用 NapCat OneBot V4 动作。</p></div>
      <button type="button" class="button secondary" @click="loadAll"><RefreshCw :size="17" />刷新目录</button>
    </div>

    <div class="api-workbench">
      <section class="content-panel action-catalog-panel">
        <div class="panel-heading"><div><h2><TerminalSquare :size="17" />动作目录</h2><p>{{ filteredCatalog.length }} / {{ catalog.length }} 个动作</p></div></div>
        <div class="catalog-filters">
          <div class="catalog-search"><Search :size="16" /><input v-model="searchText" placeholder="搜索名称或 action" /></div>
          <select v-model="category"><option v-for="item in categories" :key="item" :value="item">{{ item }}</option></select>
        </div>
        <div class="action-list">
          <button v-for="item in filteredCatalog" :key="item.action" type="button" :class="{ active: selected?.action === item.action }" @click="selectAction(item)">
            <span><strong>{{ item.name }}</strong><small>{{ item.category }}</small></span>
            <code>{{ item.action }}</code>
          </button>
          <EmptyState v-if="!filteredCatalog.length" text="没有匹配的动作" />
        </div>
      </section>

      <section class="content-panel action-executor-panel">
        <div class="panel-heading">
          <div><h2><Send :size="17" />{{ selected?.name || '动作执行' }}</h2><p class="mono-text">{{ selected?.action || '-' }}<template v-if="selected?.execute_action && selected.execute_action !== selected.action"> → {{ selected.execute_action }}</template></p></div>
          <div class="action-heading-tools">
            <span v-if="selected?.compatibility" class="scope-tag neutral">兼容动作</span>
            <span v-if="selected?.dangerous" class="scope-tag danger">危险操作</span>
            <span v-if="selected?.requires_group" class="scope-tag">群白名单校验</span>
            <a class="icon-button" href="https://napcat.apifox.cn" target="_blank" rel="noreferrer" title="查看 NapCat 官方文档"><ExternalLink :size="17" /></a>
          </div>
        </div>
        <form class="action-form" @submit.prevent="requestExecute">
          <div class="action-target-grid">
            <label>QQ 机器人<select v-model="selectedBotQq" required><option value="" disabled>选择机器人</option><option v-for="bot in bots" :key="bot.id" :value="bot.bot_qq">{{ bot.nickname || bot.bot_qq }} · {{ bot.bot_qq }}</option></select></label>
            <label v-if="selected?.requires_group">目标群<select v-model="selectedGroupId" required><option value="" disabled>选择已启用群</option><option v-for="group in groups" :key="group.id" :value="group.group_id">{{ group.group_name || group.group_id }} · {{ group.group_id }}</option></select></label>
          </div>
          <label class="json-editor-label"><span><Braces :size="16" />JSON 参数</span><textarea v-model="paramsText" rows="12" spellcheck="false" class="json-editor" /></label>
          <button type="submit" class="button primary execute-button" :disabled="executing || !selected || !selectedBotQq"><Play :size="17" /><span v-if="executing" class="button-spinner" />执行动作</button>
        </form>
        <div class="action-result">
          <div><span><CheckCircle2 :size="16" /><strong>执行结果</strong></span><button type="button" class="icon-button" title="复制执行结果" :disabled="!resultText" @click="copyResult"><Copy :size="16" /></button></div>
          <pre>{{ resultText || '{\n  "status": "waiting"\n}' }}</pre>
        </div>
      </section>
    </div>
    <LoadingOverlay :visible="loading" contained />
    <ConfirmDialog
      :open="confirmOpen"
      title="确认执行危险操作"
      :message="`即将通过机器人 ${selectedBotQq} 执行 ${selected?.name || selected?.action || ''}，请确认参数和目标无误。`"
      :loading="executing"
      confirm-text="确认执行"
      @close="confirmOpen = false"
      @confirm="execute"
    />
  </div>
</template>
