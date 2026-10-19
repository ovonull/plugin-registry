<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import {
  BadgeCheck, Ban, Check, CloudDownload, FileClock, Filter, ListChecks, RefreshCw, Search, ShieldCheck, UserPlus, X,
} from 'lucide-vue-next'
import { pluginAdminApi, pluginApi, publicApi, userApi } from '../api'
import type {
  DictionaryOption,
  ManagedUser,
  Pagination,
  PluginCapabilityItem,
  PluginReviewLog,
  PluginReviewVersion,
  PluginTrustedAuthor,
} from '../types'
import BaseModal from '../components/common/BaseModal.vue'
import ConfirmDialog from '../components/common/ConfirmDialog.vue'
import EmptyState from '../components/common/EmptyState.vue'
import LoadingOverlay from '../components/common/LoadingOverlay.vue'
import { useToast } from '../composables/useToast'

type Tab = 'versions' | 'logs' | 'trust'

const toast = useToast()
const route = useRoute()
const activeTab = ref<Tab>('versions')
const loading = ref(false)
const saving = ref(false)
const loaded = reactive<Record<Tab, boolean>>({ versions: false, logs: false, trust: false })
const reviewStatus = ref<DictionaryOption[]>([])
const reviewActions = ref<DictionaryOption[]>([])
const capabilityNames = ref<DictionaryOption[]>([])
const riskNames = ref<DictionaryOption[]>([])
const catalog = ref<Record<string, PluginCapabilityItem>>({})
const catalogReady = ref(false)
const users = ref<ManagedUser[]>([])
const syncing = ref(false)

const versionQuery = reactive({ plugin_id: '', review_status: '', keyword: '', page: 1, page_size: 20 })
const versionResult = ref<Pagination<PluginReviewVersion>>({ items: [], page: 1, page_size: 20, total: 0, pages: 1 })
const logQuery = reactive({ plugin_id: '', action: '', page: 1, page_size: 20 })
const logResult = ref<Pagination<PluginReviewLog>>({ items: [], page: 1, page_size: 20, total: 0, pages: 1 })
const trustQuery = reactive({ keyword: '', active: '', page: 1, page_size: 20 })
const trustResult = ref<Pagination<PluginTrustedAuthor>>({ items: [], page: 1, page_size: 20, total: 0, pages: 1 })

const approveTarget = ref<PluginReviewVersion | null>(null)
const approveOpen = ref(false)
const approveComment = ref('')
const approveSelection = ref<string[]>([])
const rejectTarget = ref<PluginReviewVersion | null>(null)
const rejectOpen = ref(false)
const rejectComment = ref('')
const grantOpen = ref(false)
const grantUserId = ref<number | ''>('')
const grantReason = ref('')

const confirmOpen = ref(false)
const confirmTitle = ref('')
const confirmMessage = ref('')
const confirmText = ref('确定')
let confirmHandler: (() => Promise<boolean>) | null = null

const tabs = [
  { value: 'versions' as const, label: '版本审核', icon: ListChecks },
  { value: 'logs' as const, label: '审核流水', icon: FileClock },
  { value: 'trust' as const, label: '信任名单', icon: ShieldCheck },
]

const approveHighRiskSelected = computed(() => approveSelection.value
  .filter((code) => capabilityRisk(code) === 'high').length)

function labelOf(options: DictionaryOption[], value: string) {
  return options.find((item) => item.value === value)?.label || value || '—'
}

function capabilityName(code: string) {
  return catalog.value[code]?.name || labelOf(capabilityNames.value, code)
}

function capabilityRisk(code: string) {
  return catalog.value[code]?.risk_level || 'low'
}

function riskLabel(code: string) {
  return labelOf(riskNames.value, capabilityRisk(code))
}

async function loadDictionaries() {
  const response = await publicApi.dictionaries([
    'plugin_review_status', 'plugin_review_action', 'plugin_capability', 'plugin_capability_risk',
  ])
  if (!response.success) return
  reviewStatus.value = response.data.plugin_review_status || []
  reviewActions.value = response.data.plugin_review_action || []
  capabilityNames.value = response.data.plugin_capability || []
  riskNames.value = response.data.plugin_capability_risk || []
}

