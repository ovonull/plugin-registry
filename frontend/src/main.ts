import { createApp } from 'vue'
import App from './App.vue'
import router from './router'
import { useTheme } from './composables/useTheme'
import './styles/globals.css'
import './styles/layout.css'
import './styles/components.css'
import './styles/pages.css'

useTheme().applyTheme()

createApp(App).use(router).mount('#app')

