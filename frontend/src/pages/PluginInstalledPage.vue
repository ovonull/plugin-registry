<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { Boxes, CircleArrowUp, Filter, RefreshCw, Save, Search, Settings2, Sparkles, Trash2 } from 'lucide-vue-next'
import { botApi, pluginApi, publicApi } from '../api'
import type {
  BotAccount,
  DictionaryOption,
  Pagination,
  PluginCapabilityItem,
  PluginInstallation,
} from '../types'
import BaseModal from '../components/common/BaseModal.vue'
import ConfirmDialog from '../components/common/ConfirmDialog.vue'
import EmptyState from '../components/common/EmptyState.vue'
import LoadingOverlay from '../components/common/LoadingOverlay.vue'
import PasswordInput from '../components/common/PasswordInput.vue'
import { useToast } from '../composables/useToast'

const toast = useToast()
const route = useRoute()
const loading = ref(false)
const saving = ref(false)
const query = reactive({ bot_qq: '', plugin_id: '', enabled: '', page: 1, page_size: 20 })
const result = ref<Pagination<PluginInstallation>>({ items: [], page: 1, page_size: 20, total: 0, pages: 1 })
const bots = ref<BotAccount[]>([])
const capabilityNames = ref<DictionaryOption[]>([])
const riskNames = ref<DictionaryOption[]>([])
const catalog = ref<Record<string, PluginCapabilityItem>>({})
const editing = ref<PluginInstallation | null>(null)
const configOpen = ref(false)
const configForm = reactive<Record<string, string | number | boolean>>({})
const grantedSelection = ref<string[]>([])
// 本安装生效的机器人，取消勾选后该机器人立即不再执行这个插件。
const botSelection = ref<string[]>([])
const deleting = ref<PluginInstallation | null>(null)
const confirmOpen = ref(false)
// 手动升级：插件发版后不会自动切版本，这里由用户确认后再升。
const upgrading = ref<PluginInstallation | null>(null)
const upgradeOpen = ref(false)
const upgradeSubmitting = ref(false)
// 检查仓库更新：按安装记录逐个探测，只对代码仓库来源的插件有意义。
const checkingId = ref<number | null>(null)

const catalogMap = computed(() => catalog.value)

function labelOf(options: DictionaryOption[], value: string) {
  return options.find((item) => item.value === value)?.label || value || '—'
}

function riskLabel(level: string) {
  return labelOf(riskNames.value, level)
}

function capabilityName(code: string) {
  return catalogMap.value[code]?.name || labelOf(capabilityNames.value, code)
}

function capabilityRisk(code: string) {
  return catalogMap.value[code]?.risk_level || 'low'
}

// 列表请求序号戳：快速连点查询会并发多个请求，响应乱序时旧页数据不能覆盖新页。
let listRequestId = 0

