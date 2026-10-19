<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { CloudUpload, FileArchive, Filter, Package, RefreshCw, Search, Trash2, Upload, X } from 'lucide-vue-next'
import { botApi, pluginApi, publicApi } from '../api'
import type {
  BotAccount,
  DictionaryOption,
  Pagination,
  PluginCapabilityItem,
  PluginInstallation,
  PluginItem,
  PluginRepoProbeResult,
  PluginVersionInfo,
} from '../types'
import BaseModal from '../components/common/BaseModal.vue'
import ConfirmDialog from '../components/common/ConfirmDialog.vue'
import EmptyState from '../components/common/EmptyState.vue'
import LoadingOverlay from '../components/common/LoadingOverlay.vue'
import { useAuth } from '../composables/useAuth'
import { useToast } from '../composables/useToast'

const toast = useToast()
const router = useRouter()
const auth = useAuth()
const loading = ref(false)
const saving = ref(false)
const query = reactive({ keyword: '', source_type: '', published: '', page: 1, page_size: 20 })
const result = ref<Pagination<PluginItem>>({ items: [], page: 1, page_size: 20, total: 0, pages: 1 })
const sourceTypes = ref<DictionaryOption[]>([])
const reviewStatus = ref<DictionaryOption[]>([])
const capabilityNames = ref<DictionaryOption[]>([])
const riskNames = ref<DictionaryOption[]>([])
const capabilityCatalog = ref<PluginCapabilityItem[]>([])
const bots = ref<BotAccount[]>([])
const installations = ref<PluginInstallation[]>([])
const detail = ref<PluginItem | null>(null)
const detailVersions = ref<PluginVersionInfo[]>([])
const detailOpen = ref(false)
const installing = ref<PluginItem | null>(null)
const installOpen = ref(false)
const installBots = ref<string[]>([])
const submitOpen = ref(false)
const deleting = ref<PluginItem | null>(null)
const deleteOpen = ref(false)
const deleteSubmitting = ref(false)
const submitMode = ref<'zip' | 'repo'>('zip')
const zipFile = ref<File | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)
const dragActive = ref(false)
// 与后端 PLUGIN_PACKAGE_MAX_SIZE 的默认值一致，只用于选择时提前拦下明显超限的文件，
// 真正的判定仍在服务端（ensure_archive_size），改环境变量后此处提示可能偏小，但不会误放行。
const ZIP_MAX_BYTES = 10 * 1024 * 1024
const zipLimitText = `${ZIP_MAX_BYTES / 1024 / 1024} MB`
const repoForm = reactive({ repo_url: '', source_ref: '' })
// 探测结果：把仓库里即将提交的版本号在上传前告诉作者。
const probing = ref(false)
const repoProbe = ref<PluginRepoProbeResult | null>(null)

const catalogMap = computed(() => {
  const indexed: Record<string, PluginCapabilityItem> = {}
  capabilityCatalog.value.forEach((item) => { indexed[item.code] = item })
  return indexed
})

// 详情只展示当前生效版本申请的能力，缺少目录信息时按能力码兜底，避免整页空白。
const detailCapabilities = computed<PluginCapabilityItem[]>(() => {
  const codes = detail.value?.current_version?.capabilities ?? []
  return codes.map((code) => catalogMap.value[code] || {
    code, name: labelOf(capabilityNames.value, code), risk_level: 'low', description: '', actions: [],
  })
})

function labelOf(options: DictionaryOption[], value: string) {
  return options.find((item) => item.value === value)?.label || value || '—'
}

function riskLabel(level: string) {
  return labelOf(riskNames.value, level)
}

let listRequestId = 0

async function loadPlugins() {
  const current = ++listRequestId
  loading.value = true
  const response = await pluginApi.list({
    ...query,
    published: query.published === '' ? undefined : query.published === 'true',
  })
  loading.value = false
  if (current !== listRequestId) return
  if (!response.success) {
    toast.show(response.message, 'error')
    return
  }
  result.value = response.data
}

