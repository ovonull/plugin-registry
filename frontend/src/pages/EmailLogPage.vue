<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { Filter, Mail, RefreshCw, Search } from 'lucide-vue-next'
import { emailLogApi, publicApi } from '../api'
import type { DictionaryOption, EmailLogRecord, Pagination } from '../types'
import EmptyState from '../components/common/EmptyState.vue'
import LoadingOverlay from '../components/common/LoadingOverlay.vue'
import { useToast } from '../composables/useToast'

const toast = useToast()
const loading = ref(false)
const actionOptions = ref<DictionaryOption[]>([])
const query = reactive({
  keyword: '',
  action: '',
  result: '',
  start_time: '',
  end_time: '',
  page: 1,
  page_size: 20,
})
const result = ref<Pagination<EmailLogRecord>>({
  items: [], page: 1, page_size: 20, total: 0, pages: 1,
})

/** 组装查询参数，空值不传给后端。 */
function filters() {
  return {
    keyword: query.keyword.trim() || undefined,
    action: query.action || undefined,
    result: query.result || undefined,
    start_time: query.start_time || undefined,
    end_time: query.end_time || undefined,
  }
}

/** 从数据字典读取触发操作中文名称，禁止前端硬编码映射。 */
async function loadActions() {
  const response = await publicApi.dictionaries(['email_log_action'])
  if (!response.success) {
    toast.show(response.message, 'error')
    return
  }
  actionOptions.value = response.data.email_log_action || []
}

async function loadLogs() {
  loading.value = true
  const response = await emailLogApi.list({
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
    keyword: '', action: '', result: '', start_time: '', end_time: '', page: 1,
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

/** 空值统一显示为占位符。 */
function display(value: string | null | undefined) {
  return value === null || value === undefined || value === '' ? '—' : value
}

onMounted(() => {
  void loadActions()
  void loadLogs()
})
</script>

<template>
  <div class="page-enter email-log-page">
    <div class="page-heading">
      <div>
        <p class="eyebrow">EMAIL LOGS</p>
        <h1>邮件日志</h1>
        <p>查看全部用户的邮件发送记录，包含发送成功、失败、被限流或配置缺失被拒绝的明细。</p>
      </div>
      <button type="button" class="button secondary" title="刷新邮件日志" :disabled="loading" @click="loadLogs">
        <RefreshCw :size="17" :class="{ spin: loading }" />刷新
      </button>
    </div>

    <section class="filter-panel">
      <div class="filter-title"><Filter :size="17" /><strong>筛选条件</strong></div>
      <div class="filter-fields">
        <label>关键字<input v-model="query.keyword" placeholder="收件邮箱 / 客户端 IP / 结果说明" @keyup.enter="search" /></label>
        <label>触发操作
          <select v-model="query.action" title="选择触发操作">
            <option value="">全部</option>
            <option v-for="item in actionOptions" :key="item.value" :value="item.value">{{ item.label }}</option>
          </select>
        </label>
        <label>发送结果
          <select v-model="query.result" title="选择发送结果">
            <option value="">全部</option>
            <option value="success">成功</option>
            <option value="failed">失败</option>
          </select>
        </label>
        <label>开始日期<input v-model="query.start_time" type="date" title="开始日期" /></label>
        <label>结束日期<input v-model="query.end_time" type="date" title="结束日期" /></label>
        <div class="filter-actions">
          <button type="button" class="button primary" :disabled="loading" @click="search"><Search :size="16" />查询</button>
          <button type="button" class="button ghost" title="重置筛选" @click="resetFilters"><RefreshCw :size="16" />重置</button>
        </div>
      </div>
    </section>

    <section class="content-panel rules-panel">
      <div class="panel-heading">
        <div>
          <h2><Mail :size="17" />邮件记录</h2>
          <p>共 {{ result.total }} 条 · 管理员视图</p>
        </div>
        <button type="button" class="icon-button" title="刷新邮件日志" :disabled="loading" @click="loadLogs">
          <RefreshCw :size="18" :class="{ spin: loading }" />
        </button>
      </div>

      <div class="table-scroll rules-table email-log-table">
        <table>
          <thead>
            <tr>
              <th>发生时间</th><th>收件邮箱</th><th>触发操作</th><th>结果</th><th>客户端 IP</th><th>结果说明</th><th>所属用户</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in result.items" :key="item.id">
              <td class="muted-text nowrap">{{ item.created_at }}</td>
              <td class="mono-text">{{ display(item.email) }}</td>
              <td>{{ item.action_name }}</td>
              <td>
                <span class="status-badge" :class="item.is_success ? 'enabled' : 'disabled'"><i />{{ item.is_success ? '成功' : '失败' }}</span>
              </td>
              <td class="mono-text">{{ display(item.client_ip) }}</td>
              <td class="muted-text">{{ display(item.result_message) }}</td>
              <td class="muted-text">{{ display(item.owner_username) }}</td>
            </tr>
          </tbody>
        </table>
        <EmptyState v-if="!loading && !result.items.length" text="暂无邮件日志" />
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