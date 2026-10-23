export async function mapSettledWithConcurrency(items, concurrency, mapper) {
  if (!Array.isArray(items)) throw new TypeError('items must be an array')
  if (typeof mapper !== 'function') throw new TypeError('mapper must be a function')
  if (items.length === 0) return []

  const workerCount = Math.min(items.length, Math.max(1, Math.trunc(Number(concurrency)) || 1))
  const results = new Array(items.length)
  let nextIndex = 0

  const worker = async () => {
    while (nextIndex < items.length) {
      const index = nextIndex
      nextIndex += 1
      try {
        results[index] = { status: 'fulfilled', value: await mapper(items[index], index) }
      } catch (reason) {
        results[index] = { status: 'rejected', reason }
      }
    }
  }

  await Promise.all(Array.from({ length: workerCount }, () => worker()))
  return results
}