async function loadInstallations() {
  const current = ++listRequestId
  loading.value = true
  const response = await pluginApi.installations({
    ...query,
    enabled: query.enabled === '' ? undefined : query.enabled === 'true',
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
  const response = await publicApi.dictionaries(['plugin_capability', 'plugin_capability_risk'])
  if (!response.success) return
  capabilityNames.value = response.data.plugin_capability || []
  riskNames.value = response.data.plugin_capability_risk || []
}

async function loadCatalog() {
  const response = await pluginApi.capabilities()
  if (!response.success) return
  const indexed: Record<string, PluginCapabilityItem> = {}
  response.data.forEach((item) => { indexed[item.code] = item })
  catalog.value = indexed
}

async function loadBots() {
  const response = await botApi.list()
  if (!response.success) return
  bots.value = response.data.filter((item) => item.is_enabled)
}

function search() {
  query.page = 1
  loadInstallations()
}

function resetFilters() {
  Object.assign(query, { bot_qq: '', plugin_id: '', enabled: '', page: 1 })
  loadInstallations()
}

function changePage(page: number) {
  if (page < 1 || page > result.value.pages) return
  query.page = page
  loadInstallations()
}

function openConfig(installation: PluginInstallation) {
  editing.value = installation
  Object.keys(configForm).forEach((key) => { delete configForm[key] })
  installation.config_schema.forEach((field) => {
    const saved = installation.config[field.key]
    if (field.type === 'switch') configForm[field.key] = Boolean(saved)
    else if (field.type === 'number') configForm[field.key] = Number(saved ?? 0)
    else configForm[field.key] = String(saved ?? '')
  })
  grantedSelection.value = [...installation.granted_capabilities]
  botSelection.value = [...installation.bot_qqs]
  configOpen.value = true
}

function toggleBot(botQq: string) {
  botSelection.value = botSelection.value.includes(botQq)
    ? botSelection.value.filter((item) => item !== botQq)
    : [...botSelection.value, botQq]
}

function botLabel(botQq: string) {
  const bot = bots.value.find((item) => item.bot_qq === botQq)
  return bot?.nickname || botQq
}

function toggleGranted(code: string) {
  grantedSelection.value = grantedSelection.value.includes(code)
    ? grantedSelection.value.filter((item) => item !== code)
    : [...grantedSelection.value, code]
}

async function saveConfig() {
  if (!editing.value) return
  saving.value = true
  const response = await pluginApi.updateInstallation(editing.value.id, {
    config: { ...configForm },
    granted_capabilities: [...grantedSelection.value],
    bot_qqs: [...botSelection.value],
  })
  saving.value = false
  if (!response.success) {
    toast.show(response.message, 'error')
    return
  }
  configOpen.value = false
  toast.show('插件配置已保存', 'success')
  await loadInstallations()
}

async function toggleEnabled(installation: PluginInstallation) {
  saving.value = true
  const response = await pluginApi.updateInstallation(installation.id, {
    is_enabled: !installation.is_enabled,
  })
  saving.value = false
  if (!response.success) {
    toast.show(response.message, 'error')
    return
  }
  toast.show(response.data.is_enabled ? '插件已开启' : '插件已停用', 'success')
  await loadInstallations()
}

// 新版本已批准、但本安装还没生效的能力，升级后需要用户自行勾选。
function pendingCapabilities(installation: PluginInstallation) {
  return installation.latest_approved_capabilities.filter(
    (code) => !installation.granted_capabilities.includes(code),
  )
}

function askUpgrade(installation: PluginInstallation) {
  upgrading.value = installation
  upgradeOpen.value = true
}

// 检查仓库更新：只探测不提交，发现新版本时引导去市场页重新提交该 tag。
async function checkUpdate(installation: PluginInstallation) {
  checkingId.value = installation.id
  const response = await pluginApi.checkPluginUpdate(installation.plugin_id)
  checkingId.value = null
  if (!response.success) {
    toast.show(response.message, 'error')
    return
  }
  const data = response.data
  if (data.has_new_version) {
    toast.show(`仓库有新版本 ${data.version}（${data.tag || data.ref}），去插件市场重新提交该版本即可升级`, 'success')
  } else {
    toast.show('仓库里暂未有新版本', 'info')
  }
  await loadInstallations()
}

async function confirmUpgrade() {
  if (!upgrading.value) return
  upgradeSubmitting.value = true
  const response = await pluginApi.upgradeInstallation(upgrading.value.id)
  upgradeSubmitting.value = false
  if (!response.success) {
    toast.show(response.message, 'error')
    return
  }
  const data = response.data
  upgradeOpen.value = false
  upgrading.value = null
  const purged = data.purged_version
    ? `，旧版本 ${data.purged_version} 已连同插件目录删除`
    : data.purge_skipped_reason
      ? `，旧版本保留：${data.purge_skipped_reason}`
      : ''
  if (data.auto_disabled) {
    toast.show(`已升级到 ${data.to_version}${purged}，但新版本有必填配置缺失，已自动停用，补填后再开启`, 'warning')
  } else if (data.new_capabilities.length) {
    toast.show(`已升级到 ${data.to_version}${purged}，有 ${data.new_capabilities.length} 项新增能力待勾选`, 'info')
  } else {
    toast.show(`已升级到 ${data.to_version}${purged}`, 'success')
  }
  await loadInstallations()
}

function askUninstall(installation: PluginInstallation) {
  deleting.value = installation
  confirmOpen.value = true
}async function confirmUninstall() {
  if (!deleting.value) return
  saving.value = true
  const response = await pluginApi.uninstall(deleting.value.id)
  saving.value = false
  // 失败时保持弹窗打开，与安装、删除、升级等流程保持一致。
  if (!response.success) {
    toast.show(response.message, 'error')
    return
  }
  confirmOpen.value = false
  deleting.value = null
  toast.show('插件已卸载', 'success')
  await loadInstallations()
}

onMounted(() => {
  // 从插件市场点「管理安装」跳过来时带着插件标识，直接定位到这条安装。
  if (typeof route.query.plugin_id === 'string') query.plugin_id = route.query.plugin_id
  loadInstallations()
  loadDictionaries()
  loadCatalog()
  loadBots()
})
</script>

<template>
  <div class="page-enter plugin-installed-page">
    <div class="page-heading">
      <div><p class="eyebrow">INSTALLED PLUGINS</p><h1>已装插件</h1><p>一个账号每个插件只安装一次，配置共用，可分别指定给哪几台机器人使用。</p></div>
      <button type="button" class="button ghost" @click="loadInstallations"><RefreshCw :size="16" />刷新列表</button>
    </div>

    <section class="filter-panel">
      <div class="filter-title"><Filter :size="17" /><strong>筛选条件</strong></div>
      <div class="filter-fields">
        <label>机器人<select v-model="query.bot_qq"><option value="">全部机器人</option><option v-for="bot in bots" :key="bot.id" :value="bot.bot_qq">{{ bot.nickname || bot.bot_qq }} · {{ bot.bot_qq }}</option></select></label>
        <label>插件<input v-model="query.plugin_id" placeholder="插件名称或标识，支持模糊匹配" @keyup.enter="search" /></label>
        <label>启用状态<select v-model="query.enabled"><option value="">全部状态</option><option value="true">已开启</option><option value="false">已停用</option></select></label>
        <div class="filter-actions"><button type="button" class="button primary" @click="search"><Search :size="16" />查询</button><button type="button" class="button ghost" title="重置筛选" @click="resetFilters"><RefreshCw :size="16" />重置</button></div>
      </div>
    </section>

    <section class="content-panel">
      <div class="panel-heading"><div><h2><Boxes :size="17" />安装列表</h2><p>共 {{ result.total }} 条安装记录，停用的插件不会参与事件处理</p></div></div>
      <div class="table-scroll plugin-installed-table">
        <table>
          <thead><tr><th>插件</th><th>生效机器人</th><th>版本</th><th>启用状态</th><th>生效能力</th><th>更新时间</th><th class="action-column">操作</th></tr></thead>
          <tbody>
            <tr v-for="installation in result.items" :key="installation.id">
              <td><strong>{{ installation.plugin_name || '未命名插件' }}</strong><span v-if="!installation.plugin_published" class="record-subtext">插件已被下架</span></td>
              <td><div v-if="installation.bot_qqs.length" class="capability-chips"><span v-for="botQq in installation.bot_qqs" :key="botQq" class="capability-chip" :title="botQq">{{ botLabel(botQq) }}</span></div><span v-else class="capability-empty">未分配机器人</span></td>
              <td class="mono-text">
                <span>{{ installation.version || '—' }}</span>
                <span v-if="installation.has_update" class="version-update-chip" :title="`插件已有新版本 ${installation.latest_version}`">可升级 {{ installation.latest_version }}</span>
              </td>
              <td><button type="button" class="status-toggle" :class="{ active: installation.is_enabled }" :disabled="saving" :title="installation.is_enabled ? '点击停用' : '点击开启'" @click="toggleEnabled(installation)"><i />{{ installation.is_enabled ? '已开启' : '已停用' }}</button></td>
              <td><div v-if="installation.granted_capabilities.length" class="capability-chips"><span v-for="code in installation.granted_capabilities" :key="code" class="capability-chip granted" :title="capabilityName(code)">{{ capabilityName(code) }}</span></div><span v-else class="capability-empty">未生效任何能力</span></td>
              <td class="muted-text">{{ installation.updated_at }}</td>
              <td class="action-buttons"><button v-if="installation.plugin_source_type === 'repo'" type="button" class="icon-button" :class="{ 'button-busy': checkingId === installation.id }" :disabled="checkingId === installation.id" title="检查仓库是否有新版本" @click="checkUpdate(installation)"><RefreshCw :size="16" /></button><button v-if="installation.has_update" type="button" class="icon-button accent-hover" :title="`升级到 ${installation.latest_version}`" @click="askUpgrade(installation)"><CircleArrowUp :size="16" /></button><button type="button" class="icon-button" title="配置插件" @click="openConfig(installation)"><Settings2 :size="16" /></button><button type="button" class="icon-button danger-hover" title="卸载插件" @click="askUninstall(installation)"><Trash2 :size="16" /></button></td>
            </tr>
          </tbody>
        </table>
        <EmptyState v-if="!loading && !result.items.length" text="还没有安装任何插件，可到插件市场安装" />
      </div>
      <div class="pagination"><span>第 {{ result.page }} / {{ result.pages }} 页，共 {{ result.total }} 条</span><div><select v-model.number="query.page_size" title="每页条数" @change="query.page = 1; loadInstallations()"><option :value="10">10 条/页</option><option :value="20">20 条/页</option><option :value="50">50 条/页</option><option :value="100">100 条/页</option></select><button type="button" class="page-button" :disabled="result.page <= 1" @click="changePage(result.page - 1)">上一页</button><button type="button" class="page-button" :disabled="result.page >= result.pages" @click="changePage(result.page + 1)">下一页</button></div></div>
    </section>
    <LoadingOverlay :visible="loading" contained />

    <BaseModal :open="configOpen" title="插件配置" width="640px" @close="configOpen = false">
      <form v-if="editing" class="plugin-config-form" @submit.prevent="saveConfig">
        <div class="task-edit-heading"><strong>{{ editing.plugin_name || '未命名插件' }}</strong><small>已分配 {{ botSelection.length }} 台机器人 · 版本 {{ editing.version || '未知' }}</small></div>
        <p v-if="!editing.plugin_published" class="plugin-config-hint">该插件已被管理员下架，保存后无法继续开启，请尽快卸载。</p>

        <template v-if="editing.config_schema.length">
          <label v-for="field in editing.config_schema" :key="field.key">
            <span class="field-label">{{ field.label }}<span v-if="field.required" class="required">*</span></span>
            <PasswordInput v-if="field.sensitive" :model-value="String(configForm[field.key] ?? '')" placeholder="留空或保留 ****** 表示不修改" @update:model-value="configForm[field.key] = $event" />
            <select v-else-if="field.type === 'select'" :value="String(configForm[field.key] ?? '')" @change="configForm[field.key] = ($event.target as HTMLSelectElement).value"><option v-for="option in field.options" :key="option" :value="option">{{ option }}</option></select>
            <input v-else-if="field.type === 'number'" type="number" :value="Number(configForm[field.key] ?? 0)" @input="configForm[field.key] = Number(($event.target as HTMLInputElement).value)" />
            <button v-else-if="field.type === 'switch'" type="button" class="switch" :class="{ active: Boolean(configForm[field.key]) }" role="switch" :aria-checked="Boolean(configForm[field.key])" @click="configForm[field.key] = !configForm[field.key]"><i /></button>
            <input v-else :value="String(configForm[field.key] ?? '')" maxlength="5000" @input="configForm[field.key] = ($event.target as HTMLInputElement).value" />
            <small>{{ field.key }}{{ field.sensitive ? ' · 敏感项加密保存' : '' }}</small>
          </label>
        </template>
        <p v-else class="plugin-config-hint">当前版本未声明任何配置项。</p>

        <div class="plugin-capability-section">
          <strong>生效机器人</strong>
          <p class="plugin-config-hint">同一账号下多台机器人共用这份配置，取消勾选后该机器人立即不再执行此插件。</p>
          <div v-if="bots.length" class="bot-pick-list">
            <label v-for="bot in bots" :key="bot.id" class="bot-pick-item">
              <input type="checkbox" :checked="botSelection.includes(bot.bot_qq)" @change="toggleBot(bot.bot_qq)" />
              <div><strong>{{ bot.nickname || bot.bot_qq }}</strong><small class="mono-text">{{ bot.bot_qq }}</small></div>
            </label>
          </div>
          <p v-else class="capability-empty">还没有可用机器人，请先在机器人管理中添加。</p>
        </div>

        <div class="plugin-capability-section">
          <strong>本安装生效能力</strong>
          <p class="plugin-config-hint">只能勾选管理员已批准的能力，取消勾选会进一步收窄该插件在这些机器人上的权限。</p>
          <div v-if="editing.approved_capabilities.length" class="capability-list">
            <label v-for="code in editing.approved_capabilities" :key="code" class="capability-item selectable">
              <input type="checkbox" :checked="grantedSelection.includes(code)" @change="toggleGranted(code)" />
              <div><strong>{{ capabilityName(code) }}<span class="risk-chip" :class="capabilityRisk(code)">{{ riskLabel(capabilityRisk(code)) }}</span></strong><code>{{ code }}</code></div>
            </label>
          </div>
          <p v-else class="capability-empty">管理员尚未批准任何能力，该插件无法执行操作。</p>
        </div>

        <div class="modal-form-actions"><button type="button" class="button secondary" :disabled="saving" @click="configOpen = false">取消</button><button class="button primary" type="submit" :disabled="saving"><span v-if="saving" class="button-spinner" /><Save :size="16" />保存配置</button></div>
      </form>
    </BaseModal>

    <BaseModal :open="upgradeOpen" title="升级插件" width="560px" @close="upgradeOpen = false">
      <div v-if="upgrading" class="upgrade-panel">
        <div class="upgrade-heading">
          <div><strong>{{ upgrading.plugin_name || '未命名插件' }}</strong><small class="record-subtext">已安装版本 {{ upgrading.version || '未知' }}</small></div>
          <div class="upgrade-version-switch">
            <span class="upgrade-version">{{ upgrading.version || '未知' }}</span>
            <CircleArrowUp :size="16" />
            <span class="upgrade-version next">{{ upgrading.latest_version }}</span>
          </div>
        </div>

        <ul class="upgrade-points">
          <li>插件配置与生效机器人都会保留，新版本已删除的配置项会自动丢弃。</li>
          <li>生效能力以管理员对新版本的批准范围为上限，不会自动扩大权限。</li>
          <li>{{ upgrading.is_enabled ? '该插件正在开启，升级后从下一条消息开始使用新版本。' : '该插件当前已停用，升级后需要手动开启。' }}</li>
          <li class="upgrade-warning">升级成功后旧版本 {{ upgrading.version || '原版本' }} 的记录与插件目录会被删除，之后无法再回退到该版本（仍被其他账号使用或仍是插件在线版本时会自动保留）。</li>
        </ul>

        <div v-if="pendingCapabilities(upgrading).length" class="upgrade-new-caps">
          <strong><Sparkles :size="15" />新版本新增能力</strong>
          <div class="capability-chips">
            <span v-for="code in pendingCapabilities(upgrading)" :key="code" class="capability-chip">{{ capabilityName(code) }}</span>
          </div>
          <p>这些能力不会自动生效，升级后到「配置插件」里按需勾选。</p>
        </div>

        <div class="modal-form-actions">
          <button type="button" class="button secondary" :disabled="upgradeSubmitting" @click="upgradeOpen = false">取消</button>
          <button type="button" class="button primary" :disabled="upgradeSubmitting" @click="confirmUpgrade"><span v-if="upgradeSubmitting" class="button-spinner" />确认升级</button>
        </div>
      </div>
    </BaseModal>

    <ConfirmDialog :open="confirmOpen" title="卸载插件" :message="`确定卸载插件「${deleting?.plugin_name || '未命名插件'}」吗？卸载后配置会一并移除。`" confirm-text="确定卸载" :loading="saving" @close="confirmOpen = false" @confirm="confirmUninstall" />
  </div>
</template>
