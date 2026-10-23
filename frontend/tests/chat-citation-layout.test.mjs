import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'

const source = fs.readFileSync(new URL('../src/views/ChatView.vue', import.meta.url), 'utf8')
const citationList = source.match(/<div class="source-list">([\s\S]*?)<\/template>/)?.[1] || ''

test('citation cards show the document name without a metadata row', () => {
  assert.match(citationList, /citation\.documentName/)
  assert.doesNotMatch(citationList, /<small>/)
  assert.doesNotMatch(citationList, /citation\.section|citation\.pageStart|页码未标注/)
})
