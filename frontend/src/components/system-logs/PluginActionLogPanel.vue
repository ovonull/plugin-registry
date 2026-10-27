<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { Filter, RefreshCw, Search, TerminalSquare } from 'lucide-vue-next'
import { pluginAdminApi, publicApi } from '../../api'
import type { DictionaryOption, Pagination, PluginActionLogRecord } from '../../types'
import EmptyState from '../common/EmptyState.vue'
import LoadingOverlay from '../common/LoadingOverlay.vue'
import { useToast } from '../../composables/useToast'

const toast = useToast()
const loading = ref(false)
const resultOptions = ref<DictionaryOption[]>([])
const query = reactive({
  keyword: '',
  plugin_id: '',
  bot_qq: '',
  result_code: '',
  page: 1,
  page_size: 20,
})
const result = ref<Pagination<PluginActionLogRecord>>({
  items: [], page: 1, page_size: 20, total: 0, pages: 1,
})

/** 组装查询参数，空值不传给后端。 */
function filters() {
  return {
    keyword: query.keyword.trim() || undefined,
    plugin_id: query.plugin_id.trim() || undefined,
    bot_qq: query.bot_qq.trim() || undefined,
    result_code: query.result_code || undefined,
  }
}

/** 从数据字典读取审计结果中文名称，禁止前端硬编码映射。 */
async function loadResultOptions() {
  const response = await publicApi.dictionaries(['plugin_action_result'])
  if (!response.success) {
    toast.show(response.message, 'error')
    return
  }
  resultOptions.value = response.data.plugin_action_result || []
}

async function loadLogs() {
  loading.value = true
  const response = await pluginAdminApi.actionLogs({
    page: query.page, page_size: query.page_size, ...filters(),
  })
  loading.value = false
  if (!response.success) {
    toast.show(response.message, 'error')
    return
  }
  result.value = response.data
}

function search() {
  query.page = 1
  void loadLogs()
}

function resetFilters() {
  Object.assign(query, {
    keyword: '', plugin_id: '', bot_qq: '', result_code: '', page: 1,
  })
  void loadLogs()
}

function changePage(page: number) {
  if (page < 1 || page > result.value.pages) return
  query.page = page
  void loadLogs()
}

function changePageSize() {
  query.page = 1
  void loadLogs()
}

/** 结果编码转中文名称，字典里没有时回退显示原编码。 */
function resultName(code: string) {
  return resultOptions.value.find((item) => item.value === code)?.label || code || '—'
}

/** 空值统一显示为占位符。 */
function display(value: string | number | null | undefined) {
  return value === null || value === undefined || value === '' ? '—' : String(value)
}

onMounted(() => {
  void loadResultOptions()
  void loadLogs()
})
</script>

<template>
  <div class="plugin-action-log-panel">
    <section class="filter-panel">
      <div class="filter-title"><Filter :size="17" /><strong>筛选条件</strong></div>
      <div class="filter-fields">
        <label>关键字<input v-model="query.keyword" placeholder="动作名称 / 能力标识" @keyup.enter="search" /></label>
        <label>插件标识<input v-model="query.plugin_id" maxlength="100" placeholder="例如 plughub.demo" @keyup.enter="search" /></label>
        <label>机器人 QQ<input v-model="query.bot_qq" maxlength="20" placeholder="按机器人筛选" @keyup.enter="search" /></label>
        <label>调用结果
          <select v-model="query.result_code" title="选择调用结果">
            <option value="">全部</option>
            <option v-for="item in resultOptions" :key="item.value" :value="item.value">{{ item.label }}</option>
          </select>
        </label>
        <div class="filter-actions">
          <button type="button" class="button primary" :disabled="loading" @click="search"><Search :size="16" />查询</button>
          <button type="button" class="button ghost" title="重置筛选" @click="resetFilters"><RefreshCw :size="16" />重置</button>
        </div>
      </div>
    </section>

    <section class="content-panel rules-panel">
      <div class="panel-heading">
        <div>
          <h2><TerminalSquare :size="17" />插件动作审计</h2>
          <p>共 {{ result.total }} 条 · 记录只追加，未授权调用也会留痕</p>
        </div>
        <button type="button" class="icon-button" title="刷新审计记录" :disabled="loading" @click="loadLogs">
          <RefreshCw :size="18" :class="{ spin: loading }" />
        </button>
      </div>

      <div class="table-scroll rules-table plugin-action-log-table">
        <table>
          <thead>
            <tr>
              <th>发生时间</th><th>插件标识</th><th>动作名称</th><th>能力标识</th><th>机器人 QQ</th>
              <th>群号</th><th>触发用户</th><th>调用结果</th><th>耗时</th><th>参数</th><th>结果说明</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in result.items" :key="item.id">
              <td class="muted-text nowrap">{{ item.created_at }}</td>
              <td class="mono-text">{{ display(item.plugin_id) }}</td>
              <td>{{ display(item.action_name) }}</td>
              <td class="mono-text">{{ display(item.capability) }}</td>
              <td class="mono-text">{{ display(item.bot_qq) }}</td>
              <td class="mono-text">{{ display(item.group_id) }}</td>
              <td class="mono-text">{{ display(item.user_id) }}</td>
              <td>
                <span class="status-badge" :class="item.result_code === 'ok' ? 'enabled' : 'disabled'"><i />{{ resultName(item.result_code) }}</span>
              </td>
              <td class="mono-text nowrap">{{ item.duration_ms }} ms</td>
              <td class="muted-text params-cell" :title="item.params">{{ display(item.params) }}</td>
              <td class="muted-text">{{ display(item.result_message) }}</td>
            </tr>
          </tbody>
        </table>
        <EmptyState v-if="!loading && !result.items.length" text="暂无插件动作审计记录" />
      </div>

      <div class="pagination">
        <span>第 {{ result.page }} / {{ result.pages }} 页，共 {{ result.total }} 条</span>
        <div>
          <select v-model.number="query.page_size" title="每页条数" @change="changePageSize">
            <option :value="10">10 条/页</option><option :value="20">20 条/页</option><option :value="50">50 条/页</option><option :value="100">100 条/页</option>
          </select>
          <button type="button" class="page-button" :disabled="result.page <= 1" @click="changePage(result.page - 1)">上一页</button>
          <button type="button" class="page-button" :disabled="result.page >= result.pages" @click="changePage(result.page + 1)">下一页</button>
        </div>
      </div>
    </section>
    <LoadingOverlay :visible="loading" contained />
  </div>
</template>

<style scoped>
.params-cell {
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
