import { joinApiUrl } from './api-url'

export const API_PREFIX = (import.meta.env.VITE_API_BASE_URL || '/api/v1').replace(/\/$/, '')

export function apiUrl(path) {
  return joinApiUrl(API_PREFIX, path)
}

export class ApiError extends Error {
  constructor(message, { status = 0, code = 'NETWORK_ERROR', fieldErrors = null, details = null } = {}) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.fieldErrors = fieldErrors
    this.details = details
  }
}

export async function apiRequest(path, options = {}) {
  const { includeEnvelope = false, ...fetchOptions } = options
  const headers = new Headers(options.headers || {})
  if (options.body !== undefined && !(options.body instanceof FormData)) {
    headers.set('Content-Type', 'application/json')
  }
  let response
  try {
    response = await fetch(apiUrl(path), {
      ...fetchOptions,
      headers,
      credentials: 'include',
      body:
        options.body === undefined || options.body instanceof FormData
          ? options.body
          : JSON.stringify(options.body),
    })
  } catch (error) {
    throw new ApiError('无法连接服务器，请检查网络或稍后重试', {
      details: { cause: error instanceof Error ? error.message : String(error) },
    })
  }
  if (response.status === 204) return null
  let payload = null
  try {
    payload = await response.json()
  } catch {
    throw new ApiError('服务返回了无法识别的响应', { status: response.status })
  }
  if (!response.ok) {
    const error = payload?.error || {}
    throw new ApiError(error.message || '请求失败，请稍后重试', {
      status: response.status,
      code: error.code,
      fieldErrors: error.fieldErrors,
      details: error.details,
    })
  }
  return includeEnvelope ? payload : payload?.data
}

export async function apiRawRequest(path, options = {}) {
  const headers = new Headers(options.headers || {})
  let response
  try {
    response = await fetch(apiUrl(path), { ...options, headers, credentials: 'include' })
  } catch (error) {
    throw new ApiError('无法连接服务器，请检查网络或稍后重试', {
      details: { cause: error instanceof Error ? error.message : String(error) },
    })
  }
  if (!response.ok) {
    let payload = null
    try {
      payload = await response.clone().json()
    } catch {
      // Binary and streaming responses may not contain JSON errors.
    }
    const error = payload?.error || {}
    throw new ApiError(error.message || '请求失败，请稍后重试', {
      status: response.status,
      code: error.code,
      fieldErrors: error.fieldErrors,
      details: error.details,
    })
  }
  return response
}
