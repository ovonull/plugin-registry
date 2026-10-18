<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { Plus, RefreshCw, UsersRound } from 'lucide-vue-next'
import { userApi } from '../api'
import type { CreateUserPayload, ManagedUser, Pagination } from '../types'
import BaseModal from '../components/common/BaseModal.vue'
import EmptyState from '../components/common/EmptyState.vue'
import LoadingOverlay from '../components/common/LoadingOverlay.vue'
import PasswordInput from '../components/common/PasswordInput.vue'
import { useToast } from '../composables/useToast'

const toast = useToast()
const loading = ref(false)
const saving = ref(false)
const modalOpen = ref(false)
const result = ref<Pagination<ManagedUser>>({
  items: [],
  page: 1,
  page_size: 20,
  total: 0,
  pages: 1,
})
const query = reactive({ page: 1, page_size: 20 })
const form = reactive<CreateUserPayload>({
  username: '',
  nickname: '',
  email: null,
  password: '',
})

async function loadUsers() {
  loading.value = true
  const response = await userApi.list(query)
  loading.value = false
  if (!response.success) return toast.show(response.message, 'error')
  result.value = response.data
}

function resetForm() {
  form.username = ''
  form.nickname = ''
  form.email = null
  form.password = ''
}

function openCreate() {
  resetForm()
  modalOpen.value = true
}

async function createUser() {
  if (!form.username.trim() || !form.password) return
  saving.value = true
  const response = await userApi.create({
    username: form.username.trim(),
    nickname: form.nickname.trim(),
    email: form.email?.trim() || null,
    password: form.password,
  })
  saving.value = false
  if (!response.success) return toast.show(response.message, 'error')
  modalOpen.value = false
  query.page = 1
  await loadUsers()
  toast.show('用户创建成功', 'success')
}

function changePage(page: number) {
  if (page < 1 || page > result.value.pages) return
  query.page = page
  void loadUsers()
}

onMounted(() => { void loadUsers() })
</script>

<template>
  <div class="page-enter user-management-page">
    <div class="page-heading">
      <div>
        <p class="eyebrow">USER MANAGEMENT</p>
        <h1>用户管理</h1>
        <p>查看系统用户并创建普通用户账号。</p>
      </div>
      <div class="page-heading-actions">
        <button type="button" class="button secondary" title="刷新用户列表" :disabled="loading" @click="loadUsers">
          <RefreshCw :size="17" :class="{ spin: loading }" />刷新
        </button>
        <button type="button" class="button primary" title="新建用户" @click="openCreate">
          <Plus :size="18" />新建用户
        </button>
      </div>
    </div>

    <section class="content-panel rules-panel">
      <div class="panel-heading">
        <div>
          <h2><UsersRound :size="17" />用户列表</h2>
          <p>共 {{ result.total }} 个用户</p>
        </div>
        <button type="button" class="icon-button" title="刷新用户列表" :disabled="loading" @click="loadUsers">
          <RefreshCw :size="18" :class="{ spin: loading }" />
        </button>
      </div>

      <div class="table-scroll rules-table user-management-table">
        <table>
          <thead>
            <tr><th>用户名</th><th>昵称</th><th>邮箱</th><th>角色</th><th>状态</th><th>最后登录</th><th>创建时间</th></tr>
          </thead>
          <tbody>
            <tr v-for="user in result.items" :key="user.id">
              <td class="mono-text"><strong>{{ user.username }}</strong></td>
              <td>{{ user.nickname }}</td>
              <td class="muted-text">{{ user.email || '未绑定' }}</td>
              <td><span class="keyword-chip">{{ user.role_name }}</span></td>
              <td><span class="status-badge" :class="user.is_active ? 'enabled' : 'disabled'"><i />{{ user.is_active ? '已启用' : '已停用' }}</span></td>
              <td class="muted-text">{{ user.last_login_at || '从未登录' }}</td>
              <td class="muted-text">{{ user.created_at }}</td>
            </tr>
          </tbody>
        </table>
        <EmptyState v-if="!loading && !result.items.length" text="暂无用户" />
      </div>

      <div class="pagination">
        <span>第 {{ result.page }} / {{ result.pages }} 页，共 {{ result.total }} 条</span>
        <div>
          <select v-model.number="query.page_size" title="每页条数" @change="query.page = 1; loadUsers()">
            <option :value="10">10 条/页</option><option :value="20">20 条/页</option><option :value="50">50 条/页</option><option :value="100">100 条/页</option>
          </select>
          <button type="button" class="page-button" :disabled="result.page <= 1" @click="changePage(result.page - 1)">上一页</button>
          <button type="button" class="page-button" :disabled="result.page >= result.pages" @click="changePage(result.page + 1)">下一页</button>
        </div>
      </div>
    </section>
    <LoadingOverlay :visible="loading" contained />

    <BaseModal :open="modalOpen" title="新建用户" @close="modalOpen = false">
      <form class="rule-form" @submit.prevent="createUser">
        <div class="form-grid">
          <label><span class="field-label">用户名<span class="required">*</span></span><input v-model.trim="form.username" maxlength="30" minlength="3" required autocomplete="username" placeholder="3-30 位字母、数字或下划线" /></label>
          <label><span class="field-label">昵称</span><input v-model.trim="form.nickname" maxlength="50" placeholder="留空则使用用户名" /></label>
        </div>
        <label><span class="field-label">邮箱</span><input v-model="form.email" type="email" maxlength="255" autocomplete="email" placeholder="可选，不填写则不绑定邮箱" /></label>
        <label><span class="field-label">登录密码<span class="required">*</span></span><PasswordInput v-model="form.password" :minlength="8" :maxlength="128" required autocomplete="new-password" placeholder="至少 8 位，包含字母和数字" /></label>
        <small class="form-hint">创建后用户可直接使用用户名和密码登录。</small>
        <div class="modal-form-actions">
          <button type="button" class="button secondary" :disabled="saving" @click="modalOpen = false">取消</button>
          <button type="submit" class="button primary" :disabled="saving"><span v-if="saving" class="button-spinner" />创建用户</button>
        </div>
      </form>
    </BaseModal>
  </div>
</template>
