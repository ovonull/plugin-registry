<script setup lang="ts">
import { computed, ref } from 'vue'
import { History, Mail, TerminalSquare } from 'lucide-vue-next'
import LoginLogPanel from '../components/system-logs/LoginLogPanel.vue'
import EmailLogPanel from '../components/system-logs/EmailLogPanel.vue'
import PluginActionLogPanel from '../components/system-logs/PluginActionLogPanel.vue'

type Tab = 'login' | 'email' | 'plugin_action'

const tabs = [
  { value: 'login' as const, label: '登录日志', icon: History, panel: LoginLogPanel },
  { value: 'email' as const, label: '邮件日志', icon: Mail, panel: EmailLogPanel },
  { value: 'plugin_action' as const, label: '插件动作审计', icon: TerminalSquare, panel: PluginActionLogPanel },
]

const activeTab = ref<Tab>('login')
const activePanel = computed(
  () => tabs.find((tab) => tab.value === activeTab.value)?.panel || LoginLogPanel,
)
</script>

<template>
  <div class="page-enter system-logs-page">
    <div class="page-heading">
      <div>
        <p class="eyebrow">SYSTEM LOGS</p>
        <h1>系统日志</h1>
        <p>集中查看登录、邮件发送与插件动作审计记录，仅管理员可见。</p>
      </div>
    </div>
    <div class="inner-tabs" role="tablist">
      <button
        v-for="tab in tabs"
        :key="tab.value"
        type="button"
        :class="{ active: activeTab === tab.value }"
        @click="activeTab = tab.value"
      >
        <component :is="tab.icon" :size="17" />{{ tab.label }}
      </button>
    </div>

    <!-- KeepAlive 保留各面板的筛选条件与页码，切换 Tab 不重复请求。 -->
    <KeepAlive>
      <component :is="activePanel" />
    </KeepAlive>
  </div>
</template>
