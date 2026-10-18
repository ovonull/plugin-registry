import { computed, reactive } from 'vue'
import { authApi } from '../api'
import type { UserInfo } from '../types'

const state = reactive<{
  token: string
  user: UserInfo | null
  initialized: boolean
}>({
  token: localStorage.getItem('napcat_service_token') || '',
  user: null,
  initialized: false,
})

export function useAuth() {
  const isLoggedIn = computed(() => Boolean(state.token && state.user))

  async function initialize(): Promise<boolean> {
    if (state.initialized) return isLoggedIn.value
    if (!state.token) {
      state.initialized = true
      return false
    }
    const result = await authApi.me()
    if (result.success) {
      state.user = result.data
    } else {
      clearSession()
    }
    state.initialized = true
    return isLoggedIn.value
  }

  function setSession(token: string, user: UserInfo) {
    state.token = token
    state.user = user
    state.initialized = true
    localStorage.setItem('napcat_service_token', token)
  }

  function updateUser(user: UserInfo) {
    state.user = user
  }

  function clearSession() {
    state.token = ''
    state.user = null
    state.initialized = true
    localStorage.removeItem('napcat_service_token')
  }

  return { state, isLoggedIn, initialize, setSession, updateUser, clearSession }
}

