<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ArrowRight, Boxes, CircleCheck, Package, RefreshCw, ShieldCheck, UploadCloud } from 'lucide-vue-next'
import { useRouter } from 'vue-router'
import { pluginApi } from '../api'
import type { Pagination, PluginInstallation, PluginItem } from '../types'
import EmptyState from '../components/common/EmptyState.vue'
import LoadingOverlay from '../components/common/LoadingOverlay.vue'
import { useAuth } from '../composables/useAuth'
import { useToast } from '../composables/useToast'

const auth = useAuth()
const toast = useToast()
const router = useRouter()
const loading = ref(false)
const plugins = ref<Pagination<PluginItem>>({ items: [], page: 1, page_size: 8, total: 0, pages: 1 })
const installations = ref<Pagination<PluginInstallation>>({ items: [], page: 1, page_size: 100, total: 0, pages: 1 })
const enabledCount = computed(() => installations.value.items.filter((item) => item.is_enabled).length)
const pendingCount = computed(() => installations.value.items.filter((item) => item.has_update).length)

async function loadOverview() {
  loading.value = true
  const [pluginResult, installResult] = await Promise.all([
    pluginApi.list({ page: 1, page_size: 8, published: true }),
    pluginApi.installations({ page: 1, page_size: 100, mine: true }),
  ])
  loading.value = false
  if (!pluginResult.success) toast.show(pluginResult.message, 'error')
  else plugins.value = pluginResult.data
  if (!installResult.success) toast.show(installResult.message, 'error')
  else installations.value = installResult.data
}

function openMarket() { router.push('/plugins') }
function openInstalled() { router.push('/plugins/installed') }

onMounted(loadOverview)
</script>

<template>
  <div class="page-enter dashboard-page registry-dashboard">
    <div class="page-heading">
      <div><p class="eyebrow">PLUGIN REGISTRY</p><h1>插件库概览</h1><p>你好，{{ auth.state.user?.nickname }}。发现、提交并管理 NapCat 扩展插件。</p></div>
      <button type="button" class="button secondary" @click="loadOverview"><RefreshCw :size="17" />刷新数据</button>
    </div>

    <div class="stat-grid">
      <button type="button" class="stat-item stat-blue registry-stat" @click="openMarket"><div class="stat-icon"><Package :size="20" /></div><div><strong>{{ plugins.total }}</strong><span>上架插件</span></div></button>
      <button type="button" class="stat-item stat-green registry-stat" @click="openInstalled"><div class="stat-icon"><Boxes :size="20" /></div><div><strong>{{ installations.total }}</strong><span>我的安装</span></div></button>
      <button type="button" class="stat-item stat-violet registry-stat" @click="openInstalled"><div class="stat-icon"><CircleCheck :size="20" /></div><div><strong>{{ enabledCount }}</strong><span>正在启用</span></div></button>
      <button type="button" class="stat-item stat-amber registry-stat" @click="openInstalled"><div class="stat-icon"><UploadCloud :size="20" /></div><div><strong>{{ pendingCount }}</strong><span>可升级插件</span></div></button>
    </div>

    <section class="content-panel registry-featured">
      <div class="panel-heading"><div><h2><Package :size="18" />发现插件</h2><p>浏览已通过审核并上架的扩展，按需安装和配置。</p></div><button type="button" class="button ghost small" @click="openMarket">打开插件市场<ArrowRight :size="15" /></button></div>
      <div v-if="plugins.items.length" class="registry-plugin-grid">
        <article v-for="plugin in plugins.items" :key="plugin.plugin_id" class="registry-plugin-card">
          <div class="registry-plugin-icon"><img v-if="plugin.icon_path" :src="plugin.icon_path" :alt="plugin.name" /><Package v-else :size="21" /></div>
          <div class="registry-plugin-copy"><strong>{{ plugin.name }}</strong><code>{{ plugin.plugin_id }}</code><p>{{ plugin.summary || plugin.description || '作者尚未提供简介' }}</p></div>
          <div class="registry-plugin-meta"><span>v{{ plugin.current_version?.version || '—' }}</span><span>{{ plugin.author_name || '未知作者' }}</span></div>
          <button type="button" class="text-link" @click="router.push('/plugins')">查看详情 <ArrowRight :size="14" /></button>
        </article>
      </div>
      <EmptyState v-else text="市场中还没有上架插件，提交第一个插件或稍后再来看看" />
    </section>

    <section class="registry-next-steps">
      <article><div class="registry-step-icon"><Package :size="20" /></div><div><h3>探索插件市场</h3><p>搜索插件、查看能力声明和版本历史，了解审核批准的权限范围。</p><button type="button" class="text-link" @click="openMarket">浏览插件市场 <ArrowRight :size="14" /></button></div></article>
      <article><div class="registry-step-icon"><Boxes :size="20" /></div><div><h3>管理我的插件</h3><p>调整安装配置、启停插件、分配运行机器人并检查版本升级。</p><button type="button" class="text-link" @click="openInstalled">打开我的插件 <ArrowRight :size="14" /></button></div></article>
      <article v-if="auth.state.user?.role_code === 'admin'"><div class="registry-step-icon"><ShieldCheck :size="20" /></div><div><h3>维护平台质量</h3><p>审核插件版本和权限请求，控制上架版本并管理作者信任名单。</p><button type="button" class="text-link" @click="router.push('/plugin-review')">进入审核台 <ArrowRight :size="14" /></button></div></article>
    </section>
    <LoadingOverlay :visible="loading" contained />
  </div>
</template>
