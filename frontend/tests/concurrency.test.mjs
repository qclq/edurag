import assert from 'node:assert/strict'
import test from 'node:test'

import { mapSettledWithConcurrency } from '../src/utils/concurrency.js'

test('maps every item while limiting concurrent work', async () => {
  let active = 0
  let peakActive = 0
  const release = []
  const started = []

  const work = mapSettledWithConcurrency([1, 2, 3, 4, 5], 3, async value => {
    active += 1
    peakActive = Math.max(peakActive, active)
    started.push(value)
    await new Promise(resolve => release.push(resolve))
    active -= 1
    return value * 2
  })

  await new Promise(resolve => setImmediate(resolve))
  assert.deepEqual(started, [1, 2, 3])
  assert.equal(peakActive, 3)

  while (release.length) {
    release.shift()()
    await new Promise(resolve => setImmediate(resolve))
  }

  assert.deepEqual(await work, [
    { status: 'fulfilled', value: 2 },
    { status: 'fulfilled', value: 4 },
    { status: 'fulfilled', value: 6 },
    { status: 'fulfilled', value: 8 },
    { status: 'fulfilled', value: 10 },
  ])
})

test('waits for remaining work and reports individual failures', async () => {
  const visited = []
  const results = await mapSettledWithConcurrency(['a', 'b', 'c'], 2, async value => {
    visited.push(value)
    if (value === 'b') throw new Error('upload failed')
    return value.toUpperCase()
  })

  assert.deepEqual(visited.sort(), ['a', 'b', 'c'])
  assert.equal(results[0].value, 'A')
  assert.equal(results[1].status, 'rejected')
  assert.match(results[1].reason.message, /upload failed/)
  assert.equal(results[2].value, 'C')
})
