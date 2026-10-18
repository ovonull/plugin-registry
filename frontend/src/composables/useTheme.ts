import { reactive } from 'vue'

export const themeColors = [
  { code: 'ocean', name: '海洋蓝', value: '#2563eb', soft: '#dbeafe' },
  { code: 'emerald', name: '翡翠绿', value: '#059669', soft: '#d1fae5' },
  { code: 'violet', name: '雾感紫', value: '#7c3aed', soft: '#ede9fe' },
  { code: 'indigo', name: '深海靛蓝', value: '#4338ca', soft: '#e0e7ff' },
  { code: 'amber', name: '琥珀金', value: '#d97706', soft: '#fef3c7' },
  { code: 'sunset', name: '落日橙', value: '#ea580c', soft: '#ffedd5' },
  { code: 'aurora', name: '极光青', value: '#0891b2', soft: '#cffafe' },
  { code: 'rose', name: '霓虹粉', value: '#db2777', soft: '#fce7f3' },
  { code: 'ruby', name: '宝石红', value: '#dc2626', soft: '#fee2e2' },
]

export const themeFonts = [
  { name: '系统默认', value: 'system' },
  { name: '微软雅黑', value: 'yahei' },
  { name: '现代黑体', value: 'heiti' },
  { name: '雅致宋体', value: 'songti' },
  { name: '经典楷体', value: 'kaiti' },
  { name: '文雅仿宋', value: 'fangsong' },
  { name: '行楷', value: 'xingkai' },
  { name: '圆润', value: 'rounded' },
  { name: '等宽', value: 'mono' },
]

const state = reactive({
  dark: localStorage.getItem('theme_dark') === 'true',
  color: localStorage.getItem('theme_color') || themeColors[0].value,
  soft: localStorage.getItem('theme_soft') || themeColors[0].soft,
  effect: localStorage.getItem('theme_effect') || 'solid',
  font: localStorage.getItem('theme_font') || 'system',
})

function applyTheme() {
  const root = document.documentElement
  root.classList.toggle('dark', state.dark)
  root.dataset.effect = state.effect
  root.dataset.font = state.font
  root.style.setProperty('--primary', state.color)
  root.style.setProperty('--primary-soft', state.soft)
  localStorage.setItem('theme_dark', String(state.dark))
  localStorage.setItem('theme_color', state.color)
  localStorage.setItem('theme_soft', state.soft)
  localStorage.setItem('theme_effect', state.effect)
  localStorage.setItem('theme_font', state.font)
}

export function useTheme() {
  function toggleDark() {
    state.dark = !state.dark
    applyTheme()
  }

  function setColor(value: string, soft: string) {
    state.color = value
    state.soft = soft
    applyTheme()
  }

  function setEffect(value: string) {
    state.effect = value
    applyTheme()
  }

  function setFont(value: string) {
    state.font = value
    applyTheme()
  }

  function applySystemAppearance(colorCode: string, font: string, effect: string) {
    const color = themeColors.find((item) => item.code === colorCode) || themeColors[0]
    state.color = color.value
    state.soft = color.soft
    state.font = font
    state.effect = effect
    applyTheme()
  }

  return { state, toggleDark, setColor, setEffect, setFont, applySystemAppearance, applyTheme }
}
