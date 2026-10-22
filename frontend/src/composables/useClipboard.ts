import { useToast } from './useToast'

/**
 * 复制文本到剪贴板。
 *
 * clipboard API 只在安全上下文（https / localhost）下可用，
 * 后台常以 http + 局域网 IP 访问，此时必须退回 execCommand，
 * 否则复制按钮点了没任何反应，用户只能手动选中。
 */
export function useClipboard() {
  const toast = useToast()

  async function copyText(text: string, successText = '已复制'): Promise<void> {
    if (!text) return
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text)
        toast.show(successText, 'success')
        return
      }
    } catch {
      /* 继续走下面的兜底 */
    }
    const area = document.createElement('textarea')
    area.value = text
    area.style.position = 'fixed'
    area.style.opacity = '0'
    document.body.appendChild(area)
    area.select()
    const copied = document.execCommand('copy')
    document.body.removeChild(area)
    toast.show(copied ? successText : '复制失败，请手动选中复制', copied ? 'success' : 'error')
  }

  return { copyText }
}
