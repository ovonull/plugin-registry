/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string
  readonly VITE_API_TARGET?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}

interface Window {
  initGeetest?: (config: Record<string, unknown>, callback: (captcha: GeetestObject) => void) => void
}

interface GeetestObject {
  onReady: (callback: () => void) => void
  onSuccess: (callback: () => void) => void
  onError: (callback: () => void) => void
  onClose: (callback: () => void) => void
  getValidate: () => {
    geetest_challenge: string
    geetest_validate: string
    geetest_seccode: string
  } | null
  verify: () => void
  reset: () => void
}
