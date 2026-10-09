import { computed, readonly, reactive } from 'vue'
import { apiRawRequest, apiRequest } from '../services/api'

const state = reactive({
  accessToken: null,
  user: null,
  bootstrap: null,
  bootstrapLoaded: false,
  sessionChecked: false,
})

let bootstrapPromise = null
let refreshPromise = null

export const isAuthenticated = computed(() => Boolean(state.accessToken && state.user))
export const canAccessAdmin = computed(() => Boolean(state.user?.canAccessAdmin))

export async function loadBootstrap({ force = false } = {}) {
  if (state.bootstrapLoaded && !force) return state.bootstrap
  if (!bootstrapPromise || force) {
    bootstrapPromise = apiRequest('/system/bootstrap')
      .then((bootstrap) => {
        state.bootstrap = bootstrap
        state.bootstrapLoaded = true
        return bootstrap
      })
      .finally(() => {
        bootstrapPromise = null
      })
  }
  return bootstrapPromise
}

export async function getCaptcha() {
  return apiRequest('/auth/captcha')
}

export async function login(credentials) {
  const result = await apiRequest('/auth/login', { method: 'POST', body: credentials })
  state.accessToken = result.accessToken
  state.user = result.user
  return result.user
}

export async function completeSsoLogin() {
  return refreshSession()
}

async function refreshSession() {
  if (!refreshPromise) {
    refreshPromise = apiRequest('/auth/refresh', { method: 'POST' })
      .then((result) => {
        state.accessToken = result.accessToken
        state.user = result.user
        return result.user
      })
      .finally(() => {
        refreshPromise = null
      })
  }
  return refreshPromise
}

export async function restoreSession() {
  if (state.sessionChecked) return state.user
  try {
    return await refreshSession()
  } catch {
    clearAuth()
    return null
  } finally {
    state.sessionChecked = true
  }
}

export async function fetchCurrentUser() {
  if (!state.accessToken) return null
  state.user = await authenticatedRequest('/auth/me')
  return state.user
}

export async function authenticatedRequest(path, options = {}) {
  if (!state.accessToken) throw new Error('AUTH_REQUIRED')
  try {
    return await apiRequest(path, {
      ...options,
      headers: authorizationHeaders(options.headers),
    })
  } catch (error) {
    if (error.status !== 401) throw error
    try {
      await refreshSession()
      return apiRequest(path, {
        ...options,
        headers: authorizationHeaders(options.headers),
      })
    } catch (refreshError) {
      clearAuth()
      throw refreshError
    }
  }
}

export async function authenticatedRawRequest(path, options = {}) {
  if (!state.accessToken) throw new Error('AUTH_REQUIRED')
  try {
    return await apiRawRequest(path, { ...options, headers: authorizationHeaders(options.headers) })
  } catch (error) {
    if (error.status !== 401) throw error
    try {
      await refreshSession()
      return apiRawRequest(path, { ...options, headers: authorizationHeaders(options.headers) })
    } catch (refreshError) {
      clearAuth()
      throw refreshError
    }
  }
}

function authorizationHeaders(existing) {
  const headers = new Headers(existing || {})
  headers.set('Authorization', `Bearer ${state.accessToken}`)
  return headers
}

export async function logout() {
  if (state.accessToken) {
    try {
      await apiRequest('/auth/logout', {
        method: 'POST',
        headers: { Authorization: `Bearer ${state.accessToken}` },
      })
    } finally {
      clearAuth()
    }
  } else {
    clearAuth()
  }
}

export function clearAuth() {
  state.accessToken = null
  state.user = null
}

export function useAuth() {
  return {
    state: readonly(state),
    isAuthenticated,
    canAccessAdmin,
  }
}
