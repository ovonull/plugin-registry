import axios, { type AxiosRequestConfig } from 'axios'
import type { ApiResult } from '../types'

const http = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '/api',
  timeout: 90_000,
  headers: { 'Content-Type': 'application/json' },
})

http.interceptors.request.use((config) => {
  const token = localStorage.getItem('napcat_service_token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

function networkFailure(error: unknown): ApiResult<null> {
  if (axios.isAxiosError(error) && error.code === 'ECONNABORTED') {
    return { success: false, code: 50400, message: '请求超时，请稍后重试', data: null }
  }
  return { success: false, code: 50001, message: '无法连接服务，请检查服务状态', data: null }
}

export function request<T>(config: AxiosRequestConfig): Promise<ApiResult<T>> {
  return http
    .request<ApiResult<T>>(config)
    .then((response) => response.data)
    .catch((error: unknown) => networkFailure(error) as ApiResult<T>)
}

