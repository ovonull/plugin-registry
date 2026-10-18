<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ListChecks, RefreshCw, Sparkles } from 'lucide-vue-next'
import { featuresApi } from '../api'
import type { FeatureItem } from '../types'
import EmptyState from '../components/common/EmptyState.vue'
import LoadingOverlay from '../components/common/LoadingOverlay.vue'
import { useToast } from '../composables/useToast'

const toast = useToast()
const loading = ref(false)
const features = ref<FeatureItem[]>([])

async function loadFeatures() {
  loading.value = true
  const response = await featuresApi.list()
  loading.value = false
  if (!response.success) {
    toast.show(response.message, 'error')
    return
  }
  features.value = response.data
}

onMounted(loadFeatures)
</script>

<template>
  <div class="page-enter feature-list-page">
    <div class="page-heading">
      <div>
        <p class="eyebrow">SYSTEM FEATURES</p>
        <h1>功能列表</h1>
        <p>查看当前系统已启用的功能和触发方式。</p>
      </div>
      <button type="button" class="button secondary" title="刷新功能列表" :disabled="loading" @click="loadFeatures">
        <RefreshCw :size="17" :class="{ spin: loading }" />刷新
      </button>
    </div>

    <section class="content-panel feature-panel">
      <div class="panel-heading">
        <div>
          <h2><ListChecks :size="17" />系统功能</h2>
          <p>共 {{ features.length }} 项功能</p>
        </div>
        <Sparkles class="panel-heading-icon" :size="19" />
      </div>
      <div class="table-scroll feature-table">
        <table>
          <thead>
            <tr><th>功能名称</th><th>功能说明</th><th>触发方式</th><th>分类</th><th>状态</th></tr>
          </thead>
          <tbody>
            <tr v-for="item in features" :key="item.id">
              <td><strong>{{ item.name }}</strong></td>
              <td class="feature-description">{{ item.description }}</td>
              <td><code class="feature-trigger">{{ item.trigger_keyword }}</code></td>
              <td><span class="feature-category">{{ item.category }}</span></td>
              <td><span class="status-badge enabled"><i />已启用</span></td>
            </tr>
          </tbody>
        </table>
        <EmptyState v-if="!loading && !features.length" text="暂无已启用功能" />
      </div>
    </section>
    <LoadingOverlay :visible="loading" contained />
  </div>
</template>
