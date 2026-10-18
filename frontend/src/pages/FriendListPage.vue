<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ContactRound, Ban, Filter, MessageSquareText, RefreshCw, RotateCw, Search, ShieldCheck } from 'lucide-vue-next'
import { botApi, chatRecordApi, friendApi, publicApi } from '../api'
import type {
  BotAccount,
  BotFriend,
  ChatMessageRecord,
  DictionaryOption,
  Pagination,
} from '../types'
import BaseModal from '../components/common/BaseModal.vue'
import EmptyState from '../components/common/EmptyState.vue'
import LoadingOverlay from '../components/common/LoadingOverlay.vue'
import { useAuth } from '../composables/useAuth'
import { useToast } from '../composables/useToast'

const auth = useAuth()
const toast = useToast()
const loading = ref(false)
const syncing = ref(false)
const blockingId = ref<number | null>(null)
const searched = ref(false)
const bots = ref<BotAccount[]>([])
const sexOptions = ref<DictionaryOption[]>([])
const syncBotQq = ref('')
const query = reactive({ bot_qq: '', keyword: '', category_name: '', sex: '', page: 1, page_size: 20 })
const result = ref<Pagination<BotFriend>>({ items: [], page: 1, page_size: 20, total: 0, pages: 1 })
const isAdmin = computed(() => auth.state.user?.role_code === 'admin')
const hasFilters = computed(() => Boolean(query.bot_qq || query.keyword || query.category_name || query.sex))

const recordOpen = ref(false)
const recordLoading = ref(false)
const recordSyncing = ref(false)
const recordFriend = ref<BotFriend | null>(null)
const recordQuery = reactive({ page: 1, page_size: 20 })
const recordResult = ref<Pagination<ChatMessageRecord>>({ items: [], page: 1, page_size: 20, total: 0, pages: 1 })
const recordTitle = computed(() => {
  const friend = recordFriend.value
  if (!friend) return '聊天记录'
  const name = friend.remark || friend.nickname || friend.friend_qq
  return `聊天记录 · ${name}（${friend.friend_qq}）`
})

/** 把性别编码转换为数据字典中的中文名称。 */
function sexLabel(code: string) {
  return sexOptions.value.find((item) => item.value === code)?.label || '未知'
}

/** 空值统一显示为占位符。 */
function display(value: string | number | null | undefined) {
  return value === null || value === undefined || value === '' ? '—' : String(value)
}

async function loadBots() {
  const response = await botApi.list()
  if (!response.success) {
    toast.show(response.message, 'error')
    return
  }
  bots.value = response.data.filter((item) => item.is_enabled)
  if (!syncBotQq.value && bots.value.length) syncBotQq.value = bots.value[0].bot_qq
}

async function loadSexOptions() {
  const response = await publicApi.dictionaries(['friend_sex'])
  if (!response.success) {
    toast.show(response.message, 'error')
    return
  }
  sexOptions.value = response.data.friend_sex || []
}

async function loadFriends() {
  loading.value = true
  const response = await friendApi.list({
    page: query.page,
    page_size: query.page_size,
    bot_qq: query.bot_qq || undefined,
    keyword: query.keyword.trim() || undefined,
    category_name: query.category_name.trim() || undefined,
    sex: query.sex || undefined,
  })
  loading.value = false
  searched.value = true
  if (!response.success) {
    toast.show(response.message, 'error')
    return
  }
  result.value = response.data
}

function search() {
  query.page = 1
  void loadFriends()
}

function resetFilters() {
  Object.assign(query, { bot_qq: '', keyword: '', category_name: '', sex: '', page: 1 })
  void loadFriends()
}

function changePage(page: number) {
  if (page < 1 || page > result.value.pages) return
  query.page = page
  void loadFriends()
}

function changePageSize() {
  query.page = 1
  void loadFriends()
}

function askSync() {
  if (!syncBotQq.value) {
    toast.show('请先选择要同步的机器人', 'warning')
    return
  }
  void syncFriends()
}

/** 从 NapCat 拉取所选机器人的全部好友并刷新列表。 */
async function syncFriends() {
  syncing.value = true
  const response = await friendApi.sync(syncBotQq.value)
  syncing.value = false
  if (!response.success) {
    toast.show(response.message, 'error')
    return
  }
  toast.show(response.message, 'success')
  query.bot_qq = syncBotQq.value
  query.page = 1
  await loadFriends()
}

onMounted(() => {
  void loadBots()
  void loadSexOptions()
  void loadFriends()
})

/** 切换好友屏蔽状态：屏蔽后其私聊和群聊消息将被后端直接跳过。 */
async function toggleBlock(friend: BotFriend) {
  blockingId.value = friend.id
  const response = friend.is_blocked
    ? await friendApi.unblock(friend.id)
    : await friendApi.block(friend.id)
  blockingId.value = null
  if (!response.success) {
    toast.show(response.message, 'error')
    return
  }
  toast.show(response.message, 'success')
  friend.is_blocked = response.data.is_blocked
}

