<script setup lang="ts">
/**
 * 我的插件：当前账号提交过的插件与审核进度。
 *
 * 插件库只负责分发，不运行插件，所以这里只保留「提交记录」视角，
 * 安装与启用属于下游宿主的能力。
 */
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  BadgeCheck,
  CloudUpload,
  Filter,
  FolderGit2,
  RefreshCw,
  Search,
} from 'lucide-vue-next'
import { pluginApi, publicApi } from '../api'
import type {
  DictionaryOption,
  MyPluginItem,
  Pagination,
  PluginDetail,
  PluginVersionInfo,
} from '../types'
import BaseModal from '../components/common/BaseModal.vue'
import EmptyState from '../components/common/EmptyState.vue'
import LoadingOverlay from '../components/common/LoadingOverlay.vue'
import { useToast } from '../composables/useToast'

const toast = useToast()
const router = useRouter()
const loading = ref(false)
const query = reactive({ keyword: '', review_status: '', page: 1, page_size: 20 })
const result = ref<Pagination<MyPluginItem>>({
  items: [], page: 1, page_size: 20, total: 0, pages: 1,
})
const reviewStatus = ref<DictionaryOption[]>([])
const detail = ref<PluginDetail | null>(null)
const detailOpen = ref(false)
const detailLoading = ref(false)

async function loadMine() {
  loading.value = true
  const response = await pluginApi.mine({ ...query })
  loading.value = false
  if (!response.success) return toast.show(response.message, 'error')
  result.value = response.data
}

async function loadDictionaries() {
  if (reviewStatus.value.length) return
  const response = await publicApi.dictionaries(['plugin_review_status'])
  if (response.success) reviewStatus.value = response.data.plugin_review_status || []
}

function search() {
  query.page = 1
  loadMine()
}

function resetFilters() {
  query.keyword = ''
  query.review_status = ''
  query.page = 1
  loadMine()
}

function changePage(page: number) {
  if (page < 1 || page > result.value.pages) return
  query.page = page
  loadMine()
}

function statusLabel(status: string): string {
  const found = reviewStatus.value.find((item) => item.value === status)
  if (found) return found.label
  return { pending: '待审核', approved: '已通过', rejected: '已驳回' }[status] || status
}

function statusClass(status: string): string {
  if (status === 'approved') return 'approved'
  if (status === 'rejected') return 'rejected'
  return 'pending'
}

/** 最新提交版本；没有任何版本时返回 null。 */
function latest(plugin: MyPluginItem): PluginVersionInfo | null {
  return plugin.latest_version
}

async function openDetail(plugin: MyPluginItem) {
  detailOpen.value = true
  detailLoading.value = true
  detail.value = null
  const response = await pluginApi.detail(plugin.plugin_id)
  detailLoading.value = false
  if (!response.success) return toast.show(response.message, 'error')
  detail.value = response.data
}

function goMarket() {
  router.push('/plugins')
}

onMounted(() => {
  loadDictionaries()
  loadMine()
})
</script>

