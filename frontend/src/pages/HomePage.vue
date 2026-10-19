<script setup lang="ts">
/**
 * 插件库公开首页。
 *
 * 无需登录即可浏览已上架插件，布局参照 MaiBot 插件市场：
 * 顶部导航 + 搜索 Hero + 插件卡片网格 + 详情弹窗。
 * 已登录用户可以从这里直接进入控制台。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  ArrowRight,
  Boxes,
  ChevronLeft,
  ChevronRight,
  Download,
  ExternalLink,
  Flame,
  Github,
  Layers,
  LogIn,
  Moon,
  Package,
  Search,
  ShieldCheck,
  Sparkles,
  Sun,
  Users,
} from 'lucide-vue-next'
import BaseModal from '../components/common/BaseModal.vue'
import EmptyState from '../components/common/EmptyState.vue'
import { registryApi } from '../api'
import type { Pagination, RegistryPlugin, RegistryStats } from '../types'
import { useAuth } from '../composables/useAuth'
import { useTheme } from '../composables/useTheme'

const router = useRouter()
const auth = useAuth()
const theme = useTheme()

const keyword = ref('')
const sort = ref<'latest' | 'popular' | 'name'>('latest')
const page = ref(1)
const loading = ref(false)
const plugins = ref<Pagination<RegistryPlugin>>({
  items: [], page: 1, page_size: 12, total: 0, pages: 1,
})
const stats = ref<RegistryStats>({
  plugin_count: 0, install_count: 0, author_count: 0, updated_at: '',
})
const detail = ref<RegistryPlugin | null>(null)
const detailOpen = ref(false)
const detailLoading = ref(false)

const loggedIn = computed(() => Boolean(auth.state.user))
const sortOptions = [
  { value: 'latest' as const, label: '最近更新', icon: Sparkles },
  { value: 'popular' as const, label: '安装最多', icon: Flame },
  { value: 'name' as const, label: '名称排序', icon: Layers },
]

/** 能力码转可读文案，能力中文名由后台字典维护，这里只做兜底美化。 */
function capabilityLabel(code: string): string {
  const map: Record<string, string> = {
    send_message: '发送消息',
    http_request: '网络请求',
    storage: '本地存储',
    cache: '缓存读写',
    scheduler: '定时任务',
    llm: 'AI 调用',
    database: '数据库',
  }
  return map[code] || code
}

/** 图标只接受 http(s) 直链，其余按首字母色块渲染，避免相对路径 404。 */
function iconSource(plugin: RegistryPlugin): string {
  const value = (plugin.icon_url || '').trim()
  return /^https?:\/\//i.test(value) ? value : ''
}

function initial(plugin: RegistryPlugin): string {
  return (plugin.name || plugin.plugin_id || '?').slice(0, 1).toUpperCase()
}

/** 按插件标识稳定取一个色相，保证同一插件每次刷新颜色一致。 */
function accentStyle(plugin: RegistryPlugin) {
  let hash = 0
  for (const ch of plugin.plugin_id) hash = (hash * 31 + ch.charCodeAt(0)) % 360
  return {
    background: `linear-gradient(135deg, hsl(${hash} 82% 58%), hsl(${(hash + 38) % 360} 76% 46%))`,
  }
}

function formatDate(value: string): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' })
}

async function loadPlugins() {
  loading.value = true
  const result = await registryApi.plugins({
    page: page.value,
    page_size: 12,
    keyword: keyword.value.trim(),
    sort: sort.value,
  })
  loading.value = false
  if (result.success && result.data) plugins.value = result.data
}

async function loadStats() {
  const result = await registryApi.stats()
  if (result.success && result.data) stats.value = result.data
}

async function openDetail(plugin: RegistryPlugin) {
  detail.value = plugin
  detailOpen.value = true
  detailLoading.value = true
  const result = await registryApi.detail(plugin.plugin_id)
  detailLoading.value = false
  if (result.success && result.data) detail.value = result.data
}

function changeSort(value: 'latest' | 'popular' | 'name') {
  if (sort.value === value) return
  sort.value = value
  page.value = 1
  loadPlugins()
}

function goPage(next: number) {
  const target = Math.min(Math.max(1, next), plugins.value.pages)
  if (target === page.value) return
  page.value = target
  loadPlugins()
  window.scrollTo({ top: 320, behavior: 'smooth' })
}

function enterConsole() {
  router.push(loggedIn.value ? '/dashboard' : '/login')
}

// 搜索框输入停止 350ms 后再请求，避免每敲一个字打一次库。
let searchTimer: ReturnType<typeof setTimeout> | undefined
watch(keyword, () => {
  clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    page.value = 1
    loadPlugins()
  }, 350)
})