async function loadDictionaries() {
  const response = await publicApi.dictionaries([
    'plugin_source_type', 'plugin_review_status', 'plugin_capability', 'plugin_capability_risk',
  ])
  if (!response.success) return
  sourceTypes.value = response.data.plugin_source_type || []
  reviewStatus.value = response.data.plugin_review_status || []
  capabilityNames.value = response.data.plugin_capability || []
  riskNames.value = response.data.plugin_capability_risk || []
}

async function loadCapabilities() {
  const response = await pluginApi.capabilities()
  if (!response.success) return
  capabilityCatalog.value = response.data
}

async function loadBots() {
  const response = await botApi.list()
  if (!response.success) return
  bots.value = response.data.filter((item) => item.is_enabled)
}

// 一个账号对同一个插件只有一条安装记录，这里直接按插件标识索引出来。
const installMap = computed<Record<string, PluginInstallation>>(() => {
  const indexed: Record<string, PluginInstallation> = {}
  installations.value.forEach((item) => { indexed[item.plugin_id] = item })
  return indexed
})

// mine=true 让后端只返回当前账号的安装，管理员也不会把别人的安装算成自己的。
async function loadInstallations() {
  const response = await pluginApi.installations({ page: 1, page_size: 100, mine: true })
  if (!response.success) return
  installations.value = response.data.items
}

function installTitle(plugin: PluginItem) {
  const installed = installMap.value[plugin.plugin_id]
  if (!plugin.is_published || !plugin.current_version) return installHint(plugin)
  if (!installed) return '安装到我的机器人'
  return `已安装到 ${installed.bot_qqs.length} 台机器人，可到已装插件页调整生效范围`
}

function search() {
  query.page = 1
  loadPlugins()
  loadInstallations()
}

function resetFilters() {
  Object.assign(query, { keyword: '', source_type: '', published: '', page: 1 })
  loadPlugins()
}

function changePage(page: number) {
  if (page < 1 || page > result.value.pages) return
  query.page = page
  loadPlugins()
}

async function openDetail(pluginId: string) {
  const response = await pluginApi.detail(pluginId)
  if (!response.success) {
    toast.show(response.message, 'error')
    return
  }
  detail.value = response.data.plugin
  detailVersions.value = response.data.versions
  detailOpen.value = true
}

function openInstall(plugin: PluginItem) {
  installing.value = plugin
  installBots.value = bots.value.map((bot) => bot.bot_qq)
  installOpen.value = true
}

function toggleInstallBot(botQq: string) {
  installBots.value = installBots.value.includes(botQq)
    ? installBots.value.filter((item) => item !== botQq)
    : [...installBots.value, botQq]
}

async function confirmInstall() {
  if (!installing.value) return
  saving.value = true
  const response = await pluginApi.install({
    plugin_id: installing.value.plugin_id,
    bot_qqs: installBots.value,
  })
  saving.value = false
  if (!response.success) {
    toast.show(response.message, 'error')
    return
  }
  installOpen.value = false
  toast.show('插件已安装，请在已装插件中填写配置并开启', 'success')
  await loadInstallations()
}

function openSubmit() {
  clearFile()
  dragActive.value = false
  Object.assign(repoForm, { repo_url: '', source_ref: '' })
  repoProbe.value = null
  submitMode.value = 'zip'
  submitOpen.value = true
}

// 落区主文案随状态变化：拖拽经过时给明确反馈，已选文件时显示文件名而不是长提示。
const dropzoneTitle = computed(() => {
  if (dragActive.value) return zipFile.value ? '松开即可替换压缩包' : '松开鼠标即可上传'
  return zipFile.value ? zipFile.value.name : '点击选择或拖拽压缩包到这里'
})

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

