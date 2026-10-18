<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { Clock3, Edit3, Play, RefreshCw } from 'lucide-vue-next'
import { scheduledTaskApi } from '../api'
import type { Pagination, ScheduledTask, ScheduledTaskPayload } from '../types'
import BaseModal from '../components/common/BaseModal.vue'
import EmptyState from '../components/common/EmptyState.vue'
import LoadingOverlay from '../components/common/LoadingOverlay.vue'
import { useToast } from '../composables/useToast'

const toast = useToast()
const loading = ref(false)
const saving = ref(false)
const runningId = ref<number | null>(null)
const modalOpen = ref(false)
const editing = ref<ScheduledTask | null>(null)
const result = ref<Pagination<ScheduledTask>>({ items: [], page: 1, page_size: 20, total: 0, pages: 1 })
const query = reactive({ page: 1, page_size: 20 })
const form = reactive<ScheduledTaskPayload>({ interval_seconds: 300, is_enabled: true })

const statusLabels: Record<string, string> = {
  pending: '等待执行', running: '执行中', success: '执行成功', partial: '部分失败', failed: '执行失败', unsupported: '当前版本不支持',
}

function statusClass(status: string) {
  if (status === 'success') return 'enabled'
  if (status === 'failed' || status === 'partial') return 'disabled'
  return ''
}

function statusText(status: string) {
  return statusLabels[status] || status || '未执行'
}

function resultText(task: ScheduledTask) {
  if (!task.last_result) return '暂无执行记录'
  try {
    const value = JSON.parse(task.last_result) as Record<string, unknown>
    if (typeof value.approved === 'number') {
      return `检查 ${value.bots ?? 0} 个机器人，通过 ${value.approved} 条申请`
    }
    if (typeof value.classified === 'number') {
      return `处理 ${value.pending ?? 0} 条未分类记录，完成 ${value.classified} 条`
    }
  } catch {
    return task.last_result
  }
  return task.last_result
}

async function loadTasks() {
  loading.value = true
  const response = await scheduledTaskApi.list(query)
  loading.value = false
  if (!response.success) return toast.show(response.message, 'error')
  result.value = response.data
}

function openEdit(task: ScheduledTask) {
  editing.value = task
  form.interval_seconds = task.interval_seconds
  form.is_enabled = task.is_enabled
  modalOpen.value = true
}

async function saveTask() {
  if (!editing.value || form.interval_seconds < 1) return
  saving.value = true
  const response = await scheduledTaskApi.update(editing.value.id, { ...form })
  saving.value = false
  if (!response.success) return toast.show(response.message, 'error')
  const index = result.value.items.findIndex((item) => item.id === response.data.id)
  if (index >= 0) result.value.items[index] = response.data
  modalOpen.value = false
  toast.show('定时任务已保存', 'success')
}

async function toggleTask(task: ScheduledTask) {
  const response = await scheduledTaskApi.update(task.id, {
    interval_seconds: task.interval_seconds,
    is_enabled: !task.is_enabled,
  })
  if (!response.success) return toast.show(response.message, 'error')
  const index = result.value.items.findIndex((item) => item.id === response.data.id)
  if (index >= 0) result.value.items[index] = response.data
  toast.show(response.data.is_enabled ? '定时任务已启动' : '定时任务已暂停', 'success')
}

async function runTask(task: ScheduledTask) {
  if (runningId.value !== null) return
  runningId.value = task.id
  const response = await scheduledTaskApi.run(task.id)
  runningId.value = null
  if (!response.success) return toast.show(response.message, 'error')
  const index = result.value.items.findIndex((item) => item.id === response.data.id)
  if (index >= 0) result.value.items[index] = response.data
  const hasError = ['unsupported', 'failed', 'partial'].includes(response.data.last_status)
  toast.show(response.message || '定时任务已执行', hasError ? 'error' : 'success')
}

onMounted(() => { void loadTasks() })

function changePage(page: number) {
  if (page < 1 || page > result.value.pages) return
  query.page = page
  void loadTasks()
}
</script>

