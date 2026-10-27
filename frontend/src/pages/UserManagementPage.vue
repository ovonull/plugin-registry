<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { Edit3, Filter, KeyRound, Plus, RefreshCw, Search, Trash2, UsersRound } from 'lucide-vue-next'
import { publicApi, userApi } from '../api'
import type {
  CreateUserPayload,
  DictionaryOption,
  ManagedUser,
  Pagination,
  UpdateUserPayload,
} from '../types'
import BaseModal from '../components/common/BaseModal.vue'
import ConfirmDialog from '../components/common/ConfirmDialog.vue'
import EmptyState from '../components/common/EmptyState.vue'
import LoadingOverlay from '../components/common/LoadingOverlay.vue'
import PasswordInput from '../components/common/PasswordInput.vue'
import { useAuth } from '../composables/useAuth'
import { useToast } from '../composables/useToast'

const auth = useAuth()
const toast = useToast()
const loading = ref(false)
const saving = ref(false)
const roleOptions = ref<DictionaryOption[]>([])
const result = ref<Pagination<ManagedUser>>({
  items: [],
  page: 1,
  page_size: 20,
  total: 0,
  pages: 1,
})
const query = reactive({
  keyword: '',
  role_code: '',
  status: '' as '' | 'active' | 'inactive',
  page: 1,
  page_size: 20,
})

// 新建用户
const createOpen = ref(false)
const createForm = reactive<CreateUserPayload>({
  username: '',
  nickname: '',
  email: null,
  password: '',
})

// 编辑用户
const editOpen = ref(false)
const editForm = reactive({
  id: 0,
  username: '',
  nickname: '',
  email: '',
  role_code: 'user' as 'admin' | 'user',
  is_active: true,
})

// 重置密码
const resetOpen = ref(false)
const resetForm = reactive({
  id: 0,
  username: '',
  newPassword: '',
  confirmPassword: '',
})

// 删除确认
const deleteOpen = ref(false)
const deleteTarget = ref<ManagedUser | null>(null)

/** 判断某行是否是当前登录的管理员自己，自己不允许在用户管理里被操作。 */
function isSelf(user: ManagedUser) {
  return user.id === auth.state.user?.id
}

const deleteMessage = computed(() => {
  const user = deleteTarget.value
  if (!user) return ''
  return `确定删除用户「${user.nickname}（${user.username}）」吗？删除后该账号无法登录，历史数据仍会保留。`
})

/** 角色中文名称取自数据字典，禁止前端硬编码。 */
async function loadRoleOptions() {
  const response = await publicApi.dictionaries(['user_role'])
  if (!response.success) {
    toast.show(response.message, 'error')
    return
  }
  roleOptions.value = response.data.user_role || []
}

/** 组装查询参数，空值不传给后端。 */
function filters() {
  return {
    keyword: query.keyword.trim() || undefined,
    role_code: query.role_code || undefined,
    is_active: query.status === 'active' ? true : query.status === 'inactive' ? false : undefined,
  }
}

