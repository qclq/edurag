import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'

const source = fs.readFileSync(new URL('../src/views/UploadView.vue', import.meta.url), 'utf8')

test('task center deletes only terminal task records after confirmation', () => {
  assert.match(source, /ElMessageBox\.confirm/)
  assert.match(source, /authenticatedRequest\(`\/admin\/tasks\/\$\{task\.id\}`,[\s\S]*method: 'DELETE'/)
  assert.match(source, /\['succeeded','failed','cancelled','partial_success'\]\.includes\(row\.status\)/)
  assert.match(source, /不会删除已经入库的文档/)
})