/** 打开好友聊天记录弹窗并加载首页数据。 */
function openRecords(friend: BotFriend) {
  recordFriend.value = friend
  recordQuery.page = 1
  recordResult.value = { items: [], page: 1, page_size: recordQuery.page_size, total: 0, pages: 1 }
  recordOpen.value = true
  void loadRecords()
}

function closeRecords() {
  recordOpen.value = false
  recordFriend.value = null
}

/** 查询该好友与机器人的私聊记录，按事件时间倒序。 */
async function loadRecords() {
  const friend = recordFriend.value
  if (!friend) return
  recordLoading.value = true
  const response = await chatRecordApi.messages({
    page: recordQuery.page,
    page_size: recordQuery.page_size,
    bot_qq: friend.bot_qq,
    user_qq: friend.friend_qq,
    private_only: true,
  })
  recordLoading.value = false
  if (!response.success) {
    toast.show(response.message, 'error')
    return
  }
  recordResult.value = response.data
}

function changeRecordPage(page: number) {
  if (page < 1 || page > recordResult.value.pages) return
  recordQuery.page = page
  void loadRecords()
}

function changeRecordPageSize() {
  recordQuery.page = 1
  void loadRecords()
}

/** 从 NapCat 同步该好友的历史聊天记录，重复消息由后端去重。 */
async function syncRecords() {
  const friend = recordFriend.value
  if (!friend) return
  recordSyncing.value = true
  const response = await friendApi.syncMessages(friend.bot_qq, friend.friend_qq)
  recordSyncing.value = false
  if (!response.success) {
    toast.show(response.message, 'error')
    return
  }
  toast.show(response.message, 'success')
  recordQuery.page = 1
  await loadRecords()
}
</script>

