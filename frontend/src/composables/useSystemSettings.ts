import { reactive } from 'vue'
import { publicApi } from '../api'
import { useTheme } from './useTheme'

const state = reactive({
  loaded: false,
  introduction: {
    name: 'NapCatQQ 插件库',
    description: 'NapCatQQ 插件分发与审核平台',
  },
  appearance: { color: 'ocean', font: 'system', effect: 'solid' },
})

let loadingTask: Promise<boolean> | null = null

export function useSystemSettings() {
  async function loadPublic(force = false): Promise<boolean> {
    if (state.loaded && !force) return true
    if (loadingTask && !force) return loadingTask
    loadingTask = publicApi.settings().then((result) => {
      if (!result.success || !result.data) return false
      Object.assign(state.introduction, result.data.introduction)
      Object.assign(state.appearance, result.data.appearance)
      useTheme().applySystemAppearance(
        state.appearance.color,
        state.appearance.font,
        state.appearance.effect,
      )
      state.loaded = true
      document.title = state.introduction.name
      return true
    }).finally(() => { loadingTask = null })
    return loadingTask
  }

  return { state, loadPublic }
}
