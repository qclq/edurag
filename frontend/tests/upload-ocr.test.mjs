import assert from 'node:assert/strict'
import test from 'node:test'

import { shouldEnableUploadOcr } from '../src/utils/upload-ocr.js'

test('OCR is off before a potentially visual document is selected', () => {
  assert.equal(shouldEnableUploadOcr([]), false)
  assert.equal(shouldEnableUploadOcr([{ extension: 'txt' }, { extension: 'md' }, { extension: 'csv' }]), false)
})

test('OCR turns on for PDF, Word, or PowerPoint documents', () => {
  for (const extension of ['pdf', 'doc', 'docx', 'docm', 'rtf', 'odt', 'ppt', 'pptx', 'pptm', 'odp']) {
    assert.equal(shouldEnableUploadOcr([{ extension }]), true, extension)
  }
})

test('OCR matching is case-insensitive and accepts file names', () => {
  assert.equal(shouldEnableUploadOcr([{ name: '扫描件.PDF' }]), true)
  assert.equal(shouldEnableUploadOcr([{ name: '名单.XLSX' }]), false)
})
