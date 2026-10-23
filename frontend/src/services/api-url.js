export function joinApiUrl(apiPrefix, path) {
  const prefix = String(apiPrefix || '').replace(/\/$/, '')
  const requestedPath = String(path || '')

  if (/^https?:\/\//i.test(requestedPath)) return requestedPath

  const normalizedPath = requestedPath.startsWith('/') ? requestedPath : `/${requestedPath}`
  if (/^https?:\/\//i.test(prefix)) {
    const prefixUrl = new URL(prefix)
    const prefixPath = prefixUrl.pathname.replace(/\/$/, '')
    if (normalizedPath === prefixPath || normalizedPath.startsWith(`${prefixPath}/`)) {
      return `${prefixUrl.origin}${normalizedPath}`
    }
    return `${prefix}${normalizedPath}`
  }

  if (normalizedPath === prefix || normalizedPath.startsWith(`${prefix}/`)) return normalizedPath
  return `${prefix}${normalizedPath}`
}