<template>
  <div class="page-enter friend-page">
    <div class="page-heading">
      <div>
        <p class="eyebrow">FRIEND LIST</p>
        <h1>好友列表</h1>
        <p>选择机器人后从 NapCat 同步其全部好友，好友资料按所属用户隔离保存。</p>
      </div>
      <div class="friend-sync-bar">
        <label class="friend-sync-select">
          <span>同步机器人</span>
          <select v-model="syncBotQq">
            <option value="" disabled>请选择机器人</option>
            <option v-for="bot in bots" :key="bot.id" :value="bot.bot_qq">{{ bot.nickname || bot.bot_qq }} · {{ bot.bot_qq }}</option>
          </select>
        </label>
        <button type="button" class="button primary" :disabled="syncing || !bots.length" @click="askSync">
          <RotateCw :size="17" :class="{ spin: syncing }" />同步好友
        </button>
      </div>
    </div>

    <section class="filter-panel">
      <div class="filter-title"><Filter :size="17" /><strong>筛选条件</strong></div>
      <div class="filter-fields">
        <label>机器人 QQ<select v-model="query.bot_qq"><option value="">全部机器人</option><option v-for="bot in bots" :key="bot.id" :value="bot.bot_qq">{{ bot.nickname || bot.bot_qq }} · {{ bot.bot_qq }}</option></select></label>
        <label>好友关键字<input v-model="query.keyword" placeholder="QQ 号、昵称或备注" @keyup.enter="search" /></label>
        <label>好友分组<input v-model="query.category_name" placeholder="留空显示全部" @keyup.enter="search" /></label>
        <label>性别<select v-model="query.sex"><option value="">全部性别</option><option v-for="item in sexOptions" :key="item.value" :value="item.value">{{ item.label }}</option></select></label>
        <div class="filter-actions">
          <button type="button" class="button primary" @click="search"><Search :size="16" />查询</button>
          <button v-if="hasFilters" type="button" class="button ghost" title="重置筛选" @click="resetFilters"><RefreshCw :size="16" />重置</button>
        </div>
      </div>
    </section>

    <section class="content-panel rules-panel friend-panel">
      <div class="panel-heading">
        <div><h2><ContactRound :size="17" />好友列表</h2><p>共 {{ result.total }} 位好友<span v-if="isAdmin"> · 管理员视图</span></p></div>
        <button type="button" class="icon-button" title="刷新列表" :disabled="loading" @click="loadFriends"><RefreshCw :size="18" :class="{ spin: loading }" /></button>
      </div>

      <div class="table-scroll friend-table">
        <table>
          <thead>
            <tr>
              <th>好友 QQ</th><th>昵称</th><th>备注</th><th>分组</th><th>性别</th><th>QQ 等级</th>
              <th>个性签名</th><th>生日</th><th>机器人 QQ</th><th v-if="isAdmin">所属用户</th><th>同步时间</th>
              <th class="action-column">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="friend in result.items" :key="friend.id">
              <td class="mono-text">{{ friend.friend_qq }}</td>
              <td>{{ display(friend.nickname) }}</td>
              <td>{{ display(friend.remark) }}</td>
              <td><span class="keyword-chip">{{ display(friend.category_name) }}</span></td>
              <td>{{ sexLabel(friend.sex) }}</td>
              <td class="mono-text">{{ friend.level || '—' }}</td>
              <td class="record-content" :title="friend.long_nick || undefined">{{ display(friend.long_nick) }}</td>
              <td class="muted-text nowrap">{{ display(friend.birthday) }}</td>
              <td class="mono-text">{{ friend.bot_qq }}</td>
              <td v-if="isAdmin" class="muted-text">{{ display(friend.owner_username) }}</td>
              <td class="muted-text nowrap">{{ display(friend.synced_at) }}</td>
              <td class="action-buttons">
                <button type="button" class="icon-button" title="查看聊天记录" @click="openRecords(friend)"><MessageSquareText :size="16" /></button>
                <button
                  type="button"
                  class="icon-button"
                  :class="friend.is_blocked ? 'is-blocked' : 'danger-hover'"
                  :title="friend.is_blocked ? '取消屏蔽' : '屏蔽该好友'"
                  :disabled="blockingId === friend.id"
                  @click="toggleBlock(friend)"
                >
                  <ShieldCheck v-if="friend.is_blocked" :size="16" />
                  <Ban v-else :size="16" />
                </button>
              </td>
            </tr>
          </tbody>
        </table>
        <EmptyState
          v-if="!loading && !result.items.length"
          :text="hasFilters && searched ? '没有符合筛选条件的好友' : '还没有同步好友，请先选择机器人并同步'"
        />
      </div>

      <div class="pagination">
        <span>第 {{ result.page }} / {{ result.pages }} 页，共 {{ result.total }} 条</span>
        <div>
          <select v-model.number="query.page_size" title="每页条数" @change="changePageSize">
            <option :value="10">10 条/页</option><option :value="20">20 条/页</option>
            <option :value="50">50 条/页</option><option :value="100">100 条/页</option>
          </select>
          <button type="button" class="page-button" :disabled="result.page <= 1" @click="changePage(result.page - 1)">上一页</button>
          <button type="button" class="page-button" :disabled="result.page >= result.pages" @click="changePage(result.page + 1)">下一页</button>
        </div>
      </div>
    </section>
    <LoadingOverlay :visible="loading" contained />

    <BaseModal :open="recordOpen" :title="recordTitle" width="1000px" @close="closeRecords">
      <div class="record-modal">
        <div class="record-modal-toolbar">
          <div>
            <strong>私聊记录</strong>
            <span>共 {{ recordResult.total }} 条 · 按事件时间倒序</span>
          </div>
          <div class="record-modal-actions">
            <button type="button" class="button primary" :disabled="recordSyncing" @click="syncRecords">
              <RotateCw :size="16" :class="{ spin: recordSyncing }" />同步聊天记录
            </button>
            <button type="button" class="icon-button" title="刷新记录" :disabled="recordLoading" @click="loadRecords">
              <RefreshCw :size="17" :class="{ spin: recordLoading }" />
            </button>
          </div>
        </div>

        <div class="table-scroll record-modal-table">
          <table>
            <thead><tr><th>事件时间</th><th>方向</th><th>发送者</th><th>消息内容</th><th>消息 ID</th><th>来源</th></tr></thead>
            <tbody>
              <tr v-for="item in recordResult.items" :key="item.id">
                <td class="muted-text nowrap">{{ display(item.event_time) }}</td>
                <td><span class="direction-chip" :class="{ bot: item.direction === '机器人发送' }">{{ item.direction }}</span></td>
                <td>{{ display(item.sender_nickname) }}<small v-if="item.sender_user_id" class="record-subtext">{{ item.sender_user_id }}</small></td>
                <td class="record-content" :title="item.message_text || undefined">{{ display(item.message_text) }}</td>
                <td class="mono-text">{{ display(item.message_id) }}</td>
                <td class="muted-text">{{ display(item.source) }}</td>
              </tr>
            </tbody>
          </table>
          <EmptyState
            v-if="!recordLoading && !recordResult.items.length"
            text="还没有聊天记录，可点击“同步聊天记录”从 NapCat 拉取"
          />
        </div>

        <div class="pagination">
          <span>第 {{ recordResult.page }} / {{ recordResult.pages }} 页，共 {{ recordResult.total }} 条</span>
          <div>
            <select v-model.number="recordQuery.page_size" title="每页条数" @change="changeRecordPageSize">
              <option :value="10">10 条/页</option><option :value="20">20 条/页</option>
              <option :value="50">50 条/页</option><option :value="100">100 条/页</option>
            </select>
            <button type="button" class="page-button" :disabled="recordResult.page <= 1" @click="changeRecordPage(recordResult.page - 1)">上一页</button>
            <button type="button" class="page-button" :disabled="recordResult.page >= recordResult.pages" @click="changeRecordPage(recordResult.page + 1)">下一页</button>
          </div>
        </div>
        <LoadingOverlay :visible="recordLoading || recordSyncing" contained />
      </div>
      <template #footer>
        <button type="button" class="button secondary" @click="closeRecords">关闭</button>
      </template>
    </BaseModal>
  </div>
</template>