onMounted(() => {
  auth.initialize()
  loadPlugins()
  loadStats()
})
</script>

<template>
  <div class="home-page">
    <header class="home-nav">
      <div class="home-nav-inner">
        <div class="home-brand">
          <img src="/napcat-logo.png" alt="NapCat 插件库" />
          <div>
            <strong>NapCat 插件库</strong>
            <span>PLUGIN REGISTRY</span>
          </div>
        </div>
        <nav class="home-nav-links">
          <a class="active" href="#plugins">插件市场</a>
          <a href="#about">关于</a>
        </nav>
        <div class="home-nav-actions">
          <button
            type="button"
            class="home-icon-button"
            :title="theme.state.dark ? '切换亮色模式' : '切换暗色模式'"
            @click="theme.toggleDark"
          >
            <Sun v-if="theme.state.dark" :size="18" />
            <Moon v-else :size="18" />
          </button>
          <button type="button" class="home-cta" @click="enterConsole">
            <LogIn v-if="!loggedIn" :size="16" />
            <Boxes v-else :size="16" />
            {{ loggedIn ? '进入控制台' : '登录 / 注册' }}
          </button>
        </div>
      </div>
    </header>

    <section class="home-hero">
      <div class="home-hero-glow" aria-hidden="true" />
      <div class="home-hero-inner">
        <span class="home-badge"><ShieldCheck :size="13" /> 审核上架 · 权限可控</span>
        <h1>发现并安装 <span>NapCat 插件</span></h1>
        <p>
          这里收录经过审核的 NapCat 扩展插件，查看能力声明与版本记录后再安装，
          让机器人按你的需要长出功能。
        </p>
        <div class="home-search">
          <Search :size="18" />
          <input
            v-model="keyword"
            type="search"
            placeholder="搜索插件名称、标识、作者或能力"
            aria-label="搜索插件"
          />
          <kbd v-if="!keyword">Enter</kbd>
        </div>
        <div class="home-stats">
          <div><strong>{{ stats.plugin_count }}</strong><span>上架插件</span></div>
          <i />
          <div><strong>{{ stats.install_count }}</strong><span>累计安装</span></div>
          <i />
          <div><strong>{{ stats.author_count }}</strong><span>插件作者</span></div>
        </div>
      </div>
    </section>

    <main id="plugins" class="home-main">
      <div class="home-toolbar">
        <div class="home-tabs">
          <button
            v-for="item in sortOptions"
            :key="item.value"
            type="button"
            :class="{ active: sort === item.value }"
            @click="changeSort(item.value)"
          >
            <component :is="item.icon" :size="15" />{{ item.label }}
          </button>
        </div>
        <span class="home-count">共 {{ plugins.total }} 个插件</span>
      </div>

      <div v-if="loading" class="home-skeleton-grid">
        <div v-for="index in 6" :key="index" class="home-skeleton" />
      </div>

      <div v-else-if="plugins.items.length" class="home-grid">
        <article
          v-for="plugin in plugins.items"
          :key="plugin.plugin_id"
          class="home-card"
          tabindex="0"
          role="button"
          @click="openDetail(plugin)"
          @keydown.enter="openDetail(plugin)"
        >
          <div class="home-card-head">
            <div class="home-card-icon" :style="iconSource(plugin) ? undefined : accentStyle(plugin)">
              <img v-if="iconSource(plugin)" :src="iconSource(plugin)" :alt="plugin.name" />
              <span v-else>{{ initial(plugin) }}</span>
            </div>
            <div class="home-card-title">
              <h3>{{ plugin.name }}</h3>
              <code>{{ plugin.plugin_id }}</code>
            </div>
            <span class="home-version">v{{ plugin.version }}</span>
          </div>

          <p class="home-card-desc">{{ plugin.summary || plugin.description || '作者尚未提供简介' }}</p>

          <div class="home-card-caps">
            <span v-for="cap in plugin.capabilities.slice(0, 3)" :key="cap" class="home-cap">
              {{ capabilityLabel(cap) }}
            </span>
            <span v-if="plugin.capabilities.length > 3" class="home-cap more">
              +{{ plugin.capabilities.length - 3 }}
            </span>
            <span v-if="!plugin.capabilities.length" class="home-cap muted">无特殊权限</span>
          </div>

          <footer class="home-card-foot">
            <span class="home-card-author">
              <Users :size="13" />{{ plugin.author || '未知作者' }}
            </span>
            <span class="home-card-installs">
              <Download :size="13" />{{ plugin.install_count }} 次安装
            </span>
          </footer>
        </article>
      </div>

      <EmptyState v-else text="没有匹配的插件，换个关键词试试" />

      <div v-if="plugins.pages > 1" class="home-pager">
        <button type="button" :disabled="page <= 1" @click="goPage(page - 1)">
          <ChevronLeft :size="16" />上一页
        </button>
        <span>第 {{ plugins.page }} / {{ plugins.pages }} 页</span>
        <button type="button" :disabled="page >= plugins.pages" @click="goPage(page + 1)">
          下一页<ChevronRight :size="16" />
        </button>
      </div>

      <section id="about" class="home-about">
        <div class="home-about-head">
          <div>
            <h2>插件如何进入这里</h2>
            <p>
              平台只收录审核过的扩展。作者提交一次之后，仓库打新 tag 就会自动收录新版本，
              不用反复上传。每个插件都公开版本号和权限范围，装之前就知道它会动什么。
            </p>
          </div>
          <button type="button" class="home-cta ghost" @click="enterConsole">
            提交插件<ArrowRight :size="16" />
          </button>
        </div>
        <ol class="home-steps">
          <li>
            <span class="home-step-index">1</span>
            <div>
              <strong>提交来源</strong>
              <p>提交 GitHub 仓库的 tag，或直接上传压缩包；清单里写清版本号与申请的能力。</p>
            </div>
          </li>
          <li>
            <span class="home-step-index">2</span>
            <div>
              <strong>审核上架</strong>
              <p>首次提交由管理员核对能力声明后上架，高风险权限需要逐项确认。</p>
            </div>
          </li>
          <li>
            <span class="home-step-index">3</span>
            <div>
              <strong>自动更新</strong>
              <p>之后每次发版自动同步入库，市场里直接显示新版本，安装方按需升级。</p>
            </div>
          </li>
        </ol>
      </section>
    </main>

    <footer class="home-footer">
      <span>NapCat 插件库 · 插件分发与审核平台</span>
      <span>索引地址 <code>/api/registry/index.json</code></span>
    </footer>

    <BaseModal :open="detailOpen" :title="detail?.name || '插件详情'" width="620px" @close="detailOpen = false">
      <div v-if="detail" class="home-detail">
        <div class="home-detail-head">
          <div class="home-card-icon" :style="iconSource(detail) ? undefined : accentStyle(detail)">
            <img v-if="iconSource(detail)" :src="iconSource(detail)" :alt="detail.name" />
            <span v-else>{{ initial(detail) }}</span>
          </div>
          <div>
            <h3>{{ detail.name }} <span class="home-version">v{{ detail.version }}</span></h3>
            <code>{{ detail.plugin_id }}</code>
            <p>{{ detail.summary || '作者尚未提供简介' }}</p>
          </div>
        </div>

        <dl class="home-detail-meta">
          <div><dt>作者</dt><dd>{{ detail.author || '未知作者' }}</dd></div>
          <div><dt>来源</dt><dd>{{ detail.source_type === 'repo' ? '代码仓库' : '压缩包上传' }}</dd></div>
          <div><dt>许可证</dt><dd>{{ detail.license || '未声明' }}</dd></div>
          <div><dt>安装次数</dt><dd>{{ detail.install_count }}</dd></div>
          <div><dt>更新时间</dt><dd>{{ formatDate(detail.published_at) }}</dd></div>
          <div><dt>宿主版本</dt><dd>{{ detail.min_host_version || '不限' }}</dd></div>
        </dl>

        <div class="home-detail-section">
          <h4>权限范围</h4>
          <div class="home-card-caps">
            <span v-for="cap in detail.capabilities" :key="cap" class="home-cap">
              {{ capabilityLabel(cap) }}
            </span>
            <span v-if="!detail.capabilities.length" class="home-cap muted">无特殊权限</span>
          </div>
        </div>

        <div v-if="detail.description" class="home-detail-section">
          <h4>插件说明</h4>
          <p class="home-detail-desc">{{ detail.description }}</p>
        </div>

        <div v-if="detail.changelog" class="home-detail-section">
          <h4>更新内容</h4>
          <p class="home-detail-desc">{{ detail.changelog }}</p>
        </div>

        <div class="home-detail-links">
          <a v-if="detail.repo_url" :href="detail.repo_url" target="_blank" rel="noreferrer">
            <Github :size="15" />查看仓库
          </a>
          <a v-if="detail.homepage" :href="detail.homepage" target="_blank" rel="noreferrer">
            <ExternalLink :size="15" />项目主页
          </a>
        </div>

        <p class="home-detail-hint">
          在 NapCatQQ Service 的插件市场搜索 <code>{{ detail.plugin_id }}</code> 即可安装，
          安装后默认关闭，需要你在「我的插件」里手动启用。
        </p>
      </div>
      <div v-else-if="detailLoading" class="home-detail-loading">正在加载插件详情…</div>
    </BaseModal>
  </div>
</template>
