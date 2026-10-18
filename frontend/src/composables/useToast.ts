import { reactive } from 'vue'

export type ToastKind = 'success' | 'error' | 'warning' | 'info'

export interface ToastItem {
  id: number
  kind: ToastKind
  message: string
}

const toasts = reactive<ToastItem[]>([])
let nextId = 1

export function useToast() {
  function show(message: string, kind: ToastKind = 'info') {
    const id = nextId++
    toasts.push({ id, kind, message })
    window.setTimeout(() => remove(id), 3600)
  }

  function remove(id: number) {
    const index = toasts.findIndex((item) => item.id === id)
    if (index >= 0) toasts.splice(index, 1)
  }

  return { toasts, show, remove }
}