async function loadCatalog() {
  const response = await pluginApi.capabilities()
  if (!response.success) {
    catalogReady.value = false
    toast.show('能力目录加载失败，为避免误批高风险能力，请手动逐项勾选', 'warning')
    return
  }
  const indexed: Record<string, PluginCapabilityItem> = {}
  response.data.forEach((item) => { indexed[item.code] = item })
  catalog.value = indexed
  catalogReady.value = true
}

let listRequestId = 0

/** 立刻跑一轮仓库自动同步：作者刚推 tag 时不用等定时任务。 */
async function syncRepos() {
  if (syncing.value) return
  syncing.value = true
  const response = await pluginAdminApi.syncRepos()
  syncing.value = false
  if (!response.success) return toast.show(response.message, 'error')
  const result = response.data
  const failed = result?.failed || []
  if (failed.length) {
    toast.show(
      `同步完成：登记 ${result.submitted} 个版本，${failed.length} 个仓库失败（${failed[0]}）`,
      'warning',
    )
  } else {
    toast.show(response.message, 'success')
  }
  versionQuery.page = 1
  await loadVersions()
}

async function loadVersions() {
  const current = ++listRequestId
  loading.value = true
  const response = await pluginAdminApi.versions({ ...versionQuery })
  loading.value = false
  if (current !== listRequestId) return
  if (!response.success) return toast.show(response.message, 'error')
  versionResult.value = response.data
  loaded.versions = true
}

async function loadLogs() {
  const current = ++listRequestId
  loading.value = true
  const response = await pluginAdminApi.reviewLogs({ ...logQuery })
  loading.value = false
  if (current !== listRequestId) return
  if (!response.success) return toast.show(response.message, 'error')
  logResult.value = response.data
  loaded.logs = true
}

async function loadTrust() {
  const current = ++listRequestId
  loading.value = true
  const response = await pluginAdminApi.trustedAuthors({
    ...trustQuery,
    active: trustQuery.active === '' ? undefined : trustQuery.active === 'true',
  })
  loading.value = false
  if (current !== listRequestId) return
  if (!response.success) return toast.show(response.message, 'error')
  trustResult.value = response.data
  loaded.trust = true
}

async function loadUsers() {
  const response = await userApi.list({ page: 1, page_size: 100 })
  if (!response.success) return
  users.value = response.data.items
}

function switchTab(tab: Tab) {
  activeTab.value = tab
  if (tab === 'versions' && !loaded.versions) loadVersions()
  if (tab === 'logs' && !loaded.logs) loadLogs()
  if (tab === 'trust' && !loaded.trust) { loadTrust(); loadUsers() }
}

function searchVersions() { versionQuery.page = 1; loadVersions() }
function resetVersions() { Object.assign(versionQuery, { plugin_id: '', review_status: '', keyword: '', page: 1 }); loadVersions() }
function searchLogs() { logQuery.page = 1; loadLogs() }
function resetLogs() { Object.assign(logQuery, { plugin_id: '', action: '', page: 1 }); loadLogs() }
function searchTrust() { trustQuery.page = 1; loadTrust() }
function resetTrust() { Object.assign(trustQuery, { keyword: '', active: '', page: 1 }); loadTrust() }

function changePage(target: 'versions' | 'logs' | 'trust', page: number) {
  const result = target === 'versions' ? versionResult : target === 'logs' ? logResult : trustResult
  if (page < 1 || page > result.value.pages) return
  if (target === 'versions') { versionQuery.page = page; loadVersions() }
  if (target === 'logs') { logQuery.page = page; loadLogs() }
  if (target === 'trust') { trustQuery.page = page; loadTrust() }
}

function askConfirm(title: string, message: string, text: string, handler: () => Promise<boolean>) {
  confirmTitle.value = title
  confirmMessage.value = message
  confirmText.value = text
  confirmHandler = handler
  confirmOpen.value = true
}

async function runConfirm() {
  if (!confirmHandler) return
  saving.value = true
  const succeeded = await confirmHandler()
  saving.value = false
  if (!succeeded) return
  confirmOpen.value = false
  confirmHandler = null
}