<template>
  <div class="page-enter my-plugins-page">
    <div class="page-heading">
      <div>
        <p class="eyebrow">MY PLUGINS</p>
        <h1>我的插件</h1>
        <p>这里是你提交过的插件和每个版本的审核进度，审核通过后由管理员上架到插件市场。</p>
      </div>
      <div class="page-heading-actions">
        <button type="button" class="button secondary" @click="goMarket">
          <CloudUpload :size="16" />提交新插件
        </button>
        <button type="button" class="button ghost" @click="loadMine">
          <RefreshCw :size="16" />刷新列表
        </button>
      </div>
    </div>

    <section class="filter-panel">
      <div class="filter-title"><Filter :size="17" /><strong>筛选条件</strong></div>
      <div class="filter-fields">
        <label>插件<input v-model="query.keyword" placeholder="插件名称或标识，支持模糊匹配" @keyup.enter="search" /></label>
        <label>审核状态<select v-model="query.review_status"><option value="">全部状态</option><option v-for="item in reviewStatus" :key="item.value" :value="item.value">{{ item.label }}</option></select></label>
        <div class="filter-actions">
          <button type="button" class="button primary" @click="search"><Search :size="16" />查询</button>
          <button type="button" class="button ghost" title="重置筛选" @click="resetFilters"><RefreshCw :size="16" />重置</button>
        </div>
      </div>
    </section>

    <section class="content-panel">
      <div class="panel-heading">
        <div>
          <h2><FolderGit2 :size="17" />提交记录</h2>
          <p>共 {{ result.total }} 个插件，版本提交后不可修改，需要调整请提交新版本</p>
        </div>
      </div>
      <div class="table-scroll my-plugin-table">
        <table>
          <thead>
            <tr>
              <th>插件</th>
              <th>最新版本</th>
              <th>审核状态</th>
              <th>上架版本</th>
              <th>版本数</th>
              <th>上架状态</th>
              <th>更新时间</th>
              <th class="action-column">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="plugin in result.items" :key="plugin.plugin_id">
              <td>
                <strong>{{ plugin.name || plugin.plugin_id }}</strong>
                <span class="record-subtext mono-text">{{ plugin.plugin_id }}</span>
              </td>
              <td class="mono-text">{{ latest(plugin)?.version || '—' }}</td>
              <td>
                <span
                  v-if="latest(plugin)"
                  class="status-badge"
                  :class="statusClass(latest(plugin)!.review_status)"
                >
                  <i />{{ statusLabel(latest(plugin)!.review_status) }}
                </span>
                <span v-else class="capability-empty">无版本</span>
              </td>
              <td class="mono-text">{{ plugin.current_version?.version || '—' }}</td>
              <td>
                <span class="muted-text">{{ plugin.version_count }} 个</span>
                <span v-if="plugin.pending_count" class="version-update-chip">
                  {{ plugin.pending_count }} 待审
                </span>
              </td>
              <td>
                <span class="status-badge" :class="plugin.is_published ? 'enabled' : 'disabled'">
                  <i />{{ plugin.is_published ? '已上架' : '未上架' }}
                </span>
              </td>
              <td class="muted-text">{{ plugin.updated_at }}</td>
              <td class="action-buttons">
                <button type="button" class="icon-button" title="查看提交记录" @click="openDetail(plugin)">
                  <BadgeCheck :size="16" />
                </button>
              </td>
            </tr>
          </tbody>
        </table>
        <EmptyState v-if="!loading && !result.items.length" text="还没有提交过插件，去插件市场点「提交插件」开始" />
      </div>
      <div class="pagination">
        <span>第 {{ result.page }} / {{ result.pages }} 页，共 {{ result.total }} 条</span>
        <div>
          <select v-model.number="query.page_size" title="每页条数" @change="query.page = 1; loadMine()">
            <option :value="10">10 条/页</option>
            <option :value="20">20 条/页</option>
            <option :value="50">50 条/页</option>
          </select>
          <button type="button" class="page-button" :disabled="result.page <= 1" @click="changePage(result.page - 1)">上一页</button>
          <button type="button" class="page-button" :disabled="result.page >= result.pages" @click="changePage(result.page + 1)">下一页</button>
        </div>
      </div>
    </section>
    <LoadingOverlay :visible="loading" contained />

    <BaseModal :open="detailOpen" :title="`${detail?.plugin.name || '插件'} 的提交记录`" width="760px" @close="detailOpen = false">
      <div v-if="detail" class="my-plugin-detail">
        <div class="plugin-detail-head">
          <h3>{{ detail.plugin.name }} <code class="mono-text">{{ detail.plugin.plugin_id }}</code></h3>
          <p>{{ detail.plugin.summary || '作者尚未提供简介' }}</p>
        </div>
        <div class="table-scroll plugin-detail-panel">
          <table>
            <thead><tr><th>版本</th><th>审核状态</th><th>申请能力</th><th>审核意见</th><th>提交时间</th></tr></thead>
            <tbody>
              <tr v-for="version in detail.versions" :key="version.id">
                <td class="mono-text">{{ version.version }}</td>
                <td>
                  <span class="status-badge" :class="statusClass(version.review_status)">
                    <i />{{ statusLabel(version.review_status) }}
                  </span>
                </td>
                <td>
                  <div v-if="version.capabilities.length" class="capability-chips">
                    <span v-for="code in version.capabilities" :key="code" class="capability-chip">{{ code }}</span>
                  </div>
                  <span v-else class="capability-empty">无</span>
                </td>
                <td class="muted-text">{{ version.review_comment || '—' }}</td>
                <td class="muted-text">{{ version.created_at || '—' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p class="form-hint">版本提交后不可修改，需要调整请提交新版本。</p>
      </div>
      <div v-else-if="detailLoading" class="my-plugin-detail-loading">正在加载提交记录…</div>
    </BaseModal>
  </div>
</template>