// 点选与拖拽共用一套校验：扩展名不对或体积超限都当场拦下，避免传完才被后端退回。
function acceptFile(file: File | null) {
  if (!file) {
    zipFile.value = null
    return
  }
  if (!/\.zip$/i.test(file.name)) {
    toast.show('只支持 .zip 插件包，请先压缩后再上传', 'error')
    clearFile()
    return
  }
  if (file.size > ZIP_MAX_BYTES) {
    toast.show(`插件包不能超过 ${zipLimitText}，当前 ${formatBytes(file.size)}`, 'error')
    clearFile()
    return
  }
  zipFile.value = file
}

function pickFile(event: Event) {
  acceptFile((event.target as HTMLInputElement).files?.[0] ?? null)
}

function openPicker() {
  if (saving.value) return
  fileInput.value?.click()
}

function onDrop(event: DragEvent) {
  dragActive.value = false
  if (saving.value) return
  acceptFile(event.dataTransfer?.files?.[0] ?? null)
}

// 拖拽在子元素之间移动时也会触发 dragleave，用 relatedTarget 判断是否真的离开了整块落区。
function onDragLeave(event: DragEvent) {
  const zone = event.currentTarget as HTMLElement
  if (event.relatedTarget && zone.contains(event.relatedTarget as Node)) return
  dragActive.value = false
}

// 清空原生控件，否则再次选中同一个文件不会触发 change。
function clearFile() {
  zipFile.value = null
  if (fileInput.value) fileInput.value.value = ''
}

// 仓库地址的轻量预校验
const REPO_URL_PATTERN = /^https?:\/\/(www\.)?github\.com\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+/

function checkRepoUrl(): string {
  const repoUrl = repoForm.repo_url.trim()
  if (!repoUrl) {
    toast.show('请先填写仓库地址', 'error')
    return ''
  }
  if (!REPO_URL_PATTERN.test(repoUrl)) {
    toast.show('请填写 https://github.com/所有者/仓库名 形式的仓库地址', 'error')
    return ''
  }
  return repoUrl
}

async function probeRepo() {
  const repoUrl = checkRepoUrl()
  if (!repoUrl) return
  probing.value = true
  const response = await pluginApi.probeRepoLatest({
    repo_url: repoUrl,
    source_ref: repoForm.source_ref.trim(),
  })
  probing.value = false
  if (!response.success) {
    repoProbe.value = null
    toast.show(response.message, 'error')
    return
  }
  repoProbe.value = response.data
  toast.show(`探测到版本 ${response.data.version}（${response.data.tag || response.data.ref}）`, 'success')
}

// 仓库地址改动后，之前的探测结果就过期了。
function resetProbe() {
  repoProbe.value = null
}

async function submitPlugin() {
  let repoUrl = ''
  if (submitMode.value !== 'zip') {
    repoUrl = checkRepoUrl()
    if (!repoUrl) return
  }
  saving.value = true
  const response = submitMode.value === 'zip'
    ? await pluginApi.submitPackage(zipFile.value as File)
    : await pluginApi.submitFromRepo({
        repo_url: repoUrl,
        // 留空交由后端自动探测最新 release / tag / commit。
        source_ref: repoForm.source_ref.trim(),
      })
  saving.value = false
  if (!response.success) {
    toast.show(response.message, 'error')
    return
  }
  submitOpen.value = false
  // 管理员提交由后端直接免审上架，提示语要跟着变，避免让人以为还在排队。
  const approved = response.data?.version?.review_status === 'approved'
  toast.show(
    approved ? '插件已发布并上架' : '插件版本已提交，等待管理员审核',
    'success',
  )
  query.page = 1
  await loadPlugins()
}

function versionLabel(version: PluginVersionInfo | null) {
  return version ? version.version : '暂无生效版本'
}

// 拖拽落到弹窗空白处时，浏览器默认会直接打开/下载这个 zip，这里在提交弹窗打开期间兜住。
function preventWindowDrop(event: DragEvent) {
  if (!submitOpen.value) return
  event.preventDefault()
}