async function loadUsers() {
  loading.value = true
  const response = await userApi.list({
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
  void loadUsers()
}

function resetFilters() {
  Object.assign(query, { keyword: '', role_code: '', status: '', page: 1 })
  void loadUsers()
}

function changePage(page: number) {
  if (page < 1 || page > result.value.pages) return
  query.page = page
  void loadUsers()
}

function changePageSize() {
  query.page = 1
  void loadUsers()
}

function openCreate() {
  Object.assign(createForm, { username: '', nickname: '', email: null, password: '' })
  createOpen.value = true
}

async function createUser() {
  if (!createForm.username.trim() || !createForm.password) {
    return toast.show('请填写用户名和登录密码', 'error')
  }
  saving.value = true
  const response = await userApi.create({
    username: createForm.username.trim(),
    nickname: createForm.nickname.trim(),
    email: createForm.email?.trim() || null,
    password: createForm.password,
  })
  saving.value = false
  if (!response.success) return toast.show(response.message, 'error')
  createOpen.value = false
  query.page = 1
  await loadUsers()
  toast.show('用户创建成功', 'success')
}

function openEdit(user: ManagedUser) {
  if (isSelf(user)) return toast.show('不能在这里修改自己的账号，请前往个人设置', 'error')
  Object.assign(editForm, {
    id: user.id,
    username: user.username,
    nickname: user.nickname,
    email: user.email || '',
    role_code: user.role_code === 'admin' ? 'admin' : 'user',
    is_active: user.is_active,
  })
  editOpen.value = true
}

async function saveEdit() {
  if (!editForm.nickname.trim()) return toast.show('昵称不能为空', 'error')
  const payload: UpdateUserPayload = {
    nickname: editForm.nickname.trim(),
    // 留空表示清空邮箱绑定，后端按「显式提交」处理。
    email: editForm.email.trim() || null,
    role_code: editForm.role_code,
    is_active: editForm.is_active,
  }
  saving.value = true
  const response = await userApi.update(editForm.id, payload)
  saving.value = false
  if (!response.success) return toast.show(response.message, 'error')
  editOpen.value = false
  await loadUsers()
  toast.show('用户信息已更新', 'success')
}

function openReset(user: ManagedUser) {
  if (isSelf(user)) return toast.show('不能在这里重置自己的密码，请前往个人设置', 'error')
  Object.assign(resetForm, {
    id: user.id, username: user.username, newPassword: '', confirmPassword: '',
  })
  resetOpen.value = true
}

async function submitReset() {
  if (!resetForm.newPassword) return toast.show('请输入新密码', 'error')
  if (resetForm.newPassword !== resetForm.confirmPassword) {
    return toast.show('两次输入的新密码不一致', 'error')
  }
  saving.value = true
  const response = await userApi.resetPassword(resetForm.id, resetForm.newPassword)
  saving.value = false
  if (!response.success) return toast.show(response.message, 'error')
  resetOpen.value = false
  toast.show('密码已重置，该用户需重新登录', 'success')
}

/** 快捷切换启用状态，停用会立即让该用户的所有登录会话失效。 */
async function toggleActive(user: ManagedUser) {
  if (isSelf(user)) return toast.show('不能在这里停用自己的账号', 'error')
  saving.value = true
  const response = await userApi.update(user.id, { is_active: !user.is_active })
  saving.value = false
  if (!response.success) return toast.show(response.message, 'error')
  await loadUsers()
  toast.show(user.is_active ? '用户已停用' : '用户已启用', 'success')
}

function askDelete(user: ManagedUser) {
  if (isSelf(user)) return toast.show('不能删除自己的账号', 'error')
  deleteTarget.value = user
  deleteOpen.value = true
}

async function confirmDelete() {
  const user = deleteTarget.value
  if (!user) return
  saving.value = true
  const response = await userApi.remove(user.id)
  saving.value = false
  if (!response.success) return toast.show(response.message, 'error')
  deleteOpen.value = false
  deleteTarget.value = null
  // 删掉当前页最后一条时回退一页，避免停留在空页。
  if (result.value.items.length === 1 && query.page > 1) query.page -= 1
  await loadUsers()
  toast.show('用户已删除', 'success')
}

onMounted(() => {
  void loadRoleOptions()
  void loadUsers()
})
</script>

<template>
  <div class="page-enter user-management-page">
    <div class="page-heading">
      <div>
        <p class="eyebrow">USER MANAGEMENT</p>
        <h1>用户管理</h1>
        <p>查看系统用户，创建账号，并管理角色、状态与登录密码。仅管理员可见。</p>
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

    <section class="filter-panel">
      <div class="filter-title"><Filter :size="17" /><strong>筛选条件</strong></div>
      <div class="filter-fields">
        <label>关键字<input v-model="query.keyword" placeholder="用户名 / 昵称 / 邮箱" @keyup.enter="search" /></label>
        <label>角色
          <select v-model="query.role_code" title="按角色筛选">
            <option value="">全部</option>
            <option v-for="item in roleOptions" :key="item.value" :value="item.value">{{ item.label }}</option>
          </select>
        </label>
        <label>状态
          <select v-model="query.status" title="按状态筛选">
            <option value="">全部</option>
            <option value="active">已启用</option>
            <option value="inactive">已停用</option>
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
            <tr>
              <th>用户名</th><th>昵称</th><th>邮箱</th><th>角色</th><th>状态</th>
              <th>最后登录</th><th>创建时间</th><th class="action-column">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="user in result.items" :key="user.id">
              <td class="mono-text">
                <strong>{{ user.username }}</strong>
                <span v-if="isSelf(user)" class="keyword-chip self-chip">当前账号</span>
              </td>
              <td>{{ user.nickname }}</td>
              <td class="muted-text">{{ user.email || '未绑定' }}</td>
              <td><span class="keyword-chip">{{ user.role_name }}</span></td>
              <td>
                <button
                  type="button"
                  class="status-toggle"
                  :class="{ active: user.is_active }"
                  :disabled="saving || isSelf(user)"
                  :title="isSelf(user) ? '不能操作自己的账号' : user.is_active ? '点击停用' : '点击启用'"
                  @click="toggleActive(user)"
                ><i />{{ user.is_active ? '已启用' : '已停用' }}</button>
              </td>
              <td class="muted-text">{{ user.last_login_at || '从未登录' }}</td>
              <td class="muted-text">{{ user.created_at }}</td>
              <td class="action-buttons">
                <button
                  type="button" class="icon-button"
                  :disabled="isSelf(user)" :title="isSelf(user) ? '不能编辑自己的账号' : '编辑用户信息'"
                  @click="openEdit(user)"
                ><Edit3 :size="16" /></button>
                <button
                  type="button" class="icon-button"
                  :disabled="isSelf(user)" :title="isSelf(user) ? '不能重置自己的密码' : '重置登录密码'"
                  @click="openReset(user)"
                ><KeyRound :size="16" /></button>
                <button
                  type="button" class="icon-button danger-hover"
                  :disabled="isSelf(user)" :title="isSelf(user) ? '不能删除自己的账号' : '删除用户'"
                  @click="askDelete(user)"
                ><Trash2 :size="16" /></button>
              </td>
            </tr>
          </tbody>
        </table>
        <EmptyState v-if="!loading && !result.items.length" text="没有匹配的用户" />
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

    <BaseModal :open="createOpen" title="新建用户" @close="createOpen = false">
      <form class="rule-form" @submit.prevent="createUser">
        <div class="form-grid">
          <label><span class="field-label">用户名<span class="required">*</span></span><input v-model.trim="createForm.username" maxlength="30" minlength="3" required autocomplete="username" placeholder="3-30 位字母、数字或下划线" /></label>
          <label><span class="field-label">昵称</span><input v-model.trim="createForm.nickname" maxlength="50" placeholder="留空则使用用户名" /></label>
        </div>
        <label><span class="field-label">邮箱</span><input v-model="createForm.email" type="email" maxlength="255" autocomplete="email" placeholder="可选，不填写则不绑定邮箱" /></label>
        <label><span class="field-label">登录密码<span class="required">*</span></span><PasswordInput v-model="createForm.password" :minlength="8" :maxlength="128" required autocomplete="new-password" placeholder="至少 8 位，包含字母和数字" /></label>
        <small class="form-hint">创建后用户可直接使用用户名和密码登录，角色固定为普通用户。</small>
        <div class="modal-form-actions">
          <button type="button" class="button secondary" :disabled="saving" @click="createOpen = false">取消</button>
          <button type="submit" class="button primary" :disabled="saving"><span v-if="saving" class="button-spinner" />创建用户</button>
        </div>
      </form>
    </BaseModal>

    <BaseModal :open="editOpen" :title="`编辑用户 · ${editForm.username}`" @close="editOpen = false">
      <form class="rule-form" @submit.prevent="saveEdit">
        <div class="form-grid">
          <label><span class="field-label">昵称<span class="required">*</span></span><input v-model.trim="editForm.nickname" maxlength="50" required placeholder="1-50 个字符" /></label>
          <label><span class="field-label">角色</span>
            <select v-model="editForm.role_code" title="选择用户角色">
              <option v-for="item in roleOptions" :key="item.value" :value="item.value">{{ item.label }}</option>
            </select>
          </label>
        </div>
        <label><span class="field-label">邮箱</span><input v-model="editForm.email" type="email" maxlength="255" autocomplete="email" placeholder="留空表示解绑邮箱" /></label>
        <label class="checkbox-field"><input v-model="editForm.is_active" type="checkbox" /><span>启用该账号（停用后立即退出登录且无法再登录）</span></label>
        <small class="form-hint">修改邮箱后需要用户重新验证；系统至少保留一个可用管理员。</small>
        <div class="modal-form-actions">
          <button type="button" class="button secondary" :disabled="saving" @click="editOpen = false">取消</button>
          <button type="submit" class="button primary" :disabled="saving"><span v-if="saving" class="button-spinner" />保存修改</button>
        </div>
      </form>
    </BaseModal>

    <BaseModal :open="resetOpen" :title="`重置密码 · ${resetForm.username}`" @close="resetOpen = false">
      <form class="rule-form" @submit.prevent="submitReset">
        <label><span class="field-label">新密码<span class="required">*</span></span><PasswordInput v-model="resetForm.newPassword" :minlength="8" :maxlength="128" required autocomplete="new-password" placeholder="至少 8 位，包含字母和数字" /></label>
        <label><span class="field-label">确认新密码<span class="required">*</span></span><PasswordInput v-model="resetForm.confirmPassword" :minlength="8" :maxlength="128" required autocomplete="new-password" placeholder="再次输入新密码" /></label>
        <small class="form-hint">重置后该用户已有的登录会话会全部失效，需要重新登录。</small>
        <div class="modal-form-actions">
          <button type="button" class="button secondary" :disabled="saving" @click="resetOpen = false">取消</button>
          <button type="submit" class="button primary" :disabled="saving"><span v-if="saving" class="button-spinner" />确认重置</button>
        </div>
      </form>
    </BaseModal>

    <ConfirmDialog
      :open="deleteOpen"
      title="删除用户"
      :message="deleteMessage"
      :loading="saving"
      confirm-text="确认删除"
      @close="deleteOpen = false; deleteTarget = null"
      @confirm="confirmDelete"
    />
  </div>
</template>

<style scoped>
.self-chip {
  margin-left: 6px;
  font-size: 11px;
}
.checkbox-field {
  display: flex;
  align-items: center;
  gap: 8px;
}
.checkbox-field input {
  width: 16px;
  height: 16px;
  flex: none;
}
</style>
