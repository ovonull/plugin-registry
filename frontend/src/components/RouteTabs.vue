<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { X } from 'lucide-vue-next'

interface TabItem { name: string; title: string; path: string }

const route = useRoute()
const router = useRouter()
const tabs = ref<TabItem[]>([{ name: 'dashboard', title: '仪表盘', path: '/dashboard' }])

watch(
  () => route.fullPath,
  () => {
    if (!route.name || route.name === 'login') return
    const name = String(route.name)
    if (!tabs.value.some((tab) => tab.name === name)) {
      tabs.value.push({ name, title: String(route.meta.title || name), path: route.fullPath })
    }
  },
  { immediate: true },
)

const currentName = computed(() => String(route.name || ''))

function closeTab(tab: TabItem) {
  if (tab.name === 'dashboard') return
  const index = tabs.value.findIndex((item) => item.name === tab.name)
  tabs.value.splice(index, 1)
  if (currentName.value === tab.name) {
    const target = tabs.value[Math.max(0, index - 1)]
    router.push(target?.path || '/dashboard')
  }
}
</script>

<template>
  <nav class="route-tabs" aria-label="已打开页面">
    <button
      v-for="tab in tabs"
      :key="tab.name"
      type="button"
      class="route-tab"
      :class="{ active: currentName === tab.name }"
      @click="router.push(tab.path)"
    >
      <span>{{ tab.title }}</span>
      <span
        v-if="tab.name !== 'dashboard'"
        class="tab-close"
        title="关闭页面"
        @click.stop="closeTab(tab)"
      ><X :size="13" /></span>
    </button>
  </nav>
</template>