function openApprove(row: PluginReviewVersion) {
  approveTarget.value = row
  approveComment.value = ''
  // 默认只放行中低风险能力，高风险必须由管理员在本面板显式勾选。
  approveSelection.value = catalogReady.value
    ? row.capabilities.filter((code) => capabilityRisk(code) !== 'high')
    : []
  approveOpen.value = true
}

function toggleApprove(code: string) {
  approveSelection.value = approveSelection.value.includes(code)
    ? approveSelection.value.filter((item) => item !== code)
    : [...approveSelection.value, code]
}

async function submitApprove() {
  if (!approveTarget.value) return
  saving.value = true
  const response = await pluginAdminApi.approve({
    plugin_id: approveTarget.value.plugin_id,
    version: approveTarget.value.version,
    granted_capabilities: [...approveSelection.value],
    comment: approveComment.value,
  })
  saving.value = false
  if (!response.success) return toast.show(response.message, 'error')
  approveOpen.value = false
  toast.show('插件版本已通过审核', 'success')
  await loadVersions()
}

function openReject(row: PluginReviewVersion) {
  rejectTarget.value = row
  rejectComment.value = ''
  rejectOpen.value = true
}

async function submitReject() {
  if (!rejectTarget.value) return
  if (!rejectComment.value.trim()) return toast.show('请填写驳回原因', 'error')
  saving.value = true
  const response = await pluginAdminApi.reject({
    plugin_id: rejectTarget.value.plugin_id,
    version: rejectTarget.value.version,
    comment: rejectComment.value.trim(),
  })
  saving.value = false
  if (!response.success) return toast.show(response.message, 'error')
  rejectOpen.value = false
  toast.show('插件版本已驳回', 'success')
  await loadVersions()
}

function publishVersion(row: PluginReviewVersion) {
  askConfirm(
    row.is_published ? '切换生效版本' : '上架插件',
    `确定将「${row.plugin_name || '未命名插件'}」${row.version} 设为当前生效版本${row.is_published ? '' : '并上架'}吗？`,
    '确定上架',
    async () => {
      const response = await pluginAdminApi.publish({ plugin_id: row.plugin_id, version: row.version })
      if (!response.success) { toast.show(response.message, 'error'); return false }
      toast.show(row.is_published ? '已切换当前生效版本' : '插件已上架', 'success')
      await loadVersions()
      return true
    },
  )
}

function unpublishPlugin(row: PluginReviewVersion) {
  askConfirm(
    '下架插件',
    `确定下架「${row.plugin_name || '未命名插件'}」吗？已安装实例将无法继续开启。`,
    '确定下架',
    async () => {
      const response = await pluginAdminApi.unpublish(row.plugin_id)
      if (!response.success) { toast.show(response.message, 'error'); return false }
      toast.show('插件已下架', 'success')
      await loadVersions()
      return true
    },
  )
}

function openGrant() {
  grantUserId.value = ''
  grantReason.value = ''
  if (!users.value.length) loadUsers()
  grantOpen.value = true
}

async function submitGrant() {
  if (!grantUserId.value) return toast.show('请选择要授权的用户', 'error')
  saving.value = true
  const response = await pluginAdminApi.grantTrust({
    user_id: Number(grantUserId.value), reason: grantReason.value,
  })
  saving.value = false
  if (!response.success) return toast.show(response.message, 'error')
  grantOpen.value = false
  toast.show('已加入代码插件信任名单', 'success')
  await loadTrust()
}

function revokeTrust(row: PluginTrustedAuthor) {
  askConfirm(
    '撤销信任授权',
    `确定撤销用户「${row.username}」的代码插件上传权限吗？撤销后该用户不能再提交代码插件。`,
    '确定撤销',
    async () => {
      const response = await pluginAdminApi.revokeTrust(row.id)
      if (!response.success) { toast.show(response.message, 'error'); return false }
      toast.show('已撤销该用户的代码插件上传权限', 'success')
      await loadTrust()
      return true
    },
  )
}

onMounted(async () => {
  // 从插件市场「去上架」跳进来时带 plugin_id，直接筛出该插件
  const incoming = route.query.plugin_id
  if (typeof incoming === 'string' && incoming.trim()) {
    versionQuery.plugin_id = incoming.trim()
  }
  await Promise.all([loadDictionaries(), loadCatalog()])
  loadVersions()
  loadUsers()
})
</script>