<template>
  <div class="page-enter scheduled-task-page">
    <div class="page-heading">
      <div><p class="eyebrow">SCHEDULED TASKS</p><h1>定时任务</h1><p>统一管理系统后台任务的执行间隔和运行状态。</p></div>
      <button type="button" class="button secondary" :disabled="loading" title="刷新定时任务" @click="loadTasks"><RefreshCw :size="17" :class="{ spin: loading }" />刷新</button>
    </div>

    <section class="content-panel rules-panel">
      <div class="panel-heading"><div><h2><Clock3 :size="17" />任务列表</h2><p>共 {{ result.total }} 项任务</p></div><button type="button" class="icon-button" title="刷新列表" @click="loadTasks"><RefreshCw :size="18" /></button></div>
      <div class="table-scroll rules-table scheduled-task-table">
        <table>
          <thead><tr><th>任务名称</th><th>执行间隔</th><th>下次执行</th><th>上次执行</th><th>最近结果</th><th>状态</th><th class="action-column">操作</th></tr></thead>
          <tbody>
            <tr v-for="task in result.items" :key="task.id">
              <td><strong>{{ task.task_name }}</strong><small class="task-description">{{ task.task_description }}</small></td>
              <td class="mono-text">{{ task.interval_seconds }} 秒</td>
              <td class="muted-text">{{ task.is_enabled ? (task.next_run_at || '等待调度') : '已暂停' }}</td>
              <td class="muted-text">{{ task.last_run_at || '暂无' }}</td>
              <td class="task-result"><span v-if="task.last_error" class="task-error" :title="task.last_error">{{ task.last_error }}</span><span v-else>{{ resultText(task) }}</span></td>
              <td><button type="button" class="status-toggle" :class="[{ active: task.is_enabled }, statusClass(task.last_status)]" :title="task.is_enabled ? '点击暂停' : '点击启动'" @click="toggleTask(task)"><i />{{ task.is_enabled ? '运行中' : '已暂停' }}</button><small class="task-last-status">{{ statusText(task.last_status) }}</small></td>
              <td class="action-buttons"><button type="button" class="icon-button" :disabled="runningId !== null" title="立即执行" @click="runTask(task)"><RefreshCw v-if="runningId === task.id" class="spin" :size="16" /><Play v-else :size="16" /></button><button type="button" class="icon-button" title="编辑执行间隔" @click="openEdit(task)"><Edit3 :size="16" /></button></td>
            </tr>
          </tbody>
        </table>
        <EmptyState v-if="!loading && !result.items.length" text="暂无定时任务" />
      </div>
      <div class="pagination"><span>第 {{ result.page }} / {{ result.pages }} 页，共 {{ result.total }} 条</span><div><select v-model.number="query.page_size" title="每页条数" @change="query.page = 1; loadTasks()"><option :value="10">10 条/页</option><option :value="20">20 条/页</option><option :value="50">50 条/页</option><option :value="100">100 条/页</option></select><button type="button" class="page-button" :disabled="result.page <= 1" @click="changePage(result.page - 1)">上一页</button><button type="button" class="page-button" :disabled="result.page >= result.pages" @click="changePage(result.page + 1)">下一页</button></div></div>
    </section>
    <LoadingOverlay :visible="loading" contained />

    <BaseModal :open="modalOpen" title="编辑定时任务" @close="modalOpen = false">
      <form class="rule-form" @submit.prevent="saveTask">
        <div class="task-edit-heading"><strong>{{ editing?.task_name }}</strong><small>{{ editing?.task_description }}</small></div>
        <label><span class="field-label">执行间隔（秒）<span class="required">*</span></span><input v-model.number="form.interval_seconds" type="number" min="1" max="2592000" required /><small>支持 1 秒至 30 天。</small></label>
        <label class="switch-field"><span>启用定时任务</span><button type="button" class="switch" :class="{ active: form.is_enabled }" role="switch" :aria-checked="form.is_enabled" @click="form.is_enabled = !form.is_enabled"><i /></button></label>
        <div class="modal-form-actions"><button type="button" class="button secondary" :disabled="saving" @click="modalOpen = false">取消</button><button class="button primary" type="submit" :disabled="saving"><span v-if="saving" class="button-spinner" />保存任务</button></div>
      </form>
    </BaseModal>
  </div>
</template>
