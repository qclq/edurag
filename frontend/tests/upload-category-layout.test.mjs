import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const uploadView = readFileSync(new URL('../src/views/UploadView.vue', import.meta.url), 'utf8')

test('business categories form one clipped 2x2 rectangle', () => {
  const groupRule = uploadView.match(/\.category-radios\{([^}]*)\}/)?.[1] || ''
  const tileRule = uploadView.match(/\.category-radios\s+:deep\(\.el-radio-button__inner\)\{([^}]*)\}/)?.[1] || ''

  assert.match(groupRule, /grid-template-columns:repeat\(2,minmax\(0,1fr\)\)/)
  assert.match(groupRule, /overflow:hidden/)
  assert.match(groupRule, /border:1px solid var\(--el-border-color\)/)
  assert.match(groupRule, /border-radius:\d+px/)
  assert.match(tileRule, /border:0/)
  assert.match(tileRule, /border-radius:0!important/)
  assert.match(tileRule, /box-shadow:none!important/)
  assert.match(uploadView, /\.el-radio-button:nth-child\(-n\+2\).*border-bottom:1px solid var\(--el-border-color\)/s)
  assert.match(uploadView, /\.el-radio-button:nth-child\(odd\).*border-right:1px solid var\(--el-border-color\)/s)
})

test('upload form omits publishing department and effective date', () => {
  assert.doesNotMatch(uploadView, /责任发布部门|生效日期/)
  assert.doesNotMatch(uploadView, /departments|publishDepartmentId|effectiveAt/)
})