const isAdmin = () => auth.state.user?.role_code === 'admin'

// 管理员可以删除任意插件，插件提交者只能删自己上传的那个。
function canDelete(plugin: PluginItem) {
  const user = auth.state.user
  if (!user) return false
  return user.role_code === 'admin' || plugin.owner_id === user.id
}

function askDelete(plugin: PluginItem) {
  deleting.value = plugin
  deleteOpen.value = true
}

async function confirmDelete() {
  if (!deleting.value) return
  deleteSubmitting.value = true
  const response = await pluginApi.deletePlugin(deleting.value.plugin_id)
  deleteSubmitting.value = false
  if (!response.success) {
    toast.show(response.message, 'error')
    return
  }
  const data = response.data
  deleteOpen.value = false
  deleting.value = null
  toast.show(
    `已删除「${data.name}」，清理 ${data.deleted_versions} 个版本、${data.affected_installations} 个安装`,
    'success',
  )
  await loadPlugins()
  await loadInstallations()
}

// 未上架的插件不能安装，这里把原因写清楚，避免只看到一个灰色按钮。
function installHint(plugin: PluginItem) {
  if (!bots.value.length) return '当前还没有可用机器人，请先在机器人管理中添加'
  if (!plugin.current_version) return '该插件还没有生效版本，需管理员先通过审核并上架'
  if (!plugin.is_published) return '该插件尚未上架，需管理员在插件审核台上架后才能安装'
  return '安装到我的机器人'
}

// 管理员从市场页直接跳到审核台并带上插件标识，省去手动输入。
function goReview(pluginId: string) {
  router.push({ name: 'plugin-review', query: { plugin_id: pluginId } })
}

// 已安装的插件不再重复安装，统一到已装插件页改配置与生效范围。
function goInstalled(pluginId: string) {
  router.push({ name: 'plugin-installed', query: { plugin_id: pluginId } })
}

onMounted(() => {
  window.addEventListener('dragover', preventWindowDrop)
  window.addEventListener('drop', preventWindowDrop)
  loadPlugins()
  loadDictionaries()
  loadCapabilities()
  loadBots()
  loadInstallations()
})

onBeforeUnmount(() => {
  window.removeEventListener('dragover', preventWindowDrop)
  window.removeEventListener('drop', preventWindowDrop)
})
</script>

