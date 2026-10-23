const OCR_DOCUMENT_EXTENSIONS = new Set([
  'pdf',
  'doc', 'docx', 'docm', 'rtf', 'odt',
  'ppt', 'pptx', 'pptm', 'odp',
])

const fileExtension = file => {
  if (file?.extension) return String(file.extension).toLowerCase()
  return String(file?.name || '').split('.').pop()?.toLowerCase() || ''
}

export const shouldEnableUploadOcr = files => files.some(file => OCR_DOCUMENT_EXTENSIONS.has(fileExtension(file)))
