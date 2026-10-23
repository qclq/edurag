import test from 'node:test'
import assert from 'node:assert/strict'

import { joinApiUrl } from '../src/services/api-url.js'

test('does not duplicate a relative API prefix from a server-provided URL', () => {
  assert.equal(
    joinApiUrl('/api/v1', '/api/v1/documents/123/download'),
    '/api/v1/documents/123/download',
  )
})

test('adds the API prefix to application-relative paths', () => {
  assert.equal(
    joinApiUrl('/api/v1', '/documents/123/download'),
    '/api/v1/documents/123/download',
  )
})

test('keeps the configured origin when an absolute API prefix is used', () => {
  assert.equal(
    joinApiUrl('https://api.example.edu/api/v1', '/api/v1/documents/123/download'),
    'https://api.example.edu/api/v1/documents/123/download',
  )
})