<template>
  <div class="page-enter plugin-review-page">
    <div class="page-heading">
      <div><p class="eyebrow">PLUGIN REVIEW</p><h1>插件审核台</h1><p>审核只针对能力清单：批准插件能做什么，不逐行审查代码。高风险能力必须逐项勾选。</p></div>
      <div class="page-heading-actions">
        <button type="button" class="button secondary" :disabled="syncing" @click="syncRepos">
          <RefreshCw :size="16" :class="{ spinning: syncing }" />{{ syncing ? '同步中…' : '同步仓库最新版' }}
        </button>
        <button type="button" class="button ghost" @click="activeTab === 'versions' ? loadVersions() : activeTab === 'logs' ? loadLogs() : loadTrust()"><RefreshCw :size="16" />刷新当前列表</button>
      </div>
    </div>

    <div class="inner-tabs" role="tablist">
      <button v-for="tab in tabs" :key="tab.value" type="button" :class="{ active: activeTab === tab.value }" @click="switchTab(tab.value)"><component :is="tab.icon" :size="17" />{{ tab.label }}</button>
    </div>

    <template v-if="activeTab === 'versions'">
      <section class="filter-panel">
        <div class="filter-title"><Filter :size="17" /><strong>筛选条件</strong></div>
        <div class="filter-fields">
          <label>插件<input v-model="versionQuery.plugin_id" placeholder="插件名称或标识，支持模糊匹配" @keyup.enter="searchVersions" /></label>
          <label>审核状态<select v-model="versionQuery.review_status"><option value="">全部状态</option><option v-for="item in reviewStatus" :key="item.value" :value="item.value">{{ item.label }}</option></select></label>
          <label>关键词<input v-model="versionQuery.keyword" placeholder="插件名称或标识模糊匹配" @keyup.enter="searchVersions" /></label>
          <div class="filter-actions"><button type="button" class="button primary" @click="searchVersions"><Search :size="16" />查询</button><button type="button" class="button ghost" title="重置筛选" @click="resetVersions"><RefreshCw :size="16" />重置</button></div>
        </div>
      </section>

      <section class="content-panel">
        <div class="panel-heading"><div><h2><BadgeCheck :size="17" />插件版本</h2><p>共 {{ versionResult.total }} 个插件，同一插件只展示最新提交的版本</p></div></div>
        <div class="table-scroll plugin-review-table">
          <table>
            <thead><tr><th>插件</th><th>版本</th><th>作者</th><th>申请能力</th><th>已批准能力</th><th>审核状态</th><th>生效版本</th><th>提交时间</th><th class="action-column">操作</th></tr></thead>
            <tbody>
              <tr v-for="row in versionResult.items" :key="row.id">
                <td><strong>{{ row.plugin_name || '未命名插件' }}</strong><small v-if="row.plugin_summary" class="record-subtext">{{ row.plugin_summary }}</small></td>
                <td class="mono-text">{{ row.version }}<span v-if="row.source_ref" class="record-subtext">{{ row.source_ref }}</span></td>
                <td><strong>{{ row.author_name || '未填写作者' }}</strong><small class="record-subtext">提交人 {{ row.owner_username }}</small></td>
                <td><div v-if="row.capabilities.length" class="capability-chips"><span v-for="code in row.capabilities" :key="code" class="capability-chip" :class="{ 'high-risk': capabilityRisk(code) === 'high' }" :title="capabilityName(code)">{{ capabilityName(code) }}</span></div><span v-else class="capability-empty">未申请</span></td>
                <td><div v-if="row.granted_capabilities.length" class="capability-chips"><span v-for="code in row.granted_capabilities" :key="code" class="capability-chip granted" :title="capabilityName(code)">{{ capabilityName(code) }}</span></div><span v-else class="capability-empty">未批准</span></td>
                <td>{{ labelOf(reviewStatus, row.review_status) }}</td>
                <td><span v-if="!row.is_published" class="status-badge disabled"><i />未上架</span><span v-else-if="row.is_current" class="status-badge enabled"><i />当前生效</span><span v-else class="status-badge disabled"><i />在线 {{ row.current_version || '无' }}</span></td>
                <td class="muted-text">{{ row.created_at }}</td>
                <td class="action-buttons">
                  <template v-if="row.review_status === 'pending'">
                    <button type="button" class="button primary small" @click="openApprove(row)"><Check :size="15" />通过</button>
                    <button type="button" class="button ghost small" @click="openReject(row)"><X :size="15" />驳回</button>
                  </template>
                  <template v-else-if="row.review_status === 'approved'">
                    <button v-if="!row.is_published" type="button" class="button primary small" title="上架后已安装的机器人才能启用该插件" @click="publishVersion(row)">{{ row.is_current ? '上架' : '上架并生效' }}</button>
                    <button v-else-if="row.is_current" type="button" class="button ghost small" @click="unpublishPlugin(row)"><Ban :size="15" />下架</button>
                    <button v-else type="button" class="button primary small" @click="publishVersion(row)">设为当前</button>
                  </template>
                  <span v-else class="capability-empty">无需操作</span>
                </td>
              </tr>
            </tbody>
          </table>
          <EmptyState v-if="!loading && !versionResult.items.length" text="没有符合条件的插件版本" />
        </div>
        <div class="pagination"><span>第 {{ versionResult.page }} / {{ versionResult.pages }} 页，共 {{ versionResult.total }} 条</span><div><select v-model.number="versionQuery.page_size" title="每页条数" @change="versionQuery.page = 1; loadVersions()"><option :value="10">10 条/页</option><option :value="20">20 条/页</option><option :value="50">50 条/页</option><option :value="100">100 条/页</option></select><button type="button" class="page-button" :disabled="versionResult.page <= 1" @click="changePage('versions', versionResult.page - 1)">上一页</button><button type="button" class="page-button" :disabled="versionResult.page >= versionResult.pages" @click="changePage('versions', versionResult.page + 1)">下一页</button></div></div>
      </section>
    </template>

    <template v-else-if="activeTab === 'logs'">
      <section class="filter-panel">
        <div class="filter-title"><Filter :size="17" /><strong>筛选条件</strong></div>
        <div class="filter-fields">
          <label>插件<input v-model="logQuery.plugin_id" placeholder="插件名称或标识，支持模糊匹配" @keyup.enter="searchLogs" /></label>
          <label>审核动作<select v-model="logQuery.action"><option value="">全部动作</option><option v-for="item in reviewActions" :key="item.value" :value="item.value">{{ item.label }}</option></select></label>
          <div class="filter-actions"><button type="button" class="button primary" @click="searchLogs"><Search :size="16" />查询</button><button type="button" class="button ghost" title="重置筛选" @click="resetLogs"><RefreshCw :size="16" />重置</button></div>
        </div>
      </section>

      <section class="content-panel">
        <div class="panel-heading"><div><h2><FileClock :size="17" />审核流水</h2><p>共 {{ logResult.total }} 条记录，流水只追加不修改</p></div></div>
        <div class="table-scroll plugin-review-table">
          <table>
            <thead><tr><th>时间</th><th>插件</th><th>版本</th><th>动作</th><th>状态变更</th><th>操作人</th><th>备注</th></tr></thead>
            <tbody>
              <tr v-for="row in logResult.items" :key="row.id">
                <td class="muted-text nowrap">{{ row.created_at }}</td>
                <td>{{ row.plugin_name || '未命名插件' }}</td>
                <td class="mono-text">{{ row.version || '—' }}</td>
                <td>{{ labelOf(reviewActions, row.action) }}</td>
                <td class="mono-text">{{ labelOf(reviewStatus, row.from_status) }} → {{ labelOf(reviewStatus, row.to_status) }}</td>
                <td class="muted-text">{{ row.reviewer_username }}</td>
                <td class="record-content" :title="row.comment || undefined">{{ row.comment || '—' }}</td>
              </tr>
            </tbody>
          </table>
          <EmptyState v-if="!loading && !logResult.items.length" text="还没有审核流水" />
        </div>
        <div class="pagination"><span>第 {{ logResult.page }} / {{ logResult.pages }} 页，共 {{ logResult.total }} 条</span><div><select v-model.number="logQuery.page_size" title="每页条数" @change="logQuery.page = 1; loadLogs()"><option :value="10">10 条/页</option><option :value="20">20 条/页</option><option :value="50">50 条/页</option><option :value="100">100 条/页</option></select><button type="button" class="page-button" :disabled="logResult.page <= 1" @click="changePage('logs', logResult.page - 1)">上一页</button><button type="button" class="page-button" :disabled="logResult.page >= logResult.pages" @click="changePage('logs', logResult.page + 1)">下一页</button></div></div>
      </section>
    </template>

    <template v-else>
      <section class="filter-panel">
        <div class="filter-title"><Filter :size="17" /><strong>筛选条件</strong></div>
        <div class="filter-fields">
          <label>用户 ID<input v-model="trustQuery.keyword" placeholder="按用户 ID 精确匹配" @keyup.enter="searchTrust" /></label>
          <label>授权状态<select v-model="trustQuery.active"><option value="">全部状态</option><option value="true">生效中</option><option value="false">已撤销</option></select></label>
          <div class="filter-actions"><button type="button" class="button primary" @click="searchTrust"><Search :size="16" />查询</button><button type="button" class="button ghost" title="重置筛选" @click="resetTrust"><RefreshCw :size="16" />重置</button></div>
        </div>
      </section>

      <section class="content-panel">
        <div class="panel-heading"><div><h2><ShieldCheck :size="17" />代码插件信任名单</h2><p>共 {{ trustResult.total }} 条授权，仅名单内用户可提交真实代码插件</p></div><div class="panel-heading-actions"><button type="button" class="button primary" @click="openGrant"><UserPlus :size="16" />新增授权</button></div></div>
        <div class="table-scroll plugin-review-table">
          <table>
            <thead><tr><th>用户</th><th>授权人</th><th>授权原因</th><th>状态</th><th>授权时间</th><th class="action-column">操作</th></tr></thead>
            <tbody>
              <tr v-for="row in trustResult.items" :key="row.id">
                <td><strong>{{ row.username }}</strong><small class="mono-text">ID {{ row.user_id }}</small></td>
                <td class="muted-text">{{ row.granted_by_username }}</td>
                <td class="record-content" :title="row.reason || undefined">{{ row.reason || '未填写' }}</td>
                <td><span class="status-badge" :class="row.is_active ? 'enabled' : 'disabled'"><i />{{ row.is_active ? '生效中' : '已撤销' }}</span></td>
                <td class="muted-text">{{ row.created_at }}</td>
                <td class="action-buttons"><button v-if="row.is_active" type="button" class="button ghost small" @click="revokeTrust(row)"><Ban :size="15" />撤销</button><span v-else class="capability-empty">已撤销</span></td>
              </tr>
            </tbody>
          </table>
          <EmptyState v-if="!loading && !trustResult.items.length" text="信任名单为空，暂时没有人可以提交代码插件" />
        </div>
        <div class="pagination"><span>第 {{ trustResult.page }} / {{ trustResult.pages }} 页，共 {{ trustResult.total }} 条</span><div><select v-model.number="trustQuery.page_size" title="每页条数" @change="trustQuery.page = 1; loadTrust()"><option :value="10">10 条/页</option><option :value="20">20 条/页</option><option :value="50">50 条/页</option><option :value="100">100 条/页</option></select><button type="button" class="page-button" :disabled="trustResult.page <= 1" @click="changePage('trust', trustResult.page - 1)">上一页</button><button type="button" class="page-button" :disabled="trustResult.page >= trustResult.pages" @click="changePage('trust', trustResult.page + 1)">下一页</button></div></div>
      </section>
    </template>

    <LoadingOverlay :visible="loading" contained />

    <BaseModal :open="approveOpen" title="审核通过并批准能力" width="680px" @close="approveOpen = false">
      <form v-if="approveTarget" class="plugin-config-form" @submit.prevent="submitApprove">
        <div class="task-edit-heading"><strong>{{ approveTarget.plugin_name || '未命名插件' }} · {{ approveTarget.version }}</strong><small>作者 {{ approveTarget.owner_username }} · 申请 {{ approveTarget.capabilities.length }} 项能力</small></div>
        <p class="plugin-config-hint">默认已勾选全部中低风险能力；高风险能力必须手动勾选才会被批准，未勾选的能力插件无法调用。</p>
        <p v-if="!catalogReady" class="plugin-config-warning">能力目录未能加载，无法判断各能力的风险等级，已默认全部取消勾选。请手动挑选需要批准的能力后再确认。</p>
        <div v-if="approveTarget.capabilities.length" class="capability-list">
          <label v-for="code in approveTarget.capabilities" :key="code" class="capability-item selectable" :class="{ 'needs-attention': capabilityRisk(code) === 'high' }">
            <input type="checkbox" :checked="approveSelection.includes(code)" @change="toggleApprove(code)" />
            <div><strong>{{ capabilityName(code) }}<span class="risk-chip" :class="capabilityRisk(code)">{{ riskLabel(code) }}</span></strong><code>{{ code }}</code></div>
          </label>
        </div>
        <p v-else class="capability-empty">该版本没有申请任何能力。</p>
        <p v-if="approveHighRiskSelected" class="plugin-config-hint">已勾选 {{ approveHighRiskSelected }} 项高风险能力，这些能力可对群成员执行高影响操作，请确认确有需要。</p>
        <label><span class="field-label">审核意见</span><textarea v-model="approveComment" rows="3" maxlength="2000" placeholder="可填写批准范围说明，将写入审核流水" /></label>
        <div class="modal-form-actions"><button type="button" class="button secondary" :disabled="saving" @click="approveOpen = false">取消</button><button class="button primary" type="submit" :disabled="saving"><span v-if="saving" class="button-spinner" /><Check :size="16" />确认通过</button></div>
      </form>
    </BaseModal>

    <BaseModal :open="rejectOpen" title="驳回插件版本" @close="rejectOpen = false">
      <form v-if="rejectTarget" class="plugin-config-form" @submit.prevent="submitReject">
        <div class="task-edit-heading"><strong>{{ rejectTarget.plugin_name || '未命名插件' }} · {{ rejectTarget.version }}</strong><small>作者 {{ rejectTarget.owner_username }}</small></div>
        <label><span class="field-label">驳回原因<span class="required">*</span></span><textarea v-model="rejectComment" rows="4" maxlength="2000" required placeholder="请说明申请的能力存在什么问题，作者会看到该原因" /></label>
        <div class="modal-form-actions"><button type="button" class="button secondary" :disabled="saving" @click="rejectOpen = false">取消</button><button class="button danger" type="submit" :disabled="saving"><span v-if="saving" class="button-spinner" /><X :size="16" />确认驳回</button></div>
      </form>
    </BaseModal>

    <BaseModal :open="grantOpen" title="新增信任授权" @close="grantOpen = false">
      <form class="plugin-config-form" @submit.prevent="submitGrant">
        <p class="plugin-config-hint">加入信任名单后，该用户可以提交真实代码插件；代码插件运行时仍受能力清单与隔离执行约束。</p>
        <label><span class="field-label">选择用户<span class="required">*</span></span><select v-model="grantUserId" required><option value="" disabled>请选择用户</option><option v-for="user in users" :key="user.id" :value="user.id">{{ user.username }} · ID {{ user.id }}</option></select><small>仅列出前 100 个用户，如需授权更多用户请先按用户名搜索用户管理页。</small></label>
        <label><span class="field-label">授权原因</span><input v-model="grantReason" maxlength="255" placeholder="例如：官方插件作者，已完成实名与代码约定" /></label>
        <div class="modal-form-actions"><button type="button" class="button secondary" :disabled="saving" @click="grantOpen = false">取消</button><button class="button primary" type="submit" :disabled="saving"><span v-if="saving" class="button-spinner" /><UserPlus :size="16" />确认授权</button></div>
      </form>
    </BaseModal>

    <ConfirmDialog :open="confirmOpen" :title="confirmTitle" :message="confirmMessage" :confirm-text="confirmText" :loading="saving" @close="confirmOpen = false" @confirm="runConfirm" />
  </div>
</template>