<template>
  <div class="page-enter plugin-market-page">
    <div class="page-heading">
      <div><p class="eyebrow">PLUGIN MARKET</p><h1>插件市场</h1><p>浏览已上架插件，查看能力清单并安装到自己的机器人。</p></div>
      <button type="button" class="button primary" @click="openSubmit"><Upload :size="18" />提交插件</button>
    </div>

    <section class="filter-panel">
      <div class="filter-title"><Filter :size="17" /><strong>筛选条件</strong></div>
      <div class="filter-fields">
        <label>插件<input v-model="query.keyword" placeholder="搜索插件名称" @keyup.enter="search" /></label>
        <label>来源<select v-model="query.source_type"><option value="">全部来源</option><option v-for="item in sourceTypes" :key="item.value" :value="item.value">{{ item.label }}</option></select></label>
        <label>上架状态<select v-model="query.published"><option value="">全部状态</option><option value="true">已上架</option><option value="false">未上架</option></select></label>
        <div class="filter-actions"><button type="button" class="button primary" @click="search"><Search :size="16" />查询</button><button type="button" class="button ghost" title="重置筛选" @click="resetFilters"><RefreshCw :size="16" />重置</button></div>
      </div>
    </section>

    <section class="content-panel">
      <div class="panel-heading"><div><h2><Package :size="17" />插件列表</h2><p>共 {{ result.total }} 个插件，只有已上架插件才能安装</p></div><button type="button" class="icon-button" title="刷新列表" @click="loadPlugins(); loadInstallations()"><RefreshCw :size="18" /></button></div>
      <div class="table-scroll plugin-market-table">
        <table>
          <thead><tr><th>插件</th><th>来源</th><th>作者</th><th>当前版本</th><th>上架状态</th><th>安装状态</th><th>已批准能力</th><th>更新时间</th><th class="action-column">操作</th></tr></thead>
          <tbody>
            <tr v-for="plugin in result.items" :key="plugin.plugin_id">
              <td><strong>{{ plugin.name }}</strong><small v-if="plugin.summary" class="record-subtext">{{ plugin.summary }}</small></td>
              <td>{{ labelOf(sourceTypes, plugin.source_type) }}</td>
              <td>{{ plugin.author_name || '未知作者' }}</td>
              <td class="mono-text">{{ versionLabel(plugin.current_version) }}</td>
              <td><span class="status-badge" :class="plugin.is_published ? 'enabled' : 'disabled'"><i />{{ plugin.is_published ? '已上架' : '未上架' }}</span><small v-if="!plugin.is_published" class="plugin-status-hint">需在审核台上架</small></td>
              <td><span v-if="installMap[plugin.plugin_id]" class="status-badge installed"><i />已安装</span><span v-else class="status-badge disabled"><i />未安装</span><small v-if="installMap[plugin.plugin_id]" class="plugin-status-hint">{{ installMap[plugin.plugin_id].bot_qqs.length }} 台机器人 · {{ installMap[plugin.plugin_id].is_enabled ? '已开启' : '已停用' }}</small></td>
              <td><div v-if="plugin.current_version?.granted_capabilities?.length" class="capability-chips"><span v-for="code in plugin.current_version.granted_capabilities" :key="code" class="capability-chip granted" :title="labelOf(capabilityNames, code)">{{ labelOf(capabilityNames, code) }}</span></div><span v-else class="capability-empty">未批准能力</span></td>
              <td class="muted-text">{{ plugin.updated_at }}</td>
              <td class="action-buttons"><button type="button" class="icon-button" title="查看插件详情" @click="openDetail(plugin.plugin_id)"><Package :size="16" /></button><button v-if="isAdmin() && plugin.current_version && !plugin.is_published" type="button" class="button ghost small" title="到审核台把这个版本设为当前生效版本并上架" @click="goReview(plugin.plugin_id)">去上架</button><button v-if="installMap[plugin.plugin_id]" type="button" class="button ghost small" title="调整配置与生效机器人" @click="goInstalled(plugin.plugin_id)">管理安装</button><button v-else type="button" class="button ghost small" :disabled="!plugin.is_published || !plugin.current_version" :title="installTitle(plugin)" @click="openInstall(plugin)">安装</button><button v-if="canDelete(plugin)" type="button" class="icon-button danger" title="删除插件" @click="askDelete(plugin)"><Trash2 :size="16" /></button></td>
            </tr>
          </tbody>
        </table>
        <EmptyState v-if="!loading && !result.items.length" text="还没有符合条件的插件" />
      </div>
      <div class="pagination"><span>第 {{ result.page }} / {{ result.pages }} 页，共 {{ result.total }} 条</span><div><select v-model.number="query.page_size" title="每页条数" @change="query.page = 1; loadPlugins()"><option :value="10">10 条/页</option><option :value="20">20 条/页</option><option :value="50">50 条/页</option><option :value="100">100 条/页</option></select><button type="button" class="page-button" :disabled="result.page <= 1" @click="changePage(result.page - 1)">上一页</button><button type="button" class="page-button" :disabled="result.page >= result.pages" @click="changePage(result.page + 1)">下一页</button></div></div>
    </section>
    <LoadingOverlay :visible="loading" contained />

    <BaseModal :open="detailOpen" title="插件详情" @close="detailOpen = false">
      <div v-if="detail">
        <div class="plugin-detail-head">
          <h3>{{ detail.name }}</h3>
          <p>{{ detail.description || detail.summary || '作者未填写说明' }}</p>
        </div>
        <dl class="plugin-detail-list">
          <div><dt>作者</dt><dd>{{ detail.author_name || '未知' }}</dd></div>
          <div><dt>协议</dt><dd>{{ detail.license || '未声明' }}</dd></div>
          <div><dt>主页</dt><dd>{{ detail.homepage || '未提供' }}</dd></div>
          <div v-if="detail.repo_url"><dt>仓库</dt><dd>{{ detail.repo_url }}</dd></div>
          <div><dt>来源</dt><dd>{{ labelOf(sourceTypes, detail.source_type) }}</dd></div>
          <div><dt>上架状态</dt><dd>{{ detail.is_published ? '已上架' : '未上架' }}</dd></div>
        </dl>
        <h4 class="plugin-detail-section">能力清单</h4>
        <div class="capability-list">
          <div v-for="capability in detailCapabilities" :key="capability.code" class="capability-item">
            <span class="risk-chip" :class="capability.risk_level">{{ riskLabel(capability.risk_level) }}</span>
            <div><strong>{{ capability.name }}</strong><code>{{ capability.code }}</code><small>{{ capability.description }}</small></div>
          </div>
          <p v-if="!detailCapabilities.length" class="capability-empty">该插件当前生效版本未申请任何能力</p>
        </div>
        <h4 class="plugin-detail-section">版本记录</h4>
        <div class="table-scroll plugin-detail-panel">
          <table>
            <thead><tr><th>版本</th><th>审核状态</th><th>申请能力</th><th>已批准能力</th><th>提交时间</th></tr></thead>
            <tbody>
              <tr v-for="version in detailVersions" :key="version.id">
                <td class="mono-text">{{ version.version }}</td>
                <td>{{ labelOf(reviewStatus, version.review_status) }}</td>
                <td><div v-if="version.capabilities.length" class="capability-chips"><span v-for="code in version.capabilities" :key="code" class="capability-chip" :title="labelOf(capabilityNames, code)">{{ labelOf(capabilityNames, code) }}</span></div><span v-else class="capability-empty">未申请</span></td>
                <td><div v-if="version.granted_capabilities.length" class="capability-chips"><span v-for="code in version.granted_capabilities" :key="code" class="capability-chip granted" :title="labelOf(capabilityNames, code)">{{ labelOf(capabilityNames, code) }}</span></div><span v-else class="capability-empty">未批准</span></td>
                <td class="muted-text">{{ version.created_at }}</td>
              </tr>
            </tbody>
          </table>
          <EmptyState v-if="!detailVersions.length" text="该插件还没有提交任何版本" />
        </div>
      </div>
    </BaseModal>

    <BaseModal :open="installOpen" title="安装插件" @close="installOpen = false">
      <form class="rule-form" @submit.prevent="confirmInstall">
        <p>即将安装 <strong>{{ installing?.name }}</strong>。一个账号安装一次即可，选中的机器人共用同一份配置，安装后默认停用。</p>
        <div class="field-label">生效机器人<span class="required">*</span></div>
        <div v-if="bots.length" class="bot-pick-list">
          <label v-for="bot in bots" :key="bot.id" class="bot-pick-item">
            <input type="checkbox" :checked="installBots.includes(bot.bot_qq)" @change="toggleInstallBot(bot.bot_qq)" />
            <div><strong>{{ bot.nickname || bot.bot_qq }}</strong><small class="mono-text">{{ bot.bot_qq }}</small></div>
          </label>
        </div>
        <small v-else>还没有可用机器人，请先在机器人管理中添加。</small>
        <div class="modal-form-actions"><button type="button" class="button secondary" @click="installOpen = false">取消</button><button class="button primary" type="submit" :disabled="saving || !installBots.length"><span v-if="saving" class="button-spinner" />确认安装</button></div>
      </form>
    </BaseModal>

    <BaseModal :open="submitOpen" title="提交插件" @close="submitOpen = false">
      <form class="rule-form" @submit.prevent="submitPlugin">
        <div class="inner-tabs" role="tablist">
          <button type="button" :class="{ active: submitMode === 'zip' }" @click="submitMode = 'zip'">上传压缩包</button>
          <button type="button" :class="{ active: submitMode === 'repo' }" @click="submitMode = 'repo'">代码仓库</button>
        </div>
        <template v-if="submitMode === 'zip'">
          <div class="upload-field">
            <span class="field-label">插件压缩包<span class="required">*</span></span>
            <div
              class="upload-dropzone"
              :class="{ 'is-dragging': dragActive, 'is-filled': !!zipFile, 'is-disabled': saving }"
              role="button"
              tabindex="0"
              :aria-label="zipFile ? `已选择 ${zipFile.name}，点击可重新选择` : '点击选择或拖拽插件压缩包'"
              @click="openPicker"
              @keydown.enter.prevent="openPicker"
              @keydown.space.prevent="openPicker"
              @dragenter.prevent="dragActive = true"
              @dragover.prevent="dragActive = true"
              @dragleave.prevent="onDragLeave"
              @drop.prevent="onDrop"
            >
              <input ref="fileInput" class="upload-input" type="file" accept=".zip,application/zip" :disabled="saving" @change="pickFile" />
              <span class="upload-icon"><component :is="zipFile ? FileArchive : CloudUpload" :size="22" /></span>
              <span class="upload-copy">
                <strong class="upload-name">{{ dropzoneTitle }}</strong>
                <small class="upload-meta">{{ zipFile ? `${formatBytes(zipFile.size)} · 点击可重新选择` : `仅支持 .zip，单包不超过 ${zipLimitText}` }}</small>
              </span>
              <span v-if="zipFile" class="upload-actions">
                <button type="button" class="upload-clear" title="移除已选文件" @click.stop="clearFile"><X :size="15" /></button>
              </span>
            </div>
            <small class="upload-footnote">压缩包根目录必须包含 manifest.json 与入口文件。</small>
          </div>
        </template>
        <template v-else>
          <label><span class="field-label">仓库地址<span class="required">*</span></span><input v-model="repoForm.repo_url" placeholder="https://github.com/作者/仓库" required @input="resetProbe" /><small>当前仅支持 github.com 仓库。</small></label>
          <label><span class="field-label">版本引用</span><input v-model="repoForm.source_ref" placeholder="留空自动探测最新版本" @input="resetProbe" /><small>留空即自动取仓库最新的 release / tag / commit；也可以指定 v1.0.0 或提交哈希。</small></label>
          <div class="repo-probe-row">
            <button type="button" class="button secondary" :disabled="probing || !repoForm.repo_url" @click="probeRepo">
              <span v-if="probing" class="button-spinner" /><RefreshCw :size="15" />探测最新版本
            </button>
            <span v-if="repoProbe" class="repo-probe-result">
              将提交 <strong>{{ repoProbe.plugin_id }}</strong> 的 <strong>{{ repoProbe.version }}</strong>
              <small>（{{ repoProbe.tag || repoProbe.ref }}）</small>
            </span>
          </div>
        </template>
        <div class="modal-form-actions"><button type="button" class="button secondary" :disabled="saving" @click="submitOpen = false">取消</button><button class="button primary" type="submit" :disabled="saving || (submitMode === 'zip' && !zipFile)"><span v-if="saving" class="button-spinner" /><CloudUpload :size="16" />{{ isAdmin() ? '发布插件' : '提交审核' }}</button></div>
      </form>
    </BaseModal>

    <ConfirmDialog :open="deleteOpen" title="删除插件" :message="`确定删除插件「${deleting?.name || '未命名插件'}」吗？该插件的全部版本、磁盘包文件以及所有用户的安装记录都会被删除，且无法恢复。`" confirm-text="确定删除" :loading="deleteSubmitting" @close="deleteOpen = false" @confirm="confirmDelete" />
  </div>
</template>
