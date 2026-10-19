import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import AppLayout from '../layouts/AppLayout.vue'
import { useAuth } from '../composables/useAuth'

export const appRoutes: RouteRecordRaw[] = [
  { path: '/dashboard', name: 'dashboard', component: () => import('../pages/DashboardPage.vue'), meta: { title: '平台概览', menu: true, icon: 'dashboard' } },
  { path: '/plugins', name: 'plugins', component: () => import('../pages/PluginMarketPage.vue'), meta: { title: '插件市场', menu: true, icon: 'package' } },
  { path: '/plugins/installed', name: 'plugin-installed', component: () => import('../pages/PluginInstalledPage.vue'), meta: { title: '我的插件', menu: true, icon: 'plug' } },
  { path: '/plugin-review', name: 'plugin-review', component: () => import('../pages/PluginReviewPage.vue'), meta: { title: '插件审核', menu: true, icon: 'shield-check', admin: true } },
  { path: '/profile', name: 'profile', component: () => import('../pages/ProfilePage.vue'), meta: { title: '个人设置', menu: true, icon: 'user' } },
  { path: '/settings', name: 'settings', component: () => import('../pages/SystemSettingsPage.vue'), meta: { title: '系统设置', menu: true, icon: 'settings', admin: true } },
]

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/login',
      name: 'login',
      component: () => import('../pages/LoginPage.vue'),
      meta: { title: '登录', public: true },
    },
    {
      path: '/register',
      name: 'register',
      component: () => import('../pages/RegisterPage.vue'),
      meta: { title: '注册', public: true },
    },
    {
      // 公开插件展示首页：不需要登录，独立布局，已登录用户可从右上角进控制台。
      path: '/',
      name: 'home',
      component: () => import('../pages/HomePage.vue'),
      meta: { title: '插件市场', public: true },
    },
    {
      // 后台容器：子路由都是绝对路径，这里的前缀只作为控制台入口使用。
      path: '/console',
      component: AppLayout,
      redirect: '/dashboard',
      children: appRoutes,
    },
    { path: '/:pathMatch(.*)*', redirect: '/dashboard' },
  ],
})

router.beforeEach(async (to) => {
  const auth = useAuth()
  const loggedIn = await auth.initialize()
  if (!to.meta.public && !loggedIn) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
  if (to.meta.admin && auth.state.user?.role_code !== 'admin') return { name: 'dashboard' }
  if ((to.name === 'login' || to.name === 'register') && loggedIn) return { name: 'dashboard' }
  document.title = `${String(to.meta.title || '插件库')} - NapCatQQ 插件库`
  return true
})

export default router
