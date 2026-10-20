<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  Bot,
  BrainCircuit,
  ChevronDown,
  Clock3,
  ChevronLeft,
  ContactRound,
  History,
  Home,
  LayoutDashboard,
  ListChecks,
  LogOut,
  Mail,
  Menu,
  Mic2,
  MessageSquareReply,
  MessagesSquare,
  Package,
  PartyPopper,
  Plug,
  Moon,
  Palette,
  PanelLeftClose,
  PanelLeftOpen,
  Settings,
  ShieldAlert,
  ShieldCheck,
  Sun,
  Sticker,
  TerminalSquare,
  UserRound,
  UsersRound,
  X,
  KeyRound,
} from 'lucide-vue-next'
import { authApi } from '../api'
import { useAuth } from '../composables/useAuth'
import { useTheme } from '../composables/useTheme'
import { useToast } from '../composables/useToast'
import { useSystemSettings } from '../composables/useSystemSettings'
import { appRoutes } from '../router'
import RouteTabs from '../components/RouteTabs.vue'
import ThemePanel from '../components/ThemePanel.vue'

const route = useRoute()
const router = useRouter()
const auth = useAuth()
const theme = useTheme()
const toast = useToast()
const system = useSystemSettings()
const collapsed = ref(false)
const mobileOpen = ref(false)
const userMenuOpen = ref(false)
const themeOpen = ref(false)

const iconMap = {
  home: Home,
  history: History,
  mail: Mail,
  dashboard: LayoutDashboard,
  message: MessageSquareReply,
  welcome: PartyPopper,
  bot: Bot,
  terminal: TerminalSquare,
  user: UserRound,
  users: UsersRound,
  settings: Settings,
  'shield-alert': ShieldAlert,
  'ai-keywords': KeyRound,
  'list-checks': ListChecks,
  brain: BrainCircuit,
  messages: MessagesSquare,
  friends: ContactRound,
  'ai-records': BrainCircuit,
  mic: Mic2,
  clock: Clock3,
  sticker: Sticker,
  package: Package,
  plug: Plug,
  'shield-check': ShieldCheck,
}
interface MenuItem {
  path: string
  name: string
  meta: { title: string; icon: string }
}

// 「首页」指向公开插件展示页（独立布局），所以不进 appRoutes。
// 放在菜单最后一项：日常操作都在后台页面，回首页是低频动作。
const homeMenuItem: MenuItem = {
  path: '/',
  name: 'home',
  meta: { title: '首页', icon: 'home' },
}

const menus = computed<MenuItem[]>(() => [
  ...appRoutes
    .filter((item) => item.meta?.menu && (!item.meta.admin || auth.state.user?.role_code === 'admin'))
    .map((item) => ({
      path: item.path,
      name: String(item.name),
      meta: { title: String(item.meta?.title || ''), icon: String(item.meta?.icon || '') },
    })),
  homeMenuItem,
])
const pageTitle = computed(() => String(route.meta.title || '管理后台'))

async function logout() {
  userMenuOpen.value = false
  await authApi.logout()
  auth.clearSession()
  toast.show('已退出登录', 'success')
  router.replace('/login')
}

function navigate(path: string) {
  mobileOpen.value = false
  router.push(path)
}

system.loadPublic()
</script>

<template>
  <div class="app-shell" :class="{ 'sidebar-collapsed': collapsed }">
    <div v-if="mobileOpen" class="drawer-overlay" @click="mobileOpen = false" />
    <aside class="sidebar" :class="{ 'mobile-open': mobileOpen }">
      <div class="sidebar-brand">
        <img src="/napcat-logo.png" alt="PlugHub 插件库" />
        <div v-if="!collapsed" class="brand-copy">
          <strong>PlugHub 插件库</strong>
          <span>PLUGHUB</span>
        </div>
        <button class="mobile-close icon-button" type="button" title="关闭导航" @click="mobileOpen = false">
          <X :size="19" />
        </button>
      </div>
      <nav class="sidebar-nav" aria-label="主导航">
        <button
          v-for="item in menus"
          :key="String(item.name)"
          type="button"
          class="nav-item"
          :class="{ active: route.name === item.name }"
          :title="collapsed ? String(item.meta?.title) : undefined"
          @click="navigate(item.path)"
        >
          <component :is="iconMap[String(item.meta?.icon) as keyof typeof iconMap]" :size="19" />
          <span v-if="!collapsed">{{ item.meta?.title }}</span>
        </button>
      </nav>
      <div class="sidebar-footer">
        <button type="button" class="collapse-button" @click="collapsed = !collapsed">
          <PanelLeftOpen v-if="collapsed" :size="18" />
          <PanelLeftClose v-else :size="18" />
          <span v-if="!collapsed">收起导航</span>
        </button>
      </div>
    </aside>

    <section class="main-area">
      <header class="topbar">
        <div class="topbar-left">
          <button class="mobile-menu icon-button" type="button" title="打开导航" @click="mobileOpen = true">
            <Menu :size="20" />
          </button>
          <div class="breadcrumb">
            <span>插件库平台</span>
            <ChevronLeft :size="14" class="breadcrumb-divider" />
            <strong>{{ pageTitle }}</strong>
          </div>
        </div>
        <div class="topbar-actions">
          <button class="icon-button" type="button" title="界面设置" @click="themeOpen = true">
            <Palette :size="19" />
          </button>
          <button class="icon-button" type="button" :title="theme.state.dark ? '切换亮色模式' : '切换暗色模式'" @click="theme.toggleDark">
            <Sun v-if="theme.state.dark" :size="19" />
            <Moon v-else :size="19" />
          </button>
          <div class="user-menu-wrap">
            <button type="button" class="user-trigger" @click="userMenuOpen = !userMenuOpen">
              <span class="avatar">{{ auth.state.user?.nickname.slice(0, 1) }}</span>
              <span class="user-copy">
                <strong>{{ auth.state.user?.nickname }}</strong>
                <small>{{ auth.state.user?.role_name }}</small>
              </span>
              <ChevronDown :size="15" />
            </button>
            <Transition name="menu-pop">
              <div v-if="userMenuOpen" class="user-dropdown">
                <button type="button" @click="userMenuOpen = false; router.push('/profile')">
                  <Settings :size="17" />个人设置
                </button>
                <button type="button" class="danger-text" @click="logout">
                  <LogOut :size="17" />退出登录
                </button>
              </div>
            </Transition>
          </div>
        </div>
      </header>
      <RouteTabs />
      <main class="page-content"><RouterView /></main>
    </section>
    <ThemePanel :open="themeOpen" @close="themeOpen = false" />
  </div>
</template>
